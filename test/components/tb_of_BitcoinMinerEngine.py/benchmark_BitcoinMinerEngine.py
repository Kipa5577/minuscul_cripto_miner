import os

import py4hw
from minuscul_crypto_miner.architecture.bitcoinMining import BitcoinMinerEngine


# Architecture under benchmark:
#   Sha256Engine (shared midstate) + NUM_LANES x [bitcoin_Nonce_iterator ->
#   continuation core -> DigestBridge -> Sha256CoreV1 finisher]
#   -> ResultBus -> DifficultyValidator
#
# Unlike tb_of_BitcoinMinerEngine.py this doesn't care about finding passing
# digests -- NBITS is left at 0 (decodes to target=0, never passes) so
# DifficultyValidator never has to wait on a downstream "found block"
# consumer, and every digest it examines just measures raw double-SHA256
# nonce-search throughput of the whole lane array.

# Benchmark variables
CLOCK_FREQUENCY_HZ = 50_000_000  # assumed target clock frequency, for translating cycles into wall-clock time
NUM_LANES = int(os.environ.get("BENCH_NUM_LANES", 30))  # number of nonce-search lanes
NUM_BENCHMARK_HASHES = int(os.environ.get("BENCH_NUM_HASHES", 500))  # total double-hashes to examine before reporting
NBITS = 0  # decodes to target=0: never passes, this run only measures throughput

# Fixed 80-byte test header -- not a real Bitcoin header, arbitrary but
# deterministic, since only throughput (not the found digests) is measured.
HEADER_TEMPLATE = bytes((i * 7 + 3) % 256 for i in range(80))
HEADER_FIRST64 = HEADER_TEMPLATE[:64]
MERKLE_TAIL = HEADER_TEMPLATE[64:68]
TIME_FIELD = HEADER_TEMPLATE[68:72]
BITS_FIELD = HEADER_TEMPLATE[72:76]


class OneShotPulse(py4hw.Logic):
    # Drives out=1 for exactly the first clock cycle, 0 thereafter --
    # Sha256Engine expects new_job as a pulse, not a held-high level.
    def __init__(self, parent, name, out):
        super().__init__(parent, name)
        self.out = self.addOut('out', out)
        self.fired = False

    def clock(self):
        self.out.prepare(0 if self.fired else 1)
        self.fired = True


sys = py4hw.HWSystem()

reset = sys.wire('reset', 1)
py4hw.Constant(sys, 'reset', 0, reset)

header_first64_wire = sys.wire('header_first64bytes', 512)
py4hw.Constant(sys, 'header_first64bytes_const', int.from_bytes(HEADER_FIRST64, 'big'), header_first64_wire)

new_job = sys.wire('new_job', 1)
OneShotPulse(sys, 'new_job_pulse', new_job)

merkle_tail_wire = sys.wire('merkle_tail', 32)
py4hw.Constant(sys, 'merkle_tail_const', int.from_bytes(MERKLE_TAIL, 'big'), merkle_tail_wire)
time_wire = sys.wire('time_field', 32)
py4hw.Constant(sys, 'time_field_const', int.from_bytes(TIME_FIELD, 'big'), time_wire)
bits_wire = sys.wire('bits', 32)
py4hw.Constant(sys, 'bits_const', int.from_bytes(BITS_FIELD, 'big'), bits_wire)

nBits_wire = sys.wire('nBits', 32)
py4hw.Constant(sys, 'nBits_const', NBITS, nBits_wire)

result_ready = sys.wire('result_ready', 1)
found_nonce_out = sys.wire('found_nonce_out', 512)
found_nbbytes_out = sys.wire('found_nbbytes_out', 8)
found_digest_out = sys.wire('found_digest_out', 256)
result_fetched = sys.wire('result_fetched', 1)
py4hw.Constant(sys, 'result_fetched', 0, result_fetched)  # NBITS=0 means state 2 is never entered, nothing to fetch

miner = BitcoinMinerEngine(sys, 'miner', reset, header_first64_wire, new_job,
                            merkle_tail_wire, time_wire, bits_wire, nBits_wire,
                            result_ready, found_nonce_out, found_nbbytes_out, found_digest_out, result_fetched,
                            num_lanes=NUM_LANES, debug=False)

# ResultBus/DifficultyValidator drive Digest_Fetched only after DifficultyValidator
# has examined a digest -- that ack is what we count as "one double-hash completed".
merged_digest_fetched = miner.children['result_bus'].Digest_Fetched

sim = sys.getSimulator()

total_clock_cycles = 0
hashes_completed = 0
midstate_ready_cycle = None
l1_busy_cycles = [0] * NUM_LANES
l2_busy_cycles = [0] * NUM_LANES
finisher_l1_busy_cycles = [0] * NUM_LANES
finisher_l2_busy_cycles = [0] * NUM_LANES

midstate_ready_wire = miner.children['midstate_engine'].children['controller'].midstate_ready

while hashes_completed < NUM_BENCHMARK_HASHES:
    sim.clk(1)
    total_clock_cycles += 1

    if midstate_ready_cycle is None and midstate_ready_wire.get() == 1:
        midstate_ready_cycle = total_clock_cycles

    if merged_digest_fetched.get() == 1:
        hashes_completed += 1

    for i in range(NUM_LANES):
        l1 = miner.children[f'L1_{i}']
        l2 = miner.children[f'L2_{i}']
        finisher = miner.children[f'finisher_{i}']
        if l1.state != 0:
            l1_busy_cycles[i] += 1
        if l2.state != 0:
            l2_busy_cycles[i] += 1
        if finisher.children['L1'].state != 0:
            finisher_l1_busy_cycles[i] += 1
        if finisher.children['L2'].state != 0:
            finisher_l2_busy_cycles[i] += 1

avg_cycles_per_hash = total_clock_cycles / hashes_completed
estimated_seconds = total_clock_cycles / CLOCK_FREQUENCY_HZ
hashes_per_second = hashes_completed / estimated_seconds

avg_l1_busy_pct = 100 * sum(l1_busy_cycles) / (NUM_LANES * total_clock_cycles)
avg_l2_busy_pct = 100 * sum(l2_busy_cycles) / (NUM_LANES * total_clock_cycles)
avg_finisher_l1_busy_pct = 100 * sum(finisher_l1_busy_cycles) / (NUM_LANES * total_clock_cycles)
avg_finisher_l2_busy_pct = 100 * sum(finisher_l2_busy_cycles) / (NUM_LANES * total_clock_cycles)

print("--- Benchmark (BitcoinMinerEngine) ---")
print(f"lanes                                = {NUM_LANES}")
print(f"double-hashes completed              = {hashes_completed}")
print(f"total clock cycles                   = {total_clock_cycles}")
print(f"midstate ready at cycle              = {midstate_ready_cycle} (one-time, amortized across every nonce)")
print(f"avg cycles / double-hash (aggregate) = {avg_cycles_per_hash:.2f}")
print(f"continuation L1_Handler avg busy      = {avg_l1_busy_pct:.1f}%")
print(f"continuation L2_Handler avg busy      = {avg_l2_busy_pct:.1f}%")
print(f"finisher L1_Handler avg busy          = {avg_finisher_l1_busy_pct:.1f}%")
print(f"finisher L2_Handler avg busy          = {avg_finisher_l2_busy_pct:.1f}%")
print(f"assumed clock frequency              = {CLOCK_FREQUENCY_HZ:,} Hz")
print(f"estimated wall-clock time            = {estimated_seconds*1e3:.3f} ms")
print(f"estimated throughput                 = {hashes_per_second:,.0f} double-hashes/s")
print(f"estimated throughput / lane           = {hashes_per_second/NUM_LANES:,.0f} double-hashes/s")
