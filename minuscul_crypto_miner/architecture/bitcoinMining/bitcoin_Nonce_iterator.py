import py4hw


'''
State machine of the component:
0 = reset    # hold nonce at lane_id while reset is asserted
1 = active   # continuously presents the already-padded 512b block; advances
             # to the next nonce the instant block_consumed is seen, in the
             # same cycle -- no artificial gap before the next block is ready.

Block layout (matches SHA-256's own padding rules for an 80-byte Bitcoin
header split into two 64-byte blocks -- this is the *second* block, so its
length field must be the *whole* header's bit length (640), not this block's
own 16 real bytes):

    byte  0- 3  merkle_tail  (last 4 bytes of the merkle root)
    byte  4- 7  time
    byte  8-11  bits
    byte 12-15  nonce                     <- the only thing that changes here
    byte    16  0x80                      (padding marker)
    byte 17-55  0x00                      (zero padding)
    byte 56-63  0x0000000000000280        (big-endian 640, the *original*
                                            80-byte/640-bit header length)

Unlike InputFormatter, this layout is fixed and known at compile time, so it
is built directly rather than through InputFormatter's generic variable-
length padding loop -- see the "Key correctness point" in the plan for why
InputFormatter can't be reused for this block.
'''


class bitcoin_Nonce_iterator(py4hw.Logic):
    # Per-lane nonce-search driver: replaces seedGenerator's role for the
    # mining pipeline's first (midstate-continuation) stage. Drives
    # L1_Handler's initInput/inputReady/inputConsumed ports directly --
    # there is no InputBuffer/InputFormatter/Bus in front of it, since each
    # lane owns its iterator outright and no dispatch/arbitration is needed.
    def __init__(self,parent,name,reset,start_enable,merkle_tail,time_field,bits,
                 block_out,block_ready,block_consumed,nonce_out,lane_id=0,stride=1,debug=False):
        super().__init__(parent,name)

        self.reset = self.addIn("reset",reset)
        # Gates the very first activation on Sha256Engine's midstate_ready
        # (wired straight from it, not through any intermediate clock()-based
        # latch): midstate_ready's own uninitialized-wire default is 0
        # ("not ready"), which is already the safe state, so this is race-free
        # at cycle 0. A latch driving an active-high "held" signal instead
        # would NOT be safe -- see the "Key correctness point" addendum in
        # the plan: a clock()-based signal's prepared value only becomes
        # visible to other clock()-based readers starting the *next* sweep,
        # so at cycle 0 every reader still observes the wire's raw default
        # (0) regardless of what the latch intends to drive, and 0 happens to
        # mean "go" in this codebase's reset convention -- exactly backwards
        # for a "hold until ready" gate.
        self.start_enable = self.addIn("start_enable",start_enable)
        self.merkle_tail = self.addIn("merkle_tail",merkle_tail)
        self.time_field = self.addIn("time_field",time_field)
        self.bits = self.addIn("bits",bits)
        self.block_out = self.addOut("block_out",block_out)
        self.block_ready = self.addOut("block_ready",block_ready)
        self.block_consumed = self.addIn("block_consumed",block_consumed)
        # The nonce currently embedded in block_out, exposed separately so a
        # downstream tracker can pair a winning digest back to the nonce that
        # produced it (block_out alone isn't enough -- once double-SHA256'd,
        # the nonce isn't recoverable from the digest).
        self.nonce_out = self.addOut("nonce_out",nonce_out)

        self.lane_id = lane_id & 0xFFFFFFFF
        self.stride = stride

        self.debug = debug
        self.state = 0
        self.nonce = self.lane_id
        self.block = 0

        if self.debug:
            print(f"bitcoin_Nonce_iterator created lane_id={lane_id} stride={stride}")

    def _build_block(self):
        merkle_tail = self.merkle_tail.get() & 0xFFFFFFFF
        time_field = self.time_field.get() & 0xFFFFFFFF
        bits = self.bits.get() & 0xFFFFFFFF
        nonce = self.nonce & 0xFFFFFFFF
        self.block = (merkle_tail<<480)|(time_field<<448)|(bits<<416)|(nonce<<384)|(0x80<<376)|640

    def _advance(self):
        self.nonce = (self.nonce + self.stride) & 0xFFFFFFFF
        self._build_block()

    def clock(self):

        match self.state:
            case 0:
                self.nonce = self.lane_id
                if self.reset.get() == 0 and self.start_enable.get() == 1:
                    self._build_block()
                    self.state = 1
                    if self.debug:
                        print(f"bitcoin_Nonce_iterator:reset[0]->active[1] nonce={self.nonce:#x}")
            case 1:
                if self.block_consumed.get() == 1:
                    self._advance()
                    if self.debug:
                        print(f"bitcoin_Nonce_iterator:active[1] nonce={self.nonce:#x}")

        self.block_out.prepare(self.block)
        self.block_ready.prepare(1 if self.state == 1 else 0)
        self.nonce_out.prepare(self.nonce)
