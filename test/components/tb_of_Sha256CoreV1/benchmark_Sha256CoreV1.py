import py4hw
from minuscul_crypto_miner.architecture.Sha256Crcuits import *
from minuscul_crypto_miner.architecture.bus.bus import BusInterface
from sha256_core_v1_test_doubles import Sender, Collector


# Benchmark variables
CLOCK_FREQUENCY_HZ = 50_000_000  # assumed target clock frequency, for translating cycles into wall-clock time
NUM_BENCHMARK_SEEDS = 50
L1_WORDS_PER_CYCLE = 2 # 1 2
L2_ROUNDS_PER_CYCLE = 4 # 1 2 4

sys = py4hw.HWSystem()

reset = sys.wire('reset', 1)
py4hw.Constant(sys, 'reset', 0, reset)

bus_if = BusInterface(sys, 'bus_if')

seed_out = sys.wire('seed_out', 512)
nbbytes_out = sys.wire('nbbytes_out', 8)
digest_out = sys.wire('digest_out', 256)
digest_ready = sys.wire('digest_ready', 1)
digest_fetched = sys.wire('digest_fetched', 1)

engine = Sha256CoreV1(sys, 'sha256_engine', reset, bus_if,
                       seed_out, nbbytes_out, digest_out, digest_ready, digest_fetched,
                       debug=False,l2_rounds_per_cycle=L2_ROUNDS_PER_CYCLE,l1_words_per_cycle=L1_WORDS_PER_CYCLE)

benchmark_seeds = [(i, max(1, (i.bit_length() + 7) // 8)) for i in range(1, NUM_BENCHMARK_SEEDS + 1)]

snd = Sender(sys, 'snd', bus_if, benchmark_seeds, debug=False)
col = Collector(sys, 'col', seed_out, nbbytes_out, digest_out, digest_ready, digest_fetched)

l1_handler = engine.children['L1']
l2_handler = engine.children['L2']

sim = sys.getSimulator()

total_clock_cycles = 0
l1_busy_cycles = 0
l2_busy_cycles = 0

while len(col.captured) < NUM_BENCHMARK_SEEDS:
    sim.clk(1)
    total_clock_cycles += 1
    if l1_handler.state != 0:
        l1_busy_cycles += 1
    if l2_handler.state != 0:
        l2_busy_cycles += 1

avg_cycles_per_hash = total_clock_cycles / NUM_BENCHMARK_SEEDS
estimated_seconds = total_clock_cycles / CLOCK_FREQUENCY_HZ
hashes_per_second = NUM_BENCHMARK_SEEDS / estimated_seconds

print("--- Benchmark ---")
print(f"seeds hashed              = {NUM_BENCHMARK_SEEDS}")
print(f"total clock cycles        = {total_clock_cycles}")
print(f"avg cycles / hash         = {avg_cycles_per_hash:.2f}")
print(f"L1_Handler busy cycles    = {l1_busy_cycles} ({100*l1_busy_cycles/total_clock_cycles:.1f}%)")
print(f"L2_Handler busy cycles    = {l2_busy_cycles} ({100*l2_busy_cycles/total_clock_cycles:.1f}%)")
print(f"assumed clock frequency   = {CLOCK_FREQUENCY_HZ:,} Hz")
print(f"estimated wall-clock time = {estimated_seconds*1e3:.3f} ms")
print(f"estimated throughput      = {hashes_per_second:,.0f} hashes/s")
