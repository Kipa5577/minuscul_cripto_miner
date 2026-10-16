# Why the BitcoinMiningChip benchmark simulates slowly

*Profiled 2026-10-13 with cProfile on `benchmark_BitcoinMiningChip.py`
(4 cores, 2 per arbiter, 9,929 clock cycles), py4hw 2026.2. The py4hw
maintainer has been informed of the issue below.*

## Summary

Almost all of the simulation time goes into one line of py4hw's
`Wire.prepare()`. Removing that line's cost makes the benchmark **11× faster**
with identical results.

## The cause

In `py4hw/base.py`, `Wire.prepare()`:

```python
def prepare(self, val:int):
    if (self in Wire.prepared):          # <-- linear scan of a list
        print('WARNING! wire {} already prepared'...)
    ...
    Wire.prepared.append(self)
```

- `Wire.prepared` is a global list of every wire prepared in the current
  cycle, and it's only emptied in `Wire.settleAll()` at the end of the cycle.
- The `in` test is a duplicate-prepare warning check, and it scans that whole
  list on every call. So the cost per cycle grows with the **square** of the
  number of prepared wires.
- At 4 cores the chip prepares about 894 wires per cycle, which is roughly
  400,000 comparisons per cycle just for this check.
- The profile shows `prepare()` taking **20.0 s of a 29.4 s run**, about
  2.3 µs per call for what should be a simple assignment.

The cost also grows with the square of the core count, which is why the
16-core benchmark runs take minutes.

## Proof

The same benchmark rerun with `prepare()`/`settleAll()` patched in memory to
use a set for the duplicate check (installed py4hw unchanged):

| | time / clock cycle | total run |
|---|---|---|
| py4hw 2026.2 as installed | 2,392 µs | 29 s |
| set-based duplicate check | **215 µs** | **2.1 s** |

Benchmark results were identical (7.08 cycles/hash).

## What's left after the fix

- **`L1_Handler_2x` writes all 64 schedule wires every cycle.** It's 60% of
  all `prepare()` calls (5.3 M of 8.9 M), even though those wires only matter
  in the single cycle when `DataReady=1`. In py4hw a wire keeps its value if
  it isn't prepared, so writing them only in that cycle gives the same
  behaviour with far fewer calls. `L1_Handler` (1x, 7.5% of calls) does the
  same, and `L1_res_Buffer` similarly writes its outputs every cycle.
  Expected gain: another 1.5–2×.
- **Startup imports are slow.** About 1–2 s per run goes on file lookups,
  because the project and virtualenv live on `/mnt/f` (a Windows drive under
  WSL). Moving them to the Linux filesystem (e.g. under `~/`) would remove
  most of that. It's a fixed cost, so it only matters for short runs.

## Who calls `prepare()` (share of 8.9 M calls)

| Component | calls | share |
|---|---|---|
| `L1_Handler_2x.clock` | 5,321,944 | 60.0% |
| `L1_Handler.clock` | 665,243 | 7.5% |
| `L2_Handler_4x.clock` | 635,456 | 7.2% |
| `L1_res_Buffer.clock` | 417,018 | 4.7% |
| `OutputBuffer.clock` | 397,160 | 4.5% |
| `InputBuffer.clock` | 317,728 | 3.6% |
| `DigestBridge.clock` | 198,580 | 2.2% |
| `ResultBus.clock` | 178,722 | 2.0% |
