import py4hw
from minuscul_crypto_miner.architecture.Sha256Crcuits import *
from minuscul_crypto_miner.architecture.bus.bus import Bus, BusInterface, ResultBus
from minuscul_crypto_miner.architecture.seedGenerator.seedGenerator import seedGenerator
from minuscul_crypto_miner.architecture.difficultyValidator.DifficultyValidator import DifficultyValidator
import hashlib


# Architecture under test:
#   seedGenerator -> Bus -> NUM_ENGINES x Sha256CoreV1 -> ResultBus -> DifficultyValidator


NUM_ENGINES = 4              # number of Sha256CoreV1 instances sharing the bus
NUM_RESULTS_TO_COLLECT = 5   # how many validated "blocks" to wait for before reporting
TARGET = (1 << 256) // 16    # lenient threshold (~1/16 digests pass) so this test finds some quickly
MAX_CYCLES = 2_000_000       # safety cap in case TARGET is too strict to ever be met


class FoundBlockCollector(py4hw.Logic):
    def __init__(self, parent, name, result_ready, seed_out, nbbytes_out, digest_out, result_fetched):
        super().__init__(parent, name)
        self.result_ready = self.addIn('result_ready', result_ready)
        self.seed_out = self.addIn('seed_out', seed_out)
        self.nbbytes_out = self.addIn('nbbytes_out', nbbytes_out)
        self.digest_out = self.addIn('digest_out', digest_out)
        self.result_fetched = self.addOut('result_fetched', result_fetched)
        self.captured = []
        self.pending_ack = False

    def clock(self):
        fetched = 0
        if self.result_ready.get() == 1 and not self.pending_ack:
            self.captured.append((self.seed_out.get(), self.nbbytes_out.get(), self.digest_out.get()))
            fetched = 1
            self.pending_ack = True
        elif self.result_ready.get() == 0:
            self.pending_ack = False
        self.result_fetched.prepare(fetched)


sys = py4hw.HWSystem()

reset = sys.wire('reset', 1)
py4hw.Constant(sys, 'reset', 0, reset)

# --- seedGenerator -> Bus -> NUM_ENGINES x Sha256CoreV1 ---------------------
master_if = BusInterface(sys, 'master_if')
gen = seedGenerator(sys, 'gen', reset, master_if, debug=False)

slave_ifs = [BusInterface(sys, f'slave_if_{i}') for i in range(NUM_ENGINES)]
bus = Bus(sys, 'bus', master_if, slave_ifs, debug=False)

engines = []
engine_outputs = []
for i in range(NUM_ENGINES):
    seed_out = sys.wire(f'engine_{i}_seed_out', 512)
    nbbytes_out = sys.wire(f'engine_{i}_nbbytes_out', 8)
    digest_out = sys.wire(f'engine_{i}_digest_out', 256)
    digest_ready = sys.wire(f'engine_{i}_digest_ready', 1)
    digest_fetched = sys.wire(f'engine_{i}_digest_fetched', 1)

    engine = Sha256CoreV1(sys, f'sha256_engine_{i}', reset, slave_ifs[i],
                           seed_out, nbbytes_out, digest_out, digest_ready, digest_fetched,
                           debug=False)
    engines.append(engine)
    engine_outputs.append((seed_out, nbbytes_out, digest_out, digest_ready, digest_fetched))

# --- NUM_ENGINES x Sha256CoreV1 -> ResultBus -> DifficultyValidator --------
merged_seed_out = sys.wire('merged_seed_out', 512)
merged_nbbytes_out = sys.wire('merged_nbbytes_out', 8)
merged_digest_out = sys.wire('merged_digest_out', 256)
merged_digest_ready = sys.wire('merged_digest_ready', 1)
merged_digest_fetched = sys.wire('merged_digest_fetched', 1)

result_bus = ResultBus(sys, 'result_bus', engine_outputs,
                        merged_seed_out, merged_nbbytes_out, merged_digest_out,
                        merged_digest_ready, merged_digest_fetched,
                        debug=False)

target_wire = sys.wire('target', 256)
py4hw.Constant(sys, 'target', TARGET, target_wire)

result_ready = sys.wire('result_ready', 1)
found_seed_out = sys.wire('found_seed_out', 512)
found_nbbytes_out = sys.wire('found_nbbytes_out', 8)
found_digest_out = sys.wire('found_digest_out', 256)
result_fetched = sys.wire('result_fetched', 1)

validator = DifficultyValidator(sys, 'validator', reset, target_wire,
                                 merged_digest_out, merged_digest_ready, merged_digest_fetched,
                                 merged_seed_out, merged_nbbytes_out,
                                 result_ready, found_seed_out, found_nbbytes_out, found_digest_out, result_fetched,
                                 debug=False)

col = FoundBlockCollector(sys, 'col', result_ready, found_seed_out, found_nbbytes_out, found_digest_out, result_fetched)

sim = sys.getSimulator()

cycles = 0
while len(col.captured) < NUM_RESULTS_TO_COLLECT and cycles < MAX_CYCLES:
    sim.clk(1)
    cycles += 1

passed = len(col.captured) == NUM_RESULTS_TO_COLLECT
for seed, nbbytes, digest in col.captured:
    expected = int(hashlib.sha256(seed.to_bytes(nbbytes, byteorder='big')).hexdigest(), 16)
    hash_correct = (digest == expected)
    meets_target = (digest <= TARGET)
    match = hash_correct and meets_target
    passed = passed and match
    print(f"seed={seed:#x} nbbytes={nbbytes} digest={digest:064x} expected={expected:064x} "
          f"hash_correct={hash_correct} meets_target={meets_target}")

print(f"engines={NUM_ENGINES} target={TARGET:064x} cycles={cycles} results={len(col.captured)}")

if passed:
    print("Test Passed")
else:
    print("Test Failed")
