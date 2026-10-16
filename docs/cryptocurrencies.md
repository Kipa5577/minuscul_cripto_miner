# Mineable Cryptocurrencies

Reference list of notable proof-of-work cryptocurrencies worth targeting for custom hardware acceleration, ASIC engine development, or software mining projects. This list broadens the scope beyond SHA-256 to include major GPU, CPU, and alternative ASIC proof-of-work algorithms.

Each price is dated to when it was looked up — treat it as an order-of-magnitude reference, not a live quote, and re-check before relying on it.

| Coin | Symbol | Mining algorithm | Target hardware | Price (USD) | Priced on | Notes |
|---|---|---|---|---|---|---|
| Monero | XMR | RandomX | CPU | $307.01 | 2026-09-13 | Highly CPU-friendly; uses code execution and memory hardness to actively resist ASICs. |
| Kaspa | KAS | kHeavyHash | ASIC / GPU | $0.0351 | 2026-09-13 | Fast blockDAG consensus; matrix-multiplication heavy algorithm currently dominated by custom ASICs. |
| Litecoin | LTC | Scrypt | ASIC | $53.56 | 2026-09-13 | Memory-hard KDF design; natively dual/merge-mined with Dogecoin. |
| Dogecoin | DOGE | Scrypt | ASIC | $0.0847 | 2026-09-13 | Dual-mined alongside Litecoin on the same Scrypt hardware without extra hash overhead. |
| Zcash | ZEC | Equihash (192,7) | ASIC / GPU | $681.74 | 2026-09-13 | Memory-hard algorithm based on the Wagner generalized birthday problem. |
| Ethereum Classic | ETC | Etchash | GPU | $8.29 | 2026-09-13 | Modified Ethash variant optimized for GPUs with smaller DAG sizes. |
| Ravencoin | RVN | KAWPOW | GPU | $0.0029 | 2026-09-13 | ProgPoW-based algorithm designed to defeat ASICs and favor consumer GPU memory access patterns. |
| Dash | DASH | X11 | ASIC | $56.41 | 2026-09-13 | Chained sequence of 11 sequential cryptographic hash functions (BLAKE to SIMD). |
| Bitcoin | BTC | SHA-256d | ASIC | $77,238.19 | 2026-09-13 | The classic standard; baseline implementation for pipelined double-SHA engines. |

---

## Architectural considerations by algorithm class

- **Standard Hash Pipelines (SHA-256d, X11):** Ideal for fixed-function hardware (FPGAs/ASICs). X11 requires cascading 11 discrete hash cores sequentially, making the pipeline significantly wider than single-purpose engines.
- **Matrix & Heavy Multiplications (kHeavyHash):** Modifies standard hash pipelines by inserting a dense matrix-vector multiplication step between core hashes. Requires dedicated hardware multiplier arrays.
- **Memory-Hard & DAG-Based (Scrypt, Etchash, Equihash):** High bandwidth and large RAM capacity govern performance. Hardware design focuses heavily on memory controller efficiency rather than raw logic gate speed.
- **CPU Execution / Anti-ASIC (RandomX, KAWPOW):** Designed specifically to break fixed-function hardware. RandomX executes random synthetic instruction sequences in custom virtual machines, making CPU caches and general-purpose execution units the optimal target platform.