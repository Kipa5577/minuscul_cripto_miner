import py4hw
from minuscul_crypto_miner.architecture.bitcoinMining import BitcoinMinerEngine
from minuscul_crypto_miner.algorithms.sha256 import bitcoin_double_sha256


# Architecture under test:
#   Sha256Engine (shared midstate) + N x [bitcoin_Nonce_iterator -> continuation
#   core -> DigestBridge -> Sha256CoreV1 finisher] -> ResultBus -> DifficultyValidator


NUM_LANES = 29
NUM_RESULTS_TO_COLLECT = 30
MAX_CYCLES = 10_000_000

# Fixed 80-byte test header. Not a real Bitcoin header -- correctness here
# only requires the header split fed to hardware and the split fed to the
# Python double-SHA256 reference to agree, which header_for_nonce() below
# guarantees by construction.
HEADER_TEMPLATE = bytes((i*7+3) % 256 for i in range(80))
HEADER_FIRST64 = HEADER_TEMPLATE[:64]
MERKLE_TAIL = HEADER_TEMPLATE[64:68]
TIME_FIELD = HEADER_TEMPLATE[68:72]
BITS_FIELD = HEADER_TEMPLATE[72:76]

# Compact difficulty with the same leniency as the multi-core benchmark's
# TARGET=(1<<256)//16 (mantissa=0x100000, exponent=0x20 -> target=2**252),
# so this test finds a few passing digests quickly.
NBITS = 0x20100000
TARGET = (NBITS & 0xFFFFFF) << (8*((NBITS>>24)-3))


def header_for_nonce(nonce: int) -> bytes:
    return HEADER_FIRST64 + MERKLE_TAIL + TIME_FIELD + BITS_FIELD + nonce.to_bytes(4,'big')


class OneShotPulse(py4hw.Logic):
    # Drives out=1 for exactly the first clock cycle, 0 thereafter --
    # Sha256Engine expects new_job as a pulse, not a held-high level (a
    # held-high new_job would make it reload forever, see Sha256Engine).
    def __init__(self,parent,name,out):
        super().__init__(parent,name)
        self.out = self.addOut('out',out)
        self.fired = False

    def clock(self):
        self.out.prepare(0 if self.fired else 1)
        self.fired = True


class FoundBlockCollector(py4hw.Logic):
    def __init__(self,parent,name,result_ready,nonce_out,nbbytes_out,digest_out,result_fetched):
        super().__init__(parent,name)
        self.result_ready = self.addIn('result_ready',result_ready)
        self.nonce_out = self.addIn('nonce_out',nonce_out)
        self.nbbytes_out = self.addIn('nbbytes_out',nbbytes_out)
        self.digest_out = self.addIn('digest_out',digest_out)
        self.result_fetched = self.addOut('result_fetched',result_fetched)
        self.captured = []
        self.pending_ack = False

    def clock(self):
        fetched = 0
        if self.result_ready.get() == 1 and not self.pending_ack:
            self.captured.append((self.nonce_out.get(), self.nbbytes_out.get(), self.digest_out.get()))
            fetched = 1
            self.pending_ack = True
        elif self.result_ready.get() == 0:
            self.pending_ack = False
        self.result_fetched.prepare(fetched)


sys = py4hw.HWSystem()

reset = sys.wire('reset',1)
py4hw.Constant(sys,'reset',0,reset)

header_first64_wire = sys.wire('header_first64bytes',512)
py4hw.Constant(sys,'header_first64bytes_const',int.from_bytes(HEADER_FIRST64,'big'),header_first64_wire)

new_job = sys.wire('new_job',1)
OneShotPulse(sys,'new_job_pulse',new_job)

merkle_tail_wire = sys.wire('merkle_tail',32)
py4hw.Constant(sys,'merkle_tail_const',int.from_bytes(MERKLE_TAIL,'big'),merkle_tail_wire)
time_wire = sys.wire('time_field',32)
py4hw.Constant(sys,'time_field_const',int.from_bytes(TIME_FIELD,'big'),time_wire)
bits_wire = sys.wire('bits',32)
py4hw.Constant(sys,'bits_const',int.from_bytes(BITS_FIELD,'big'),bits_wire)

nBits_wire = sys.wire('nBits',32)
py4hw.Constant(sys,'nBits_const',NBITS,nBits_wire)

result_ready = sys.wire('result_ready',1)
found_nonce_out = sys.wire('found_nonce_out',512)
found_nbbytes_out = sys.wire('found_nbbytes_out',8)
found_digest_out = sys.wire('found_digest_out',256)
result_fetched = sys.wire('result_fetched',1)

col = FoundBlockCollector(sys,'col',result_ready,found_nonce_out,found_nbbytes_out,found_digest_out,result_fetched)

engine = BitcoinMinerEngine(sys,'miner',reset,header_first64_wire,new_job,
                            merkle_tail_wire,time_wire,bits_wire,nBits_wire,
                            result_ready,found_nonce_out,found_nbbytes_out,found_digest_out,result_fetched,
                            num_lanes=NUM_LANES,debug=False)

sim = sys.getSimulator()

cycles = 0
while len(col.captured) < NUM_RESULTS_TO_COLLECT and cycles < MAX_CYCLES:
    sim.clk(1)
    cycles += 1

passed = len(col.captured) == NUM_RESULTS_TO_COLLECT
for nonce, nbbytes, digest in col.captured:
    header = header_for_nonce(nonce)
    expected = int.from_bytes(bitcoin_double_sha256(header),'big')
    hash_correct = (digest == expected)
    meets_target = (digest <= TARGET)
    match = hash_correct and meets_target
    passed = passed and match
    print(f"nonce={nonce:#010x} nbbytes={nbbytes} digest={digest:064x} expected={expected:064x} "
          f"hash_correct={hash_correct} meets_target={meets_target}")

print(f"lanes={NUM_LANES} nBits={NBITS:#010x} target={TARGET:064x} cycles={cycles} results={len(col.captured)}")

if passed:
    print("Test Passed")
else:
    print("Test Failed")
