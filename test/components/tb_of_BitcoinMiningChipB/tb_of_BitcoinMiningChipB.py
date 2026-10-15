import os
import sys as _sys

import py4hw
from minuscul_crypto_miner.architecture.bitcoinMining.BitcoinMiningChipB import BitcoinMiningChipB
from minuscul_crypto_miner.architecture.bitcoinMining.bitcoinminingchip import protocol as P
from minuscul_crypto_miner.algorithms.sha256 import bitcoin_double_sha256

# Same host-side UART model as chip A's testbench
_sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'tb_of_BitcoinMiningChip'))
from bitcoin_mining_chip_test_doubles import HostUart, make_header, meets_target, EASY_NBITS, HARD_NBITS


# Architecture under test (only the UART pins are touched):
#   host UART model -> uart_rx -> BitcoinMiningChipB -> uart_tx -> host UART model
#
# Same protocol checks as tb_of_BitcoinMiningChip.py (chip B keeps chip A's
# USART protocol, registers and Control); only the cores differ (fully
# pipelined, one double-hash per cycle per core). HostUart serialises/
# deserialises the 8N1 line itself, independently of the chip's UsartRx/UsartTx.


NUM_CORES = 4
CORES_PER_ARBITER = 2     # -> 2 first-level arbiters + top arbiter
CLKS_PER_BIT = 4
ROUNDS_PER_STAGE = 4      # 1 2 4 rounds per pipeline stage (127 / 64 / 33 stages)
BITCOIN_BYTE_ORDER = True # chip B always works in Bitcoin byte order


def header_for(seed, nbits):
    return make_header(seed, nbits, bitcoin_byte_order=BITCOIN_BYTE_ORDER)


sys = py4hw.HWSystem()

reset = sys.wire('reset',1)
py4hw.Constant(sys,'reset',0,reset)
host_to_chip = sys.wire('host_to_chip',1)
chip_to_host = sys.wire('chip_to_host',1)

host = HostUart(sys,'host',host_to_chip,chip_to_host,CLKS_PER_BIT)
chip = BitcoinMiningChipB(sys,'chip',reset,host_to_chip,chip_to_host,
                          num_cores=NUM_CORES,cores_per_arbiter=CORES_PER_ARBITER,clks_per_bit=CLKS_PER_BIT,
                          rounds_per_stage=ROUNDS_PER_STAGE,debug=False)

sim = sys.getSimulator()
total_cycles = 0
failures = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        failures.append(msg)


def transact(frame, n_replies, max_cycles=20_000):
    # Send one frame, run until n_replies new frames came back (or timeout).
    global total_cycles
    first = len(host.frames)
    host.send(frame)
    cycles = 0
    while len(host.frames) < first + n_replies and cycles < max_cycles:
        sim.clk(1)
        cycles += 1
    if n_replies == 0:
        # nothing expected: let the frame fully go out plus some margin
        extra = len(host.tx_levels) + 50 * CLKS_PER_BIT
        sim.clk(extra)
        cycles += extra
    total_cycles += cycles
    replies = host.frames[first:]
    for cmd, payload, chk_ok in replies:
        if not chk_ok:
            failures.append(f"bad checksum on reply cmd={cmd:#04x}")
    return [(cmd, payload) for cmd, payload, _ in replies]


def read_reg(addr):
    replies = transact(P.build_frame(P.CMD_READ_REG,[addr]),1)
    if replies and replies[0][0] == P.RSP_REG_VALUE:
        return int.from_bytes(replies[0][1][1:5],'big')
    return None


def write_reg(addr, value):
    return transact(P.build_frame(P.CMD_WRITE_REG,[addr] + list(value.to_bytes(4,'big'))),1)


def job_payload(header):
    return header[:76]


def run_job(header, label, min_nonce=0):
    replies = transact(P.build_frame(P.CMD_NEW_JOB,job_payload(header)),2,max_cycles=200_000)
    check(len(replies) == 2 and replies[0] == (P.RSP_JOB_ACK,b''), f"{label}: JOB_ACK")
    if len(replies) < 2 or replies[1][0] != P.RSP_RESULT:
        check(False, f"{label}: RESULT received (got {replies})")
        return
    payload = replies[1][1]
    nonce = int.from_bytes(payload[0:4],'big')
    digest_bytes = payload[4:36]
    digest = int.from_bytes(digest_bytes,'big')
    # RESULT carries the nonce as the raw header bytes 76..79
    expected = int.from_bytes(bitcoin_double_sha256(header[:76] + payload[0:4]),'big')
    nbits = int.from_bytes(header[72:76],'little' if BITCOIN_BYTE_ORDER else 'big')
    print(f"  {label}: nonce={nonce:#010x} digest={digest:064x}")
    check(digest == expected, f"{label}: digest matches double-SHA256 reference")
    check(meets_target(digest_bytes, nbits, BITCOIN_BYTE_ORDER), f"{label}: digest meets target"
          + (" (little-endian, as Bitcoin)" if BITCOIN_BYTE_ORDER else ""))
    check(nonce >= min_nonce, f"{label}: nonce {nonce} inside the configured range (>= {min_nonce})")
    check(read_reg(P.REG_STATUS) == P.STATUS_IDLE, f"{label}: STATUS idle after RESULT")


print("--- liveness / registers")
check(transact(P.build_frame(P.CMD_PING),1) == [(P.RSP_PONG,b'')], "PING -> PONG")
check(read_reg(P.REG_CHIP_ID) == P.CHIP_ID, "CHIP_ID")
check(read_reg(P.REG_NUM_CORES) == NUM_CORES, "NUM_CORES")
check(read_reg(P.REG_STATUS) == P.STATUS_IDLE, "STATUS idle at boot")
check(read_reg(P.REG_ACTIVE_CORES_MASK) == (1 << NUM_CORES) - 1, "ACTIVE_CORES_MASK defaults to all cores")
check(transact(P.build_frame(P.CMD_READ_REG,[0x40]),1) == [(P.RSP_REG_NACK,bytes([0x40]))], "READ_REG out of range -> REG_NACK")
check(write_reg(P.REG_CHIP_ID,5) == [(P.RSP_REG_NACK,bytes([P.REG_CHIP_ID]))], "WRITE_REG read-only -> REG_NACK")

print("--- framing robustness")
bad = bytearray(P.build_frame(P.CMD_PING)); bad[-1] ^= 0xFF
check(transact(bytes(bad),0) == [], "bad checksum -> silently dropped")
check(transact(b'\x00\x13' + P.build_frame(P.CMD_PING),1) == [(P.RSP_PONG,b'')], "garbage before SOF skipped")
check(transact(P.build_frame(P.CMD_NEW_JOB,bytes(10)),1) == [(P.RSP_JOB_NACK,bytes([P.NACK_MALFORMED]))], "short NEW_JOB -> JOB_NACK malformed")

print("--- job A: all cores")
run_job(header_for(7,EASY_NBITS), "job A")

print("--- job B: cores 1,2 only, base offset 5000")
check(write_reg(P.REG_ACTIVE_CORES_MASK,0b0110) == [(P.RSP_WRITE_ACK,bytes([P.REG_ACTIVE_CORES_MASK]))], "WRITE ACTIVE_CORES_MASK")
check(write_reg(P.REG_CORE_BASE_OFFSET,5000) == [(P.RSP_WRITE_ACK,bytes([P.REG_CORE_BASE_OFFSET]))], "WRITE CORE_BASE_OFFSET")
run_job(header_for(11,EASY_NBITS), "job B", min_nonce=5000)

print("--- busy behaviour + ABORT_JOB")
check(transact(P.build_frame(P.CMD_NEW_JOB,job_payload(header_for(13,HARD_NBITS))),1) == [(P.RSP_JOB_ACK,b'')], "hard job -> JOB_ACK")
check(read_reg(P.REG_STATUS) == P.STATUS_BUSY, "STATUS busy while mining")
check(transact(P.build_frame(P.CMD_PING),1) == [(P.RSP_PONG,b'')], "PING answered while mining")
check(transact(P.build_frame(P.CMD_NEW_JOB,job_payload(header_for(17,EASY_NBITS))),1) == [(P.RSP_JOB_NACK,bytes([P.NACK_BUSY]))], "NEW_JOB while busy -> JOB_NACK busy")
check(write_reg(P.REG_JOB_TIMEOUT,1) == [(P.RSP_REG_NACK,bytes([P.REG_JOB_TIMEOUT]))], "WRITE_REG while busy -> REG_NACK")
check(transact(P.build_frame(P.CMD_ABORT_JOB),0) == [], "ABORT_JOB sends no reply")
check(read_reg(P.REG_STATUS) == P.STATUS_IDLE, "STATUS idle after ABORT_JOB")

print("--- JOB_TIMEOUT")
check(write_reg(P.REG_JOB_TIMEOUT,1500) == [(P.RSP_WRITE_ACK,bytes([P.REG_JOB_TIMEOUT]))], "WRITE JOB_TIMEOUT")
replies = transact(P.build_frame(P.CMD_NEW_JOB,job_payload(header_for(19,HARD_NBITS))),2,max_cycles=50_000)
check(replies == [(P.RSP_JOB_ACK,b''),(P.RSP_JOB_NACK,bytes([P.NACK_TIMEOUT]))], "hard job -> JOB_ACK then JOB_NACK timeout")
check(read_reg(P.REG_STATUS) == P.STATUS_ERROR, "STATUS error after timeout")

print("--- no active cores")
check(write_reg(P.REG_ACTIVE_CORES_MASK,0) == [(P.RSP_WRITE_ACK,bytes([P.REG_ACTIVE_CORES_MASK]))], "WRITE ACTIVE_CORES_MASK=0")
check(transact(P.build_frame(P.CMD_NEW_JOB,job_payload(header_for(23,EASY_NBITS))),1) == [(P.RSP_JOB_NACK,bytes([P.NACK_NO_CORES]))], "NEW_JOB -> JOB_NACK no cores")

print("--- job C: back to defaults after abort/timeout")
write_reg(P.REG_ACTIVE_CORES_MASK,(1 << NUM_CORES) - 1)
write_reg(P.REG_CORE_BASE_OFFSET,0)
write_reg(P.REG_JOB_TIMEOUT,0)
run_job(header_for(29,EASY_NBITS), "job C")

cores = [chip.children[f'core_{i}'] for i in range(NUM_CORES)]
print(f"cores={NUM_CORES} cores_per_arbiter={CORES_PER_ARBITER} clks_per_bit={CLKS_PER_BIT} "
      f"rounds_per_stage={ROUNDS_PER_STAGE} cycles={total_cycles}")
print(f"double-hashes={sum(c.hashes for c in cores)} hits={sum(c.hit_count for c in cores)} "
      f"dropped_hits={sum(c.dropped_hits for c in cores)} (drops are harmless: one RESULT per job)")
if failures:
    print(f"Test Failed ({len(failures)} failures)")
else:
    print("Test Passed")
