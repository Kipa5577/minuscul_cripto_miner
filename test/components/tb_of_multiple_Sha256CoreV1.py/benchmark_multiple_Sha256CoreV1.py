import py4hw
from minuscul_crypto_miner.architecture.Sha256Crcuits import *
from minuscul_crypto_miner.architecture.bus.bus import Bus, BusInterface, ResultBus
from minuscul_crypto_miner.architecture.seedGenerator.seedGenerator import seedGenerator
from minuscul_crypto_miner.architecture.difficultyValidator.DifficultyValidator import DifficultyValidator


# Architecture under benchmark:
#   seedGenerator -> Bus -> NUM_ENGINES x Sha256CoreV1 -> ResultBus -> DifficultyValidator
#
# Unlike tb_of_multiple_Sha256CoreV1.py this doesn't care about finding
# passing digests -- TARGET is left at 0 (never passes) so DifficultyValidator
# never has to wait on a downstream "found block" consumer, and every digest
# it examines just measures raw parallel hashing throughput of the whole
# engine array.

# Benchmark variables
CLOCK_FREQUENCY_HZ = 50_000_000  # assumed target clock frequency, for translating cycles into wall-clock time
NUM_ENGINES = 10               # number of Sha256CoreV1 instances sharing the bus
NUM_BENCHMARK_HASHES = 200       # total digests to examine (across all engines) before reporting
TARGET = 0                       # never passes: this run only measures throughput, not found blocks


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
py4hw.Constant(sys, 'result_fetched', 0, result_fetched)  # TARGET=0 means state 2 is never entered, nothing to fetch

validator = DifficultyValidator(sys, 'validator', reset, target_wire,
                                 merged_digest_out, merged_digest_ready, merged_digest_fetched,
                                 merged_seed_out, merged_nbbytes_out,
                                 result_ready, found_seed_out, found_nbbytes_out, found_digest_out, result_fetched,
                                 debug=False)

sim = sys.getSimulator()

total_clock_cycles = 0
hashes_completed = 0
l1_busy_cycles = [0] * NUM_ENGINES
l2_busy_cycles = [0] * NUM_ENGINES

while hashes_completed < NUM_BENCHMARK_HASHES:
    sim.clk(1)
    total_clock_cycles += 1
    if merged_digest_fetched.get() == 1:
        hashes_completed += 1
    for i, engine in enumerate(engines):
        if engine.children['L1'].state != 0:
            l1_busy_cycles[i] += 1
        if engine.children['L2'].state != 0:
            l2_busy_cycles[i] += 1

avg_cycles_per_hash = total_clock_cycles / hashes_completed
estimated_seconds = total_clock_cycles / CLOCK_FREQUENCY_HZ
hashes_per_second = hashes_completed / estimated_seconds

avg_l1_busy_pct = 100 * sum(l1_busy_cycles) / (NUM_ENGINES * total_clock_cycles)
avg_l2_busy_pct = 100 * sum(l2_busy_cycles) / (NUM_ENGINES * total_clock_cycles)

print("--- Benchmark (multi-engine) ---")
print(f"engines                        = {NUM_ENGINES}")
print(f"hashes completed                = {hashes_completed}")
print(f"total clock cycles              = {total_clock_cycles}")
print(f"avg cycles / hash (aggregate)   = {avg_cycles_per_hash:.2f}")
print(f"L1_Handler avg busy (per engine) = {avg_l1_busy_pct:.1f}%")
print(f"L2_Handler avg busy (per engine) = {avg_l2_busy_pct:.1f}%")
print(f"assumed clock frequency         = {CLOCK_FREQUENCY_HZ:,} Hz")
print(f"estimated wall-clock time       = {estimated_seconds*1e3:.3f} ms")
print(f"estimated throughput            = {hashes_per_second:,.0f} hashes/s")
print(f"estimated throughput / engine    = {hashes_per_second/NUM_ENGINES:,.0f} hashes/s")
