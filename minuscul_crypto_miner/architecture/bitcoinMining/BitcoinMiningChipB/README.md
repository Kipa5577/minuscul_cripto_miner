# BitcoinMiningChipB — fully pipelined cores

Variant B of the mining chip. Same pins, USART protocol, registers, Control
and result tree as chip A (`../bitcoinminingchip/`, see its README for the
protocol); only the cores differ. Each core is fully pipelined: a new nonce
enters every clock cycle, so a core completes **one double-SHA256 per cycle**
instead of one per 16 cycles.

## Files

| File | Role |
|---|---|
| `BitcoinMiningChipB.py` | Structural top level. Parameters: `num_cores` (1..32), `cores_per_arbiter`, `clks_per_bit`, `rounds_per_stage` (1/2/4, default 4), `hit_fifo_depth` (default 4). |
| `PipelinedMiningCore.py` | One fully pipelined core: nonce counter → hash 1 (rounds 3..63) → final add + fixed padding → hash 2 (rounds 0..63) → little-endian target compare → hit FIFO. |

Reused unchanged from chip A and `miningengine/`: `UsartRx`/`UsartTx`,
`ChipControl`, `Sha256Engine` (shared midstate), `SecondBlockPrecompute`
(rounds 0–2 once per job), `NBitsDecoder` (Bitcoin byte order), `ResultBus`.

Testbench and benchmark: `test/components/tb_of_BitcoinMiningChipB/`.

## How the core works

```
 nonce counter ─► [stage 0] ─► [stage 1] ─► … ─► [stage N-1] ─► hit FIFO ─► arbiter
  (start, step)    rounds        rounds            final add +
                   3..           …                 LE compare
```

- Each pipeline stage has its own round logic and its own registers: the
  working state a..h, the 16-word message-schedule window (each `W[t]` is
  computed in the stage that needs it) and the nonce, which travels with
  its data — so a hit is reported with its own nonce, no `NonceTracker`.
- `rounds_per_stage` chains 1, 2 or 4 rounds per stage. Throughput is always
  1 hash/cycle/core; this only trades pipeline depth against logic per stage
  (clock frequency):

  | `rounds_per_stage` | stages (latency) | logic per stage |
  |---|---|---|
  | 1 | 127 | 1 round |
  | 2 | 64 | 2 rounds |
  | 4 (default) | 33 | 4 rounds — same as chip A's default 4 rounds/cycle |

- Hash 1 starts at round 3 from the per-job `state3` (Bitcoin shortcut, as
  chip A). The final addition of hash 1 and the padding of the 32-byte
  digest are folded into the last round stage when it has a spare round
  slot (same rule as chip A's `Sha256Compressor`).
- The last stage compares the digest read little-endian (Bitcoin order)
  with the target. Chip A's round-61 early reject is not used: it saves no
  throughput when one attempt enters per cycle regardless, and the RESULT
  frame needs the full digest.
- Hits go into a small FIFO; after each fetch, ready drops for one cycle so
  `ResultBus` sees it low before the next hit. A hit arriving while the FIFO
  is full is dropped and counted (`dropped_hits`) — harmless, Control only
  reports the first hit of a job, and at real difficulty a hit is 1 in
  ~2³² attempts.
- Control's job sequencing is unchanged: when a job ends, the cores stop
  injecting nonces and Control waits until every pipeline and hit FIFO is
  empty (`idle_out`) before going idle — the drain takes about one pipeline
  depth.

## Measured (benchmark_BitcoinMiningChipB.py, 50 MHz assumed)

| | chip A (fused + shortcuts, 4 rounds/cycle) | chip B (`rounds_per_stage=4`) |
|---|---|---|
| cycles per double-hash per core | 15.9 | **1.0** |
| throughput per core at 50 MHz | 3.1 MH/s | **50 MH/s** |
| 16 cores | 50 MH/s | **800 MH/s** |
| first hash after a job starts | ~33 cycles (2 stages × 16) | 34 cycles (pipeline fill) |
| ABORT drain | 46 cycles | 41 cycles (127 at `rounds_per_stage=1`) |

Both chips pass the same protocol testbench (all three `rounds_per_stage`
settings for chip B).

## What it costs (rough estimate, not synthesised)

Per core, chip B unrolls all 125 rounds instead of chip A's 2 × 4, and every
stage carries its own registers (~800 bits: a..h + schedule window + nonce):

| for 1 double-hash per cycle | chip A (16 cores) | chip B (1 core, 4 rounds/stage) |
|---|---|---|
| round logic | 16 × 8 = 128 rounds | 125 rounds |
| registers | ~16 × 3 kbit ≈ 48 kbit | ~33 × 0.8 kbit ≈ 26 kbit |
| control | 32 FSMs, handshakes, nonce FIFOs | none in the core |

So per hash/s the round logic is about the same — the fully pipelined core
is not "free" throughput, it packs the same work into one core — but it
needs fewer registers and no per-core control, and its clock can be pushed
higher with `rounds_per_stage=1` (1 round of logic per cycle, at the cost
of ~4× the pipeline registers). The schedule window is the biggest register
cost; real designs trim it to the words each stage still needs.
