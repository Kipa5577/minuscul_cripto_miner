# BitcoinMiningChip performance evaluation

*Measured 2026-10-13 with `test/components/tb_of_BitcoinMiningChip/benchmark_BitcoinMiningChip.py`
(`BENCH_NUM_CORES`, `BENCH_CORES_PER_ARBITER`, `BENCH_L1_WORDS_PER_CYCLE`,
`BENCH_L2_ROUNDS_PER_CYCLE` varied as shown below).*

Today each core needs about 28 cycles per double-hash, and cores scale
linearly (16 cores measured 28.8 cycles/hash each, against 28.2 for 2 cores).
At the assumed 50 MHz, 16 cores give about 28 M double-hashes/s. Two things
hold it back: the result arbiters choke if too many cores share one, and the
L1 schedule step is now the slowest stage in each core.

**Caveat:** these are cycle counts at a fixed assumed 50 MHz. Chaining 4
rounds per cycle makes each cycle's logic much longer, so the real clock would
drop. On silicon the unrolled settings would gain less than the cycle counts
show.

## What the measurements show

| Config (per core) | cycles/hash/core | What limits it |
|---|---|---|
| L1=1, L2=1 | 74.5 | L2 round loop |
| L1=2, L2=1 | 72.9 | L2 round loop |
| L1=2, L2=2 | 39.9 | L2 |
| L1=1, L2=4 | 53.0 | L1 (L2 only 42% busy) |
| **L1=2, L2=4 (default)** | **28.2** | **L1** (L2 78% busy) |

| 16 cores | aggregate cycles/hash |
|---|---|
| 4 arbiters of 4 cores | 1.80 |
| 1 arbiter of 16 cores | **3.09 (42% slower)** |

## Changes, ranked

**1. Fix the arbiter bandwidth limit.** ✅ **Done** (2026-10-13) — each core
now has its own `DifficultyValidator` and the arbiters carry hits only.
Measured after the fix (cycles per double-hash, aggregate):

| Config | before | after |
|---|---|---|
| 16 cores, 1 arbiter of 16 | 3.09 | **1.75** (1.77× faster) |
| 16 cores, 4 arbiters of 4 | 1.80 | **1.75** |
| 32 cores, 1 arbiter of 32 | — | **0.88** (28.2 per core, linear) |

`cores_per_arbiter` no longer affects throughput. ABORT drain also dropped
from 117 to 88 cycles. Original analysis, kept for reference:

- **Cause:** a `ResultBus` arbiter moves at most one digest every 3 cycles
  (select, validator fetch, ack). Every hash goes through it, even ones that
  miss the target. With 28 cycles per hash, more than about 9 cores per
  arbiter saturates it.
- **Quick fix:** limit `cores_per_arbiter` to about 8. This is a
  configuration rule that could be checked in `BitcoinMiningChip`.
- **Real fix:** give each core its own difficulty check, so only winning
  hashes ever reach an arbiter. At real difficulty that's almost no traffic,
  so one flat arbiter would work for any number of cores. The check could
  also compare only the top 32 bits instead of all 256.
- **This becomes mandatory with faster cores:** at about 18 cycles per hash,
  the limit drops to about 6 cores per arbiter.

**2. Remove the L1 bottleneck.** ✅ **Done** (2026-10-13) with option b —
new `Sha256Crcuits/components/Sha256Compressor.py` computes the message
schedule from a rolling 16-word window inside the round loop. Each core is
now `nonce iterator → Sha256Compressor (H₀ = midstate) → Sha256Compressor
(fixed 32-byte padding) → DifficultyValidator`; `L1_Handler`,
`L1_res_Buffer`, `L2_Handler`, `OutputBuffer`, `DigestBridge` and the
`Sha256CoreV1` finisher are gone from the core. Selected with
`pipeline='fused'` (the chip's default); `pipeline='l1_l2'` keeps the old
stages (still `BitcoinMinerEngine`'s default). Measured, 4 cores:

| Pipeline | cycles/hash per core | stage busy |
|---|---|---|
| old `l1_l2` (L1=2, L2=4) | 28.0 | L1 96%, L2 78% |
| **fused, 4 rounds/cycle** | **16.8** (1.67× faster) | both 100% |
| fused, 8 rounds/cycle | 8.9 | both 100% |
| fused, 2 rounds/cycle | 32.7 | both 100% |
| fused, 1 round/cycle | 64.7 (old L1=2, L2=1: 72.9) | both 100% |

A block costs exactly 64/k + 1 cycles (16 rounds + 1 finalize at k=4). The
two stages are balanced, and ABORT drain halved (88 → 46 cycles). Side
effect: simulation is 3.8× faster per cycle (218 → 57 µs with the set-based
`prepare()` patch), because a compressor writes 3 output wires per cycle
instead of `L1_Handler`'s 64 schedule words. Original analysis, kept for
reference:

- **Cause:** `L1_Handler_2x` takes about 28 cycles per block (24 compute
  cycles plus init, wait and done steps). `L2_Handler_4x` takes about 21
  (16 rounds plus about 5 cycles of handshake). L2 is idle 22% of the time.
- **Option a, an `L1_Handler_4x`:** 4 schedule words per cycle brings L1 to
  about 14 cycles, and L2 becomes the limit at about 21, so roughly 28 → 21
  (1.3x). This is a small change. `W[i+2]` depends on `W[i]`, so the 4 words
  need chained logic within the cycle, the same longer-path trade-off as L2
  unrolling.
- **Option b, compute the schedule inside L2 (better):** L2 keeps a 16-word
  window and computes each `W[t+16]` as the rounds go, which is how real
  mining chips do it. This removes `L1_Handler` and `L1_res_Buffer`, a 64×32-bit
  register buffer (2,048 bits), twice per core. It also removes the
  L1→buffer→L2 handshakes. Expected about 17–19 cycles per block, roughly
  1.5x. The freed area also means more cores fit, and the measurements show
  throughput grows directly with core count. It's a bigger change: one new
  component used by both hash stages, with nothing outside the core affected.

**3. Remove handshake overhead per block.** ✅ **Mostly done by #2** — the
compressor's output register lets the next block load in the same cycle the
previous digest is finalized, so the only per-block overhead left is the 1
finalize cycle (H = H₀ + a..h). Folding that addition into the last round
cycle would give 16 instead of 17 cycles (~6%), at the cost of a longer path
in that cycle. Original analysis, kept for reference: gain about 10–20%.
- Each L2 block spends about 5 cycles that aren't rounds (init, prefetch,
  final add, done/consumed handshake).
- Accepting the next block while the current one finishes, with a second set
  of state registers, would hide most of that. It's worth doing after #2,
  because then the overhead is a larger share of a shorter block.

**4. Bitcoin-specific shortcuts.** ✅ **Done** (2026-10-13), chip option
`bitcoin_shortcuts=True` (default):

- **Rounds 0–2 precomputed once per job** by `miningengine/SecondBlockPrecompute.py`
  (one round per cycle, 3 cycles after the midstate); each core's first
  `Sha256Compressor` starts at round 3 (`start_round=3`, `state_init`).
- **Simpler finisher input** — already done by the fused pipeline
  (`pad_digest_input`).
- **Early reject at round 61** in each core's finisher (`early_reject`):
  generalised to any target — if `byteswap(H7)` (the top word of the digest
  read little-endian) exceeds the target's top word, the last 3 rounds are
  skipped and a marker that fails every target goes to the validator, so the
  nonce pairing stays one output per block. Needed the endianness decision:
  the chip now uses **real Bitcoin byte order** (nBits little-endian, digest
  compared little-endian; see README "Byte order").
- Saving 3 rounds only saves a cycle because the end-of-block work (final
  H addition or the round-61 check) is folded into the last round cycle
  when it has a spare round slot.

Measured, 4 cores, cycles per double-hash per core:

| rounds/cycle | before | with shortcuts | gain |
|---|---|---|---|
| 1 | 64.8 | 61.8 | 4.6% |
| **4 (default)** | **16.9** | **15.9** | **5.9%** |
| 8 | 9.0 | 7.9 | 12% |

On a never-passing target ~99% of finisher blocks are dropped at round 61;
the per-job precompute adds 4 cycles to loading (126 → 130). Original
analysis, kept for reference — gain about 5–10% in total; these are
standard mining-chip techniques.
- **Precompute the first 3 rounds once per job.** In the second header block
  only the nonce (`W[3]`) changes. `W[0..2]` (merkle tail, time, bits) are
  fixed for the whole job, so the first 3 of 64 rounds give the same result
  for every nonce. Control or the midstate engine could compute them once per
  job (about 5% saved). Several schedule words are also partly fixed.
- **Simpler finisher input.** The finisher always hashes 32 bytes with fixed
  padding. A fixed-padding feeder, like `bitcoin_Nonce_iterator`, could
  replace `DigestBridge` → `InputBuffer` → `InputFormatter`. That saves area
  and latency but not throughput, because those stages aren't the bottleneck.
- **Stop the final hash 3 rounds early.** With real Bitcoin byte order, a
  winning hash needs `H7 = 0`, which is known after round 61, so the last 3
  rounds can be skipped (about 5%). With this project's big-endian
  comparison, the deciding word is `H0`, which needs all 64 rounds, so this
  only works after fixing the endianness.

**5. Deeper unrolling or a fully pipelined core.** ✅ **Fully pipelined core
built as a separate chip variant**, `../BitcoinMiningChipB/` (2026-10-13):
1 double-hash per cycle per core (vs 15.9 cycles here), 50 MH/s per core at
50 MHz; same protocol, Control and result tree. Its README compares the cost
(round logic per hash/s about equal, fewer registers, no per-core control).
Original analysis, kept for reference — big gain, big cost.
- 8 rounds per cycle only helps after #2, and the clock-speed cost grows
  with it.
- A fully pipelined design (64 round stages, one hash per cycle per core) is
  what commercial mining chips use. It's much larger and a different
  architecture from the current handshake pipeline.

**6. System level.**
- The chip stops at the first hit; a "report and keep searching" mode would
  suit pool mining.
- The nonce space wraps around instead of moving to the next job.
- The time between jobs (drain about 100 cycles + midstate about 126) is
  negligible for real job lengths.

## Recommended order

1. ~~**Per-core difficulty check**~~ — done, see #1 above.
2. ~~**Compute the schedule inside L2.**~~ — done, see #2 above (also covers
   most of #3).
3. ~~**Bitcoin shortcuts**~~ — done, see #4 above. What's left is #5
   (deeper unrolling / fully pipelined cores) and the system-level items in #6.

See also `../../Sha256Crcuits/sha256_core_v1_perf_notes.md` for the earlier
single-core optimisations (L2 memory-read pipelining, round unrolling).
