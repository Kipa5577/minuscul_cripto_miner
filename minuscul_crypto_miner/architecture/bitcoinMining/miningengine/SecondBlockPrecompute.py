import py4hw
from minuscul_crypto_miner.architecture.Sha256Crcuits.components.L2_Handler import sha256_round
from minuscul_crypto_miner.architecture.Sha256Crcuits.components.Sha256Compressor import K


'''
State machine of the component:
0 = wait      # midstate not ready (or being reloaded): state3_ready = 0
1 = rounds    # one compression round per cycle over W[0..2] of the second header block
2 = ready     # state3 held valid until midstate_ready drops (next job)

Bitcoin shortcut: the second 64-byte header block is
    merkle_tail | time | bits | nonce | padding
so its first 3 schedule words (W[0..2] = merkle_tail, time, bits) are fixed
for the whole job, and only W[3] (the nonce) changes per attempt. The first 3
compression rounds therefore give the same state for every nonce: computed
once here, per job, right after the midstate, every core's first
Sha256Compressor starts at round 3 from state3 instead of round 0.

state3_ready follows midstate_ready's low/high pattern (it drops as soon as
the midstate engine starts reloading and rises 3 cycles after the new
midstate is valid), so ChipControl's "seen low, then high" loading handshake
can watch it in place of midstate_ready.
'''


class SecondBlockPrecompute(py4hw.Logic):
    def __init__(self,parent,name,midstate,midstate_ready,merkle_tail,time_field,bits,
                 state3,state3_ready,debug=False):
        super().__init__(parent,name)

        self.midstate = self.addIn("midstate",midstate)
        self.midstate_ready = self.addIn("midstate_ready",midstate_ready)
        self.merkle_tail = self.addIn("merkle_tail",merkle_tail)
        self.time_field = self.addIn("time_field",time_field)
        self.bits = self.addIn("bits",bits)
        self.state3 = self.addOut("state3",state3)
        self.state3_ready = self.addOut("state3_ready",state3_ready)

        self.debug = debug
        self.state = 0
        self.t = 0
        self.regs = [0] * 8

    def clock(self):

        if self.midstate_ready.get() == 0:
            self.state = 0

        match self.state:
            case 0:
                if self.midstate_ready.get() == 1:
                    m = self.midstate.get()
                    self.regs = [(m >> (32 * (7 - j))) & 0xFFFFFFFF for j in range(8)]
                    self.t = 0
                    self.state = 1
            case 1:
                w = [self.merkle_tail.get(), self.time_field.get(), self.bits.get()][self.t] & 0xFFFFFFFF
                self.regs = list(sha256_round(*self.regs, K[self.t], w))
                self.t += 1
                if self.t == 3:
                    self.state = 2
                    if self.debug:
                        print("SecondBlockPrecompute:rounds[1]->ready[2]")

        value = 0
        for r in self.regs:
            value = (value << 32) | r
        self.state3.prepare(value)
        self.state3_ready.prepare(1 if self.state == 2 else 0)
