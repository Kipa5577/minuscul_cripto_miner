# BitcoinMiningChip

## Purpose

`bitcoinminingchip` is the top-level chip that congregates multiple mining
engines behind a single USART link. The job's first 64 (constant) header
bytes are only ever compressed **once**, chip-wide, by a single shared
`Sha256Engine` — the same midstate is then handed identically to every
mining engine, rather than each engine recomputing it internally the way a
standalone `BitcoinMinerEngine` does today. The rest of the job (the fields
that feed the variable second block) is dispatched identically to all
engines as well; each engine then runs the same nonce-search core
(`bitcoin_Nonce_iterator` -> L1/L2[midstate] -> finisher, i.e. everything
`BitcoinMinerEngine` does *except* its own `Sha256Engine`) on its own
nonce offset/stride, so no two engines search the same nonce.

This means the engines living under this chip are **not** plain
`BitcoinMinerEngine` instances (that would duplicate the midstate
computation N times) — they are `miningengine.BitcoinMiningCore`, the
nonce-search lane factored out of `BitcoinMinerEngine` (which now builds its
own lanes from it too), fed by the chip's one shared midstate wire.

## Files

| File | Role |
|---|---|
| `BitcoinMiningChip.py` | Structural top level; only pins are `reset`, `uart_rx`, `uart_tx`. Parameters: `num_cores` (1..32), `cores_per_arbiter`, `clks_per_bit`, `pipeline` (`'fused'` default: each core's two SHA-256 stages are `Sha256Compressor`s with the message schedule computed on the fly, 64/`l2_rounds_per_cycle` + 1 cycles per hash; `'l1_l2'`: original L1/L2 + `Sha256CoreV1` stages), `l2_rounds_per_cycle` (fused: 1/2/4/8), `l1_words_per_cycle` (`l1_l2` only), `bitcoin_shortcuts` (default on, fused only: rounds 0–2 of the second header block precomputed once per job by `miningengine.SecondBlockPrecompute`, finisher early reject at round 61, Bitcoin byte order — see "Byte order" below; 64/k cycles per hash instead of 64/k + 1). |
| `ChipControl.py` | The control leaf: frame parser, register file, job state machine, TX frame queue. |
| `UsartRx.py` / `UsartTx.py` | Standard asynchronous 8N1 UART (1 start bit, 8 data bits LSB first, 1 stop bit, no parity, line idles high); bit time = `clks_per_bit` clock cycles (>= 4). The receiver samples mid-bit, rejects start-bit glitches and drops bytes with a bad stop bit. |
| `UsartInterface.py` | `ByteStreamInterface` (`Data`/`Valid`/`Ack`): the byte stream between the UART and Control, same in both directions. |
| `protocol.py` | Command/response codes, register addresses, NACK reasons, `build_frame()`/`checksum()` — shared by hardware and testbench. |

Testbench: `test/components/tb_of_BitcoinMiningChip/tb_of_BitcoinMiningChip.py`
drives the chip only through its UART pins (with its own independent host
UART model) and checks every protocol path plus the RESULT digests against
`bitcoin_double_sha256()`.

```
                ┌─────────────────────────────────────────────────────┐
                │                  BitcoinMiningChip                    │
                │                                                       │
 USART RX -->│               job fields (wire, broadcast)             │
   <-- USART TX│  Control  =========+========+========+========      │
                │  (state       Sha256Engine  Core0    Core1  ... CoreN│
                │   machine)    (shared)         │        │       │   │
                │      ^                      ArbiterA  ArbiterB ...  │
                │      │                          │        │         │
                │      └──────── result ─────── ArbiterTop ┘         │
                └─────────────────────────────────────────────────────┘
```

- **Control** — a single state machine with the only direct connection to
  USART RX/TX (same "no children + own `clock()`" leaf-class constraint
  `_Sha256EngineController` already follows). It decodes `NEW_JOB`/
  `ABORT_JOB`/`PING`/`READ_REG`/`WRITE_REG` frames, drives the shared
  `Sha256Engine`'s `new_job` pulse, latches the job fields onto the
  broadcast wires below, replies `JOB_ACK`/`JOB_NACK`/`PONG`/`REG_VALUE`/
  `WRITE_ACK`, waits on the arbiter tree's `result_ready`, and encodes/
  sends the `RESULT` frame — i.e. it owns framing (decode+encode), the
  chip's own idle/loading/running/done sequencing, **and** the register
  file described below, there's no separate decoder/encoder/config block.
- **Sha256Engine (shared)** — one instance, computes the midstate over
  `header_first64bytes` exactly once per job; every core reads the same
  `midstate`/`midstate_ready` wires.
- **Job fields (wire, broadcast)** — `merkle_tail`/`time`/`bits` and each
  core's nonce offset/stride aren't actively "dispatched" one by one; since
  every core needs the exact same job at once, Control just latches them
  onto plain wires that fan out to all cores in parallel, the same way a
  single `wire()` already broadcasts to multiple readers elsewhere in this
  codebase.
- **Cores** — the nonce-search part of `BitcoinMinerEngine` (everything
  except its own `Sha256Engine`), one per lane at chip level. Each core's
  nonce offset/stride is computed and assigned by Control itself — from
  the core's fixed index, `NUM_CORES`, `ACTIVE_CORES_MASK` (disabled cores
  are skipped when spacing the stride) and `CORE_BASE_OFFSET` — the same
  way `BitcoinMinerEngine` fixes `lane_id`/`stride` for its own lanes at
  elaboration time, except here it's Control doing it at runtime, per job.
  The host never picks nonce ranges directly; it only ever supplies the
  block fields and, optionally, `ACTIVE_CORES_MASK`/`CORE_BASE_OFFSET`.
- **Result Arbiters (plural)** — every core ends in its own
  `DifficultyValidator` (`BitcoinMiningCore`'s `target` port, target from
  `NBitsDecoder` on the job's `bits`), so only winning digests ever leave a
  core. Hits are merged in a tree: cores are grouped `cores_per_arbiter` at a
  time under first-level `ResultBus` arbiters, which feed the top-level
  `ResultBus` that Control watches for `result_ready`. A `ResultBus` moves
  about one transfer every 3 cycles; checking difficulty inside each core
  keeps per-hash traffic off the arbiters, so any core count and grouping
  runs at full speed (see `performance_evaluation.md`).

### Job sequencing and draining

Control's job state machine is `idle -> loading -> running -> draining -> idle`:

- **loading** — `new_job` pulsed to the shared `Sha256Engine`; Control waits
  for `midstate_ready` to go low (reload started) then high again, and only
  then enables the active cores.
- **running** — until the first validated hit, `JOB_TIMEOUT`, or `ABORT_JOB`.
- **draining** — the pipeline stages only use `reset` to block a *new*
  start, they don't clear work in flight, so cores can't be flushed by reset.
  Instead Control stops every nonce iterator, discards any late hits, and
  waits until every core's `NonceTracker` FIFO is empty, no core validator
  holds a hit and every arbiter is idle for 8 cycles in a row. Only then is the `RESULT`
  (or timeout `JOB_NACK`) frame queued, so a host that receives `RESULT` can
  send the next `NEW_JOB` immediately.

## USART interface protocol

Framing is byte-oriented, with a fixed start-of-frame byte, a command byte,
an explicit big-endian length, and a checksum trailer. Register values are
big-endian; block header data is raw header bytes (see "Byte order"). The chip is a passive responder: it only
ever answers a request, it never speaks first except to ACK/NACK/report a
result.

### Frame format (both directions)

| Field | Size | Description |
|---|---|---|
| `SOF` | 1B | `0xAA` — start-of-frame marker |
| `CMD` | 1B | Command/response code, see tables below |
| `LEN` | 2B | Payload length in bytes, big-endian |
| `PAYLOAD` | `LEN` bytes | Command-specific body |
| `CHK` | 1B | XOR checksum over `CMD ‖ LEN ‖ PAYLOAD` |

A receiver that sees a bad `CHK` drops the frame silently and waits for the
next `SOF` — there is no automatic retransmit at this layer, the host is
expected to time out and resend. (So a `NEW_JOB` with a bad checksum gets
no `JOB_NACK`: the command byte itself can't be trusted.) A frame that stalls
mid-way for 20 character times is abandoned the same way, and frames with an
unknown `CMD` are dropped silently too.

### Byte order

Hashing always runs on the header bytes exactly as sent, so the double-SHA256
is the same either way. Only the difficulty interpretation depends on
`bitcoin_shortcuts`:

| | `bitcoin_shortcuts=True` (default) — real Bitcoin | `bitcoin_shortcuts=False` |
|---|---|---|
| `bits` field → nBits | little-endian, as serialised in a header | big-endian |
| digest meets target when | digest read **little-endian** ≤ target (block explorers show it byte-reversed, leading zeros first) | digest read big-endian ≤ target |

The round-61 early reject relies on the little-endian comparison: the
deciding top word of the digest is then `H7`, which is known 3 rounds before
the end.

### Host → Chip commands

| `CMD` | Name | `LEN` | Payload | Meaning |
|---|---|---|---|---|
| `0x01` | `NEW_JOB` | 76 | `header_first64bytes` (64B) ‖ `merkle_tail` (4B) ‖ `time` (4B) ‖ `bits` (4B) | Load a new block header to mine: the first 76 bytes of the serialised 80-byte header, as is. `bits` doubles as the `nBits` difficulty field — the chip derives the target from it, it is not sent twice. |
| `0x02` | `ABORT_JOB` | 0 | — | Stop the current job without waiting for a result; chip returns to idle. |
| `0x03` | `PING` | 0 | — | Liveness check; chip replies `PONG`. |
| `0x04` | `READ_REG` | 1 | `addr` (1B) | Read one register, see the register map below. |
| `0x05` | `WRITE_REG` | 5 | `addr` (1B) ‖ `value` (4B) | Write one register. Writing a read-only or out-of-range `addr` gets `REG_NACK`. |

### Chip → Host responses

| `CMD` | Name | `LEN` | Payload | Meaning |
|---|---|---|---|---|
| `0x81` | `JOB_ACK` | 0 | — | `NEW_JOB` accepted, search has started. |
| `0x82` | `JOB_NACK` | 1 | reason code | `NEW_JOB` rejected or job ended without a result: `0x01` malformed (wrong `LEN`), `0x02` busy (previous job still loading/running/draining), `0x03` timeout (`JOB_TIMEOUT` expired), `0x04` no active cores (`ACTIVE_CORES_MASK` selects none). |
| `0x83` | `RESULT` | 36 | `found_nonce` (4B) ‖ `found_digest` (32B) | A winning nonce was found. `found_nonce` is the 4 raw header bytes 76..79 to paste into the header; `found_digest` is the raw double-SHA256 output that met the target. Sent exactly once per job. |
| `0x84` | `PONG` | 0 | — | Reply to `PING`. |
| `0x85` | `REG_VALUE` | 5 | `addr` (1B) ‖ `value` (4B) | Reply to `READ_REG`. |
| `0x86` | `WRITE_ACK` | 1 | `addr` (1B) | Reply to a successful `WRITE_REG`. |
| `0x87` | `REG_NACK` | 1 | `addr` (1B) | `READ_REG`/`WRITE_REG` on a bad/read-only address, or a `WRITE_REG` while not idle (the payload carries no reason code; read `STATUS` to tell them apart). |

### Register map

All registers are 32-bit, zero-padded on the wire when narrower, and are
only safe to write while the chip is idle (writing mid-job gets `REG_NACK`
with a "busy" reason, same as `JOB_NACK`).

| `addr` | Name | Access | Description |
|---|---|---|---|
| `0x00` | `CHIP_ID` | RO | Fixed chip/version identifier, `0xB7C00001`. |
| `0x01` | `STATUS` | RO | `0`=idle, `1`=busy (loading/running, or draining after abort/timeout), `2`=result pending (hit found, draining before `RESULT` is sent), `3`=error (last job timed out; cleared by the next accepted `NEW_JOB`). |
| `0x02` | `NUM_CORES` | RO | Number of cores actually instantiated (fixed at synthesis time). |
| `0x03` | `ACTIVE_CORES_MASK` | RW | Bitmask of which cores participate in the next job (bit *i* = core *i*); disabled cores are excluded from both dispatch and the arbiter tree. Defaults to all-ones. |
| `0x04` | `CORE_BASE_OFFSET` | RW | Nonce value added to every core's own `lane_id`-derived offset — lets a host running several chips give each chip a disjoint nonce range without changing the job itself. |
| `0x05` | `JOB_TIMEOUT` | RW | Cycles to search before Control auto-aborts and reports `JOB_NACK` (reason "timeout") instead of waiting forever; `0` disables the timeout. |

This set covers what's needed to size/partition the search across cores and
across chips; it's expected to grow as the design firms up (e.g. per-core
enable feedback, error counters).

### Sequencing

1. Host sends `NEW_JOB`. Chip replies `JOB_ACK` (or `JOB_NACK` and stays
   idle).
2. Chip fans the job out internally and runs all engines until one reports
   a hit.
3. Chip sends `RESULT` once, then returns to idle and accepts the next
   `NEW_JOB`.
4. `ABORT_JOB` can be sent at any time after `JOB_ACK`; the chip stops all
   engines and does not send a `RESULT` for that job. `ABORT_JOB` has no
   reply of its own (and is a no-op while idle) — poll `STATUS` until it
   reads idle before sending the next `NEW_JOB`, otherwise it may get
   `JOB_NACK` busy while the cores drain.

`PING`/`READ_REG` are answered in every state, including while mining.

The line format is fixed at 8N1; the baud rate is `f_clk / clks_per_bit`,
chosen at instantiation to match whatever host UART this chip is paired
with.
