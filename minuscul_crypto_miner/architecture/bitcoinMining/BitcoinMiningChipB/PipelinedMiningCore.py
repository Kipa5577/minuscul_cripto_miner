import py4hw
from minuscul_crypto_miner.architecture.Sha256Crcuits.components.L1_Handler import ror
from minuscul_crypto_miner.architecture.Sha256Crcuits.components.L2_Handler import sha256_round
from minuscul_crypto_miner.architecture.Sha256Crcuits.components.Sha256Compressor import K


'''
Fully pipelined Bitcoin mining core: one double-SHA256 per clock cycle.

Instead of looping one block through a compressor 16 times (chip A's
Sha256Compressor), the 125 rounds of a nonce attempt are unrolled into a
chain of pipeline stages, each with its own round logic and its own
registers (a..h + the 16-word schedule window + the nonce). Every cycle each
attempt moves one stage forward and a new nonce enters stage 0, so once the
pipeline is full one double-hash completes per cycle, with no handshakes and
no control state at all:

    nonce counter -> [hash 1: rounds 3..63, H0 = midstate, starts from state3]
                  -> final add + fixed 32-byte padding (wiring)
                  -> [hash 2: rounds 0..63, H0 = standard IV]
                  -> final add + little-endian target compare -> hit FIFO

rounds_per_stage (1/2/4) sets how many rounds each stage chains: it does not
change throughput (always 1 hash/cycle), only the pipeline depth (latency,
pipeline registers) against the logic per stage (Fmax):
    rounds_per_stage=1: 127 stages, 2: 64 stages, 4: 33 stages.
End-of-hash work (final addition / compare) is folded into the last round
stage when it has a spare round slot, same rule as Sha256Compressor.

The first 3 rounds of hash 1 come precomputed per job (state3, see
SecondBlockPrecompute); the job fields W[0..2] still enter the schedule
window, it needs them for W[16..18]. The nonce travels down the pipeline
alongside its data, so a hit is reported with its own nonce directly -- no
NonceTracker. The comparison is Bitcoin's: the digest read little-endian <=
target.

Hits go into a small FIFO (hit_fifo_depth) presented on the usual
(nonce_as_seed, nbbytes, digest, ready, fetched) result port; after each
fetch ready drops for one cycle before the next hit is shown (ResultBus
ignores a still-high ready until it has seen it low). A hit arriving while
the FIFO is full is dropped and counted (dropped_hits) -- a real-difficulty
hit is 1 in ~2^32+ attempts, and Control only ever reports the first hit of
a job anyway. Round-61 early reject buys no throughput here (one attempt per
cycle regardless), so all 64 rounds of hash 2 are kept: the RESULT frame
needs the full digest.

Nonce generation: when enable rises the counter loads nonce_start, then
injects one nonce per cycle, stepping by nonce_stride, until enable drops.
The attempts already inside keep flowing out; idle_out is 1 once the pipeline
and the hit FIFO are both empty (ChipControl's drain waits for it).
'''

IV = [0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19]
ROUNDS_PER_STAGE_OPTIONS = (1, 2, 4)


def _words(value, n):
    return [(value >> (32 * (n - 1 - j))) & 0xFFFFFFFF for j in range(n)]


def build_stage_plan(rounds_per_stage):
    """List of stages, each a list of operations: ('r', n) = n rounds,
    'f1' = hash-1 final add + padding into hash 2, 'f2' = hash-2 final add + compare."""
    stages = []

    def add_rounds(total):
        full, rem = divmod(total, rounds_per_stage)
        stages.extend([[('r', rounds_per_stage)] for _ in range(full)])
        if rem:
            stages.append([('r', rem)])
        return rem

    for total, finish in ((61, 'f1'), (64, 'f2')):
        if add_rounds(total):
            stages[-1].append(finish)   # spare round slot: fold the cheap end-of-hash work in
        else:
            stages.append([finish])
    return stages


class _Attempt:
    """One nonce attempt's pipeline registers."""
    __slots__ = ('nonce', 'regs', 'W', 't', 'digest', 'hit')

    def __init__(self, nonce, regs, W):
        self.nonce = nonce
        self.regs = regs
        self.W = W
        self.t = 3
        self.digest = 0
        self.hit = False


class PipelinedMiningCore(py4hw.Logic):
    def __init__(self,parent,name,reset,enable,midstate,state3,
                 merkle_tail,time_field,bits,nonce_start,nonce_stride,target,
                 nonce_as_seed_out,nbbytes_out,digest_out,digest_ready,digest_fetched,idle_out,
                 rounds_per_stage=1,hit_fifo_depth=4,debug=False):
        super().__init__(parent,name)

        if rounds_per_stage not in ROUNDS_PER_STAGE_OPTIONS:
            raise ValueError(f"rounds_per_stage must be one of {ROUNDS_PER_STAGE_OPTIONS}, got {rounds_per_stage}")

        self.reset = self.addIn("reset",reset)
        self.enable = self.addIn("enable",enable)
        self.midstate = self.addIn("midstate",midstate)
        self.state3 = self.addIn("state3",state3)
        self.merkle_tail = self.addIn("merkle_tail",merkle_tail)
        self.time_field = self.addIn("time_field",time_field)
        self.bits = self.addIn("bits",bits)
        self.nonce_start = self.addIn("nonce_start",nonce_start)
        self.nonce_stride = self.addIn("nonce_stride",nonce_stride)
        self.target = self.addIn("target",target)
        self.nonce_as_seed_out = self.addOut("nonce_as_seed_out",nonce_as_seed_out)
        self.nbbytes_out = self.addOut("nbbytes_out",nbbytes_out)
        self.digest_out = self.addOut("digest_out",digest_out)
        self.digest_ready = self.addOut("digest_ready",digest_ready)
        self.digest_fetched = self.addIn("digest_fetched",digest_fetched)
        self.idle_out = self.addOut("idle_out",idle_out)

        self.rounds_per_stage = rounds_per_stage
        self.hit_fifo_depth = hit_fifo_depth
        self.debug = debug

        self.stages = build_stage_plan(rounds_per_stage)
        self.depth = len(self.stages)
        self.pipe = [None] * self.depth     # pipe[i]: the attempt that has just gone through stage i
        self.in_flight = 0

        self.running = False
        self.next_nonce = 0
        self.hits = []                      # hit FIFO: (nonce, digest)
        self.gap = False                    # force ready low for one cycle after a fetch

        # counters, simulation visibility only
        self.hashes = 0
        self.hit_count = 0
        self.dropped_hits = 0

    def _schedule_word(self, a, t):
        W = a.W
        if t < 16:
            return W[t]
        w15 = W[(t - 15) % 16]
        w2 = W[(t - 2) % 16]
        s0 = ror(w15,7,32) ^ ror(w15,18,32) ^ (w15 >> 3)
        s1 = ror(w2,17,32) ^ ror(w2,19,32) ^ (w2 >> 10)
        w = (W[t % 16] + s0 + W[(t - 7) % 16] + s1) & 0xFFFFFFFF
        W[t % 16] = w
        return w

    def _apply(self, ops, a):
        for op in ops:
            if op == 'f1':
                digest1 = [(h + r) & 0xFFFFFFFF for h, r in zip(_words(self.midstate.get(), 8), a.regs)]
                a.W = digest1 + [0x80000000, 0, 0, 0, 0, 0, 0, 256]   # fixed padding of a 32-byte message
                a.regs = list(IV)
                a.t = 0
            elif op == 'f2':
                words = [(h + r) & 0xFFFFFFFF for h, r in zip(IV, a.regs)]
                digest = 0
                for w in words:
                    digest = (digest << 32) | w
                a.digest = digest
                a.hit = int.from_bytes(digest.to_bytes(32,'big'),'little') <= self.target.get()
            else:
                regs = a.regs
                for _ in range(op[1]):
                    regs = sha256_round(*regs, K[a.t], self._schedule_word(a, a.t))
                    a.t += 1
                a.regs = list(regs)

    def _new_attempt(self):
        nonce = self.next_nonce
        self.next_nonce = (self.next_nonce + self.nonce_stride.get()) & 0xFFFFFFFF
        W = [self.merkle_tail.get() & 0xFFFFFFFF, self.time_field.get() & 0xFFFFFFFF,
             self.bits.get() & 0xFFFFFFFF, nonce,
             0x80000000, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 640]   # second header block padding (640-bit header)
        return _Attempt(nonce, _words(self.state3.get(), 8), W)

    def clock(self):

        # --- hit FIFO: pop on fetch, with a one-cycle ready gap after it -----
        self.gap = False
        if self.hits and self.digest_fetched.get() == 1:
            self.hits.pop(0)
            self.gap = True

        # --- nonce counter --------------------------------------------------
        enabled = self.enable.get() == 1 and self.reset.get() == 0
        if enabled and not self.running:
            self.next_nonce = self.nonce_start.get() & 0xFFFFFFFF
        self.running = enabled

        # --- advance the pipeline one stage (skipped entirely while empty) ---
        if self.in_flight or enabled:
            leaving = self.pipe[-1]
            if leaving is not None:
                self.in_flight -= 1
                self.hashes += 1
                if leaving.hit:
                    self.hit_count += 1
                    if len(self.hits) < self.hit_fifo_depth:
                        self.hits.append((leaving.nonce, leaving.digest))
                        if self.debug:
                            print(f"PipelinedMiningCore[{self.name}]:hit nonce={leaving.nonce:#x}")
                    else:
                        self.dropped_hits += 1

            for i in range(self.depth - 1, 0, -1):
                a = self.pipe[i - 1]
                if a is not None:
                    self._apply(self.stages[i], a)
                self.pipe[i] = a

            a = self._new_attempt() if enabled else None
            if a is not None:
                self._apply(self.stages[0], a)
                self.in_flight += 1
            self.pipe[0] = a

        head = self.hits[0] if self.hits else (0, 0)
        self.nonce_as_seed_out.prepare(head[0])
        self.nbbytes_out.prepare(4)
        self.digest_out.prepare(head[1])
        self.digest_ready.prepare(1 if self.hits and not self.gap else 0)
        self.idle_out.prepare(1 if self.in_flight == 0 and not self.hits else 0)
