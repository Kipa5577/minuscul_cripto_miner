import py4hw


# Once digest1 is double-hashed by the finisher Sha256CoreV1, the digest
# alone no longer reveals which nonce produced it (Sha256CoreV1's own
# Seed_OUT would report digest1, not the nonce). This tracks nonces in a
# side FIFO, pushed the moment bitcoin_Nonce_iterator's block is consumed,
# so a winning digest can still be reported against the nonce that made it.
#
# nonce_out continuously exposes the FIFO head (not just while presenting a
# just-popped value) and the head is popped on digest_fetched_in, not on
# digest_ready_in. This matters because ResultBus reads nonce_out and
# digest_out in the *same* cycle it first observes digest_ready_in==1 --
# any reactive "pop on digest_ready, then prepare()" scheme puts nonce_out
# one full clock() sweep behind what ResultBus samples that same cycle
# (prepare() only becomes visible to other clock()-based readers on the
# *next* sweep), pairing every digest with the *previous* nonce. Exposing
# the head continuously means it is already stable and correct before
# digest_ready_in ever turns visible, with no reaction lag.
class NonceTracker(py4hw.Logic):
    def __init__(self,parent,name,nonce_push_in,push_valid_in,
                 digest_ready_in,digest_fetched_in,nonce_out,debug=False):
        super().__init__(parent,name)

        self.nonce_push_in = self.addIn("nonce_push_in",nonce_push_in)
        self.push_valid_in = self.addIn("push_valid_in",push_valid_in)
        self.digest_ready_in = self.addIn("digest_ready_in",digest_ready_in)
        self.digest_fetched_in = self.addIn("digest_fetched_in",digest_fetched_in)
        self.nonce_out = self.addOut("nonce_out",nonce_out)

        self.debug = debug
        self.nonce_fifo = []

    def clock(self):

        if self.push_valid_in.get() == 1:
            self.nonce_fifo.append(self.nonce_push_in.get())
            if self.debug:
                print(f"NonceTracker:pushed nonce={self.nonce_fifo[-1]:#x} (fifo depth={len(self.nonce_fifo)})")

        if self.digest_fetched_in.get() == 1:
            if len(self.nonce_fifo) == 0:
                if self.debug:
                    print("NonceTracker:WARNING digest fetched with empty nonce FIFO")
            else:
                popped = self.nonce_fifo.pop(0)
                if self.debug:
                    print(f"NonceTracker:popped nonce={popped:#x} (fifo depth={len(self.nonce_fifo)})")

        self.nonce_out.prepare(self.nonce_fifo[0] if len(self.nonce_fifo) > 0 else 0)
