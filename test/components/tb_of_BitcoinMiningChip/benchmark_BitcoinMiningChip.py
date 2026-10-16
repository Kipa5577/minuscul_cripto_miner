import os
import time

import py4hw
from minuscul_crypto_miner.architecture.bitcoinMining.bitcoinminingchip import BitcoinMiningChip
from minuscul_crypto_miner.architecture.bitcoinMining.bitcoinminingchip import protocol as P
from bitcoin_mining_chip_test_doubles import HostUart, make_header, frame_cycles, EASY_NBITS, HARD_NBITS


# Architecture under benchmark (driven only through its UART pins):
#   HostUart -> uart_rx -> UsartRx -> ChipControl -> Sha256Engine (shared midstate)
#   + NUM_CORES x BitcoinMiningCore (each ending in its own DifficultyValidator)
#   -> ResultBus per group (hits only) -> top ResultBus -> ChipControl -> UsartTx -> uart_tx -> HostUart
#
# Three phases:
#   1. raw throughput -- a NEW_JOB with HARD_NBITS (never passes) so every
#      digest the per-core validators examine is a pure nonce-search hash;
#   2. control overheads -- PING round trip, NEW_JOB -> JOB_ACK, midstate
#      load, ABORT_JOB drain, all read off ChipControl's own job state;
#   3. end-to-end -- NUM_EASY_JOBS easy jobs (EASY_NBITS) back to back, from
#      the first NEW_JOB bit on the line to the RESULT frame at the host.

# Benchmark variables
CLOCK_FREQUENCY_HZ = 50_000_000  # assumed target clock frequency, for translating cycles into wall-clock time
REAL_BAUD_RATE = 115_200         # for re-scaling the measured UART overheads to a realistic link
NUM_CORES = int(os.environ.get("BENCH_NUM_CORES", 16))
CORES_PER_ARBITER = int(os.environ.get("BENCH_CORES_PER_ARBITER", 4))
NUM_BENCHMARK_HASHES = int(os.environ.get("BENCH_NUM_HASHES", 200))  # double-hashes to examine in phase 1
NUM_EASY_JOBS = int(os.environ.get("BENCH_NUM_EASY_JOBS", 3))         # jobs to time end-to-end in phase 3
CLKS_PER_BIT = int(os.environ.get("BENCH_CLKS_PER_BIT", 4))           # kept tiny so the simulation stays fast
PIPELINE = os.environ.get("BENCH_PIPELINE", "fused")                     # fused | l1_l2
SHORTCUTS = os.environ.get("BENCH_SHORTCUTS", "1" if PIPELINE == "fused" else "0") == "1"  # Bitcoin shortcuts (fused only)
L1_WORDS_PER_CYCLE = int(os.environ.get("BENCH_L1_WORDS_PER_CYCLE", 2))  # 1 2 -- l1_l2 pipeline only
L2_ROUNDS_PER_CYCLE = int(os.environ.get("BENCH_L2_ROUNDS_PER_CYCLE", 4))  # fused: 1 2 4 8, l1_l2: 1 2 4
MAX_CYCLES_PER_STEP = 500_000

CONTROL_STATE_NAMES = {0: "idle", 1: "loading", 2: "running", 3: "draining"}


tb_start_time = time.perf_counter()
init_start_time = time.perf_counter()

sys = py4hw.HWSystem()

reset = sys.wire('reset', 1)
py4hw.Constant(sys, 'reset', 0, reset)
host_to_chip = sys.wire('host_to_chip', 1)
chip_to_host = sys.wire('chip_to_host', 1)

host = HostUart(sys, 'host', host_to_chip, chip_to_host, CLKS_PER_BIT)
chip = BitcoinMiningChip(sys, 'chip', reset, host_to_chip, chip_to_host,
                         num_cores=NUM_CORES, cores_per_arbiter=CORES_PER_ARBITER, clks_per_bit=CLKS_PER_BIT,
                         pipeline=PIPELINE, bitcoin_shortcuts=SHORTCUTS, l1_words_per_cycle=L1_WORDS_PER_CYCLE, l2_rounds_per_cycle=L2_ROUNDS_PER_CYCLE,
                         debug=False)

control = chip.children['control']
num_groups = (NUM_CORES + CORES_PER_ARBITER - 1) // CORES_PER_ARBITER
cores = [chip.children[f'core_{i}'] for i in range(NUM_CORES)]
# Each core's validator ack back to its finisher is "one double-hash examined".
validator_fetched = [core.children['validator'].Digest_Fetched for core in cores]

# Per-stage "busy" probes (component not idle), named for the report.
if PIPELINE == 'fused':
    STAGE_PROBES = [
        ("first SHA-256", lambda core: core.children['sha1'].state != 0),
        ("finisher SHA-256", lambda core: core.children['sha2'].state != 0),
    ]
else:
    STAGE_PROBES = [
        ("continuation L1_Handler", lambda core: core.children['L1'].state != 0),
        ("continuation L2_Handler", lambda core: core.children['L2'].state != 0),
        ("finisher L1_Handler", lambda core: core.children['finisher'].children['L1'].state != 0),
        ("finisher L2_Handler", lambda core: core.children['finisher'].children['L2'].state != 0),
    ]

sim = sys.getSimulator()

init_end_time = time.perf_counter()
init_elapsed_seconds = init_end_time - init_start_time

total_clock_cycles = 0
state_enter_cycle = {}  # control job state -> cycle it was last entered
_prev_control_state = control.state


def step(n=1):
    global total_clock_cycles, _prev_control_state
    for _ in range(n):
        sim.clk(1)
        total_clock_cycles += 1
        if control.state != _prev_control_state:
            state_enter_cycle[control.state] = total_clock_cycles
            _prev_control_state = control.state


def run_until(cond, what):
    start = total_clock_cycles
    while not cond():
        if total_clock_cycles - start > MAX_CYCLES_PER_STEP:
            raise RuntimeError(f"benchmark stuck waiting for {what} (control state={CONTROL_STATE_NAMES[control.state]})")
        step()


def send_and_wait(frame, n_replies, what):
    first = len(host.frames)
    host.send(frame)
    run_until(lambda: len(host.frames) >= first + n_replies, what)
    return host.frames[first:first + n_replies]


sim_start_time = time.perf_counter()

# --- Phase 2a: PING round trip (idle chip) ----------------------------------
ping_start = total_clock_cycles
send_and_wait(P.build_frame(P.CMD_PING), 1, "PONG")
ping_round_trip = total_clock_cycles - ping_start
ping_line_cycles = 2 * frame_cycles(0, CLKS_PER_BIT)

# --- Phase 1: raw throughput on a never-passing job --------------------------
hard_frame = P.build_frame(P.CMD_NEW_JOB, make_header(13, HARD_NBITS, SHORTCUTS)[:76])
job_start = total_clock_cycles
host.send(hard_frame)
run_until(lambda: not host.sending(), "NEW_JOB to go out")
new_job_sent = total_clock_cycles
run_until(lambda: len(host.frames) >= 2, "JOB_ACK")
job_ack_latency = host.frame_cycles[-1] - new_job_sent
run_until(lambda: control.state == 2, "cores to start")
midstate_load_cycles = state_enter_cycle[2] - state_enter_cycle[1]

hashes_completed = 0
throughput_cycles = 0
stage_busy = [0] * len(STAGE_PROBES)
while hashes_completed < NUM_BENCHMARK_HASHES:
    step()
    throughput_cycles += 1
    hashes_completed += sum(w.get() for w in validator_fetched)
    for core in cores:
        for k, (_, probe) in enumerate(STAGE_PROBES):
            if probe(core):
                stage_busy[k] += 1

# --- Phase 2b: ABORT_JOB drain while the cores are full ----------------------
host.send(P.build_frame(P.CMD_ABORT_JOB))
run_until(lambda: control.state == 0, "ABORT_JOB drain")
abort_drain_cycles = state_enter_cycle[0] - state_enter_cycle[3]

# --- Phase 3: end-to-end easy jobs -------------------------------------------
easy_jobs = []  # (total, search, drain) cycles per job
for j in range(NUM_EASY_JOBS):
    first = len(host.frames)
    start = total_clock_cycles
    host.send(P.build_frame(P.CMD_NEW_JOB, make_header(7 + 4 * j, EASY_NBITS, SHORTCUTS)[:76]))
    run_until(lambda: len(host.frames) >= first + 2, f"RESULT of easy job {j}")
    cmd, payload, _ = host.frames[first + 1]
    if cmd != P.RSP_RESULT:
        raise RuntimeError(f"easy job {j}: expected RESULT, got cmd={cmd:#04x}")
    easy_jobs.append((host.frame_cycles[first + 1] - start,
                      state_enter_cycle[3] - state_enter_cycle[2],
                      state_enter_cycle[0] - state_enter_cycle[3]))

sim_end_time = time.perf_counter()
sim_elapsed_seconds = sim_end_time - sim_start_time
avg_seconds_per_clk_cycle = sim_elapsed_seconds / total_clock_cycles

avg_cycles_per_hash = throughput_cycles / hashes_completed
hashes_per_second = CLOCK_FREQUENCY_HZ / avg_cycles_per_hash
busy_pct = lambda n: 100 * n / (NUM_CORES * throughput_cycles)

new_job_line_cycles = frame_cycles(P.NEW_JOB_LEN, CLKS_PER_BIT)
result_line_cycles = frame_cycles(36, CLKS_PER_BIT)
avg_total = sum(t for t, _, _ in easy_jobs) / len(easy_jobs)
avg_search = sum(s for _, s, _ in easy_jobs) / len(easy_jobs)
avg_drain = sum(d for _, _, d in easy_jobs) / len(easy_jobs)

def at_real_baud(line_cycles):
    # same frame, re-timed at REAL_BAUD_RATE instead of CLKS_PER_BIT
    return line_cycles / (10 * CLKS_PER_BIT) * 10 / REAL_BAUD_RATE

print("--- Benchmark (BitcoinMiningChip) ---")
print(f"cores                                = {NUM_CORES} ({num_groups} arbiter groups of {CORES_PER_ARBITER})")
print(f"pipeline                             = {PIPELINE} ({L2_ROUNDS_PER_CYCLE} rounds/cycle"
      + (f", L1 {L1_WORDS_PER_CYCLE} words/cycle)" if PIPELINE == 'l1_l2' else ")")
      + (", Bitcoin shortcuts" if SHORTCUTS else ""))
if SHORTCUTS:
    rejects = sum(core.children['sha2'].early_rejects for core in cores)
    finished = sum(core.children['sha2'].blocks for core in cores)
    print(f"finisher early rejects at round 61   = {rejects} of {finished} blocks")
print(f"clks per UART bit                    = {CLKS_PER_BIT} (= {CLOCK_FREQUENCY_HZ // CLKS_PER_BIT:,} baud at the assumed clock)")
print("[mining throughput, never-passing target]")
print(f"double-hashes examined               = {hashes_completed}")
print(f"clock cycles                         = {throughput_cycles}")
print(f"avg cycles / double-hash (aggregate) = {avg_cycles_per_hash:.2f}")
for (name, _), busy in zip(STAGE_PROBES, stage_busy):
    print(f"{name + ' avg busy':<37}= {busy_pct(busy):.1f}%")
print(f"estimated throughput                 = {hashes_per_second:,.0f} double-hashes/s")
print(f"estimated throughput / core          = {hashes_per_second / NUM_CORES:,.0f} double-hashes/s")
print("[control overheads, clock cycles]")
print(f"PING round trip                      = {ping_round_trip} ({ping_line_cycles} of it on the UART line)")
print(f"NEW_JOB last bit -> JOB_ACK received = {job_ack_latency} ({frame_cycles(0, CLKS_PER_BIT)} of it on the UART line)")
print(f"midstate load (loading state)        = {midstate_load_cycles}")
print(f"ABORT_JOB drain (draining state)     = {abort_drain_cycles}")
print(f"[end-to-end, {NUM_EASY_JOBS} easy jobs, nBits={EASY_NBITS:#010x}]")
for j, (total, search, drain) in enumerate(easy_jobs):
    print(f"job {j}: NEW_JOB first bit -> RESULT    = {total} (search {search}, drain {drain})")
print(f"avg end-to-end                       = {avg_total:.0f} cycles = {avg_total / CLOCK_FREQUENCY_HZ * 1e6:.1f} us")
print(f"  of which NEW_JOB + RESULT frames   = {new_job_line_cycles + result_line_cycles} cycles on the UART line")
print(f"  of which search / drain            = {avg_search:.0f} / {avg_drain:.0f} cycles")
print(f"NEW_JOB + RESULT frames at {REAL_BAUD_RATE} baud = "
      f"{(at_real_baud(new_job_line_cycles) + at_real_baud(result_line_cycles)) * 1e3:.2f} ms per job")
print(f"assumed clock frequency              = {CLOCK_FREQUENCY_HZ:,} Hz")

tb_end_time = time.perf_counter()
tb_elapsed_seconds = tb_end_time - tb_start_time

print("--- Simulator Performance ---")
print(f"circuit init time         = {init_elapsed_seconds*1e3:.3f} ms")
print(f"total clock cycles        = {total_clock_cycles}")
print(f"avg time / clk cycle      = {avg_seconds_per_clk_cycle*1e6:.3f} us")
print(f"total testbench time      = {tb_elapsed_seconds*1e3:.3f} ms")
