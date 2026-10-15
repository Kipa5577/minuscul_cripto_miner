import py4hw
from minuscul_crypto_miner.architecture.Sha256Crcuits.components.L1_Handler import ror
from minuscul_crypto_miner.architecture.Sha256Crcuits.components.L2_Handler import sha256_round


'''
State machine of the component:
0 = idle      # waiting for a block
1 = rounds    # up to rounds_per_cycle compression rounds per cycle, schedule computed on the fly
2 = emit      # H = H_init + (a..h) (or the early-reject marker) into the output
              # register, then straight into the next block if one is already
              # waiting (no idle cycle between blocks)

L1_Handler + L1_res_Buffer + L2_Handler fused into one stage: instead of
expanding the whole W[0..63] schedule up front into a 64-word buffer and
reading it back, a rolling 16-word window W[t-16..t-1] produces each W[t]
exactly when its round needs it -- the usual hardware SHA-256 structure.
No schedule buffer, no L1<->L2 handshakes, and the expansion can't become the
bottleneck. A block costs 64/rounds_per_cycle round cycles + 1 emit cycle.

Critical path note: with rounds_per_cycle > 1, W[t+2] depends on W[t]
(sigma1), so the schedule words are chained inside the cycle just like the
rounds are -- the same Fmax trade-off as L2_Handler_2x/4x's round unrolling.

The finished digest sits in its own output register (digest_ready held until
digest_fetched), so the next block's rounds run while the previous digest
waits to be taken. emit[2] only stalls if that register is still full.
After a fetch, digest_ready always drops for at least one cycle before the
next digest is presented: consumers such as DifficultyValidator/ResultBus
ignore a still-high ready until they've seen it low.

pad_digest_input=True: block_in is a 256-bit digest and the 512-bit block is
its fixed SHA-256 padding, digest || 0x80 || 0... || len=256 bits -- the
second hash of Bitcoin's double-SHA256 never needs the generic InputFormatter.

Bitcoin shortcuts (both optional, see BitcoinMiningChip):

  start_round=3 + state_init: the first 3 rounds of the second header block
  only depend on merkle_tail/time/bits (W[0..2]), which are fixed for a whole
  job, so their result (state_init) is computed once per job outside the core
  and every block starts at round 3. H_init is still the midstate (it is what
  the final addition adds back). W[0..2] still come from block_in: the
  schedule needs them for W[16..18].

  early_reject + reject_target: Bitcoin compares the final digest as a
  little-endian number, so its most significant 32 bits are byteswap(H7), and
  H7 = h_64 + H_init[7] where h_64 = e_61 (h is e delayed by 3 rounds). After
  61 rounds the top word is therefore already known: if it exceeds the
  target's top word the digest can't meet the target and the last 3 rounds
  are skipped. The block is then reported as REJECT_MARKER (all ones, fails
  every target) so the output handshake -- and the nonce pairing behind it --
  stays one output per block. Otherwise the rounds finish and the downstream
  validator does the full comparison.

Folding: work at the end of a block (the final H addition, or the round-61
check) is much shallower than a round, so when the last round cycle has a
spare round slot (fewer than rounds_per_cycle rounds left), it is done in that
same cycle instead of a cycle of its own. That is what turns the 3 skipped
rounds into a saved cycle: 61 rounds at 4 rounds/cycle = 15 full cycles + 1
cycle of 1 round + emit = 16 cycles instead of 17.
'''

K = [0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
     0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
     0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
     0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
     0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
     0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
     0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
     0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2]

ROUNDS_PER_CYCLE_OPTIONS = (1, 2, 4, 8)
EARLY_REJECT_ROUND = 61                 # e after 61 rounds == h after 64 rounds
REJECT_MARKER = (1 << 256) - 1          # fails every target, in either byte order


def bswap32(x):
    return int.from_bytes((x & 0xFFFFFFFF).to_bytes(4,'big'),'little')


class Sha256Compressor(py4hw.Logic):
    def __init__(self,parent,name,reset,block_in,block_ready,block_consumed,H_init,
                 digest_out,digest_ready,digest_fetched,
                 rounds_per_cycle=4,pad_digest_input=False,
                 start_round=0,state_init=None,early_reject=False,reject_target=None,debug=False):
        super().__init__(parent,name)

        if rounds_per_cycle not in ROUNDS_PER_CYCLE_OPTIONS:
            raise ValueError(f"rounds_per_cycle must be one of {ROUNDS_PER_CYCLE_OPTIONS}, got {rounds_per_cycle}")
        if start_round not in (0, 3):
            raise ValueError(f"start_round must be 0 or 3, got {start_round}")
        if (start_round != 0) != (state_init is not None):
            raise ValueError("start_round=3 needs state_init (and state_init needs start_round=3)")
        if early_reject != (reject_target is not None):
            raise ValueError("early_reject needs reject_target (and reject_target needs early_reject)")

        self.reset = self.addIn("reset",reset)
        self.block_in = self.addIn("block_in",block_in)             # 512b block, or 256b digest if pad_digest_input
        self.block_ready = self.addIn("block_ready",block_ready)
        self.block_consumed = self.addOut("block_consumed",block_consumed)  # one-cycle pulse: block latched
        self.H_init = self.addIn("H_init",H_init)                   # sampled when the block is latched
        self.state_init = self.addIn("state_init",state_init) if state_init is not None else None
        self.reject_target = self.addIn("reject_target",reject_target) if reject_target is not None else None
        self.digest_out = self.addOut("digest_out",digest_out)
        self.digest_ready = self.addOut("digest_ready",digest_ready)
        self.digest_fetched = self.addIn("digest_fetched",digest_fetched)

        self.rounds_per_cycle = rounds_per_cycle
        self.pad_digest_input = pad_digest_input
        self.start_round = start_round
        self.early_reject = early_reject
        self.debug = debug

        self.state = 0
        self.t = 0                  # next round index
        self.W = [0] * 16           # rolling window, W[t] lives at W[t % 16]
        self.H = [0] * 8            # chaining value this block started from
        self.regs = [0] * 8         # a..h
        self.checked = False        # round-61 early-reject check done for this block
        self.pending = None         # value waiting in emit[2] (None: final H not computed yet)

        self.out_digest = 0
        self.out_full = False
        self.last_early_reject = False  # last emitted value was the reject marker (for visualisation)
        self.early_rejects = 0          # counters, simulation visibility only
        self.blocks = 0

    def _try_load(self):
        if self.reset.get() == 1 or self.block_ready.get() == 0:
            return False

        block = self.block_in.get()
        if self.pad_digest_input:
            block = ((block & ((1 << 256) - 1)) << 256) | (0x80 << 248) | 256
        self.W = [(block >> (32 * (15 - j))) & 0xFFFFFFFF for j in range(16)]

        h_init = self.H_init.get()
        self.H = [(h_init >> (32 * (7 - j))) & 0xFFFFFFFF for j in range(8)]
        if self.state_init is not None:
            s = self.state_init.get()
            self.regs = [(s >> (32 * (7 - j))) & 0xFFFFFFFF for j in range(8)]
        else:
            self.regs = list(self.H)
        self.t = self.start_round
        self.checked = not self.early_reject
        self.pending = None
        self.state = 1
        self.blocks += 1
        if self.debug:
            print(f"Sha256Compressor[{self.name}]:block latched")
        return True

    def _schedule_word(self, t):
        if t < 16:
            return self.W[t]
        w15 = self.W[(t - 15) % 16]
        w2 = self.W[(t - 2) % 16]
        s0 = ror(w15,7,32) ^ ror(w15,18,32) ^ (w15 >> 3)
        s1 = ror(w2,17,32) ^ ror(w2,19,32) ^ (w2 >> 10)
        w = (self.W[t % 16] + s0 + self.W[(t - 7) % 16] + s1) & 0xFFFFFFFF  # W[t % 16] still holds W[t-16]
        self.W[t % 16] = w
        return w

    def _final_digest(self):
        digest = 0
        for hj, rj in zip(self.H, self.regs):
            digest = (digest << 32) | ((hj + rj) & 0xFFFFFFFF)
        return digest

    def _passes_early_check(self):
        # top 32 bits of the little-endian digest = byteswap(H7), H7 = e_61 + H_init[7]
        top_word = bswap32(self.regs[4] + self.H[7])
        return top_word <= (self.reject_target.get() >> 224)

    def _boundary(self):
        """End-of-segment work at t == 61 (early check) or t == 64 (final H).
        Returns True when the block is finished and its value is pending."""
        if not self.checked:
            self.checked = True
            if self._passes_early_check():
                return False
            self.pending = REJECT_MARKER
            self.early_rejects += 1
            return True
        self.pending = self._final_digest()
        return True

    def _emit(self, freed_now):
        """Write the pending value if the output register is free; returns True if written."""
        if self.out_full or freed_now:
            return False
        self.out_digest = self.pending
        self.out_full = True
        self.last_early_reject = self.pending == REJECT_MARKER
        if self.debug:
            print(f"Sha256Compressor[{self.name}]:emit {'REJECT' if self.last_early_reject else f'{self.out_digest:064x}'}")
        self.pending = None
        return True

    def clock(self):

        consumed = 0

        # Output register: free it on fetch; a value written in the same cycle
        # would keep digest_ready high with no low gap (see docstring).
        freed_now = False
        if self.out_full and self.digest_fetched.get() == 1:
            self.out_full = False
            freed_now = True

        finished = False
        match self.state:
            case 0:
                if self._try_load():
                    consumed = 1
            case 1:
                if not self.checked and self.t == EARLY_REJECT_ROUND:
                    # check left over from a cycle with no spare slot: evaluated
                    # from this cycle's starting registers, alongside round 61
                    finished = self._boundary()
                if not finished:
                    stop = EARLY_REJECT_ROUND if not self.checked else 64
                    n = min(self.rounds_per_cycle, stop - self.t)
                    a,b,c,d,e,f,g,h = self.regs
                    for _ in range(n):
                        a,b,c,d,e,f,g,h = sha256_round(a,b,c,d,e,f,g,h,K[self.t],self._schedule_word(self.t))
                        self.t += 1
                    self.regs = [a,b,c,d,e,f,g,h]
                    if self.t == stop and n < self.rounds_per_cycle:
                        finished = self._boundary()     # spare slot: fold it into this cycle
                    elif self.t == 64:
                        self.state = 2                  # final H in a cycle of its own
                if finished:
                    self.state = 2
                    if self._emit(freed_now):
                        self.state = 0
                        if self._try_load():
                            consumed = 1
            case 2:
                if self.pending is None:
                    self._boundary()
                if self._emit(freed_now):
                    self.state = 0
                    if self._try_load():
                        consumed = 1

        self.block_consumed.prepare(consumed)
        self.digest_out.prepare(self.out_digest)
        self.digest_ready.prepare(1 if self.out_full else 0)
