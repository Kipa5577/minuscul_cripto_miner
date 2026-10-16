# Sha256CoreV1 performance notes

Baseline before any changes (50-seed benchmark, `benchmark_Sha256CoreV1.py`, 50 MHz assumed clock):

```
avg cycles / hash = 135.12
throughput        = 370,041 hashes/s
```

## Done: L2_Handler memory-read pipelining

`L2_Handler`'s round loop drove `L1_res_Buffer`'s address one round ahead and then
spent a whole state (`wait_for_memory`) blocked on the buffer's 2-cycle
(address-register + data-register) read latency, so every compression round cost
2 cycles instead of 1 (64 rounds -> ~128 cycles).

Fix: prefetch the schedule word **two** rounds ahead instead of one — issue
`address = i+1` (relative to the round currently starting) every cycle in the
calculation state, primed by one extra prefetch cycle right after init. That
keeps two reads permanently in flight, so `Val` is always ready when the round
that needs it executes, and the wait state is never re-entered after the initial
priming. See `L2_Handler.py` states 1/2/3.

Also dropped `self.Klist`'s per-clock-cycle literal rebuild in states 0 and 1
(it's already set once in `__init__` and never mutated) — simulation-speed-only
cleanup, no effect on cycle count.

Result:

```
avg cycles / hash = 72.12
throughput        = 693,289 hashes/s   (~1.9x)
```

Verified against `hashlib.sha256` via `tb_of_Sha256CoreV1.py` — all digests still match.

## Not yet done — future work

### Parallel message-schedule expansion (L1_Handler)

`W[i] = W[i-16] + sigma0(W[i-15]) + W[i-7] + sigma1(W[i-2])`. `W[i+1]` depends on
`W[i-14], W[i-6], W[i-1], W[i-15]` — none of which depend on `W[i]`. So `W[i]`
and `W[i+1]` are independent and can be computed in the same cycle with two
parallel adder/sigma units, halving L1's 48-cycle schedule expansion to ~24.

Not urgent on its own: after the L2 fix, L1 is already comfortably hidden behind
L2's ~64-cycle compression loop (L1 and L2 already overlap — see the busy-cycle
percentages in the benchmark output, both near 97%, meaning L1 computes the next
block's schedule while L2 digests the previous one). This becomes worth doing
once L2 gets faster (round unrolling below), so L1 doesn't become the new
critical path.

### Round unrolling in L2_Handler (2+ rounds/cycle)

Each compression round's logic (`temp1`/`temp2`/register shift) is purely
combinational between register stages. Chaining the round function twice (or
4x) combinationally before registering turns the 64-cycle loop into 32 (or 16)
cycles. Real tradeoff: longer combinational path per cycle lowers achievable
Fmax, so this needs to be evaluated against the target clock frequency rather
than applied blindly — prototype and re-check timing closure before committing.

### Multiple parallel Sha256CoreV1 cores

This is a nonce-search (mining) workload, not a single hash-in-a-vacuum
problem — throughput scales close to linearly by instantiating N cores behind
the shared bus with round-robin seed dispatch. `OutputBuffer` already uses a
FIFO specifically to let multiple seed/digest pairs be in flight at once, so
the core is already safe to replicate. For a given area budget this is usually
a better hashes/sec-per-gate return than deeper unrolling of a single core, and
it's orthogonal to the per-core optimizations above (can be combined with them).
