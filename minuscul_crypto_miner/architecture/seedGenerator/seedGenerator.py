import py4hw

'''
State machine of the component:
0 = reset    # hold the seed at 0 while reset is asserted
1 = active   # continuously presents seedVal/NBbytes; advances to a fresh
             # value (and flips Seq) the instant Value_Fetched is seen, in
             # the same cycle -- no artificial gap before the next value is
             # ready. Safe because Bus refuses to dispatch the same Seq
             # twice, so nothing here needs to "prove" a value is new by
             # briefly dropping Value_Ready.
'''


class seedGenerator(py4hw.Logic): # This seed generator is mean to generate a seed of 512 bits and a NBbytes value
    def __init__(self, parent, instanceName,reset,BusInterface,debug=False):
        super().__init__(parent, instanceName)

        self.reset = self.addIn("Reset",reset)
        self.BusInterface = self.addInterfaceSource("BusInterface",BusInterface)

        self.debug = debug
        self.state = 0
        self.seedVal = 0
        self.NBbytes = 0
        self.seq = 0

        if self.debug:
            print("seedGenerator created")

    def _advance(self):
        self.seedVal = (self.seedVal + 1) & ((1 << 512) - 1)
        self.NBbytes = max(1, (self.seedVal.bit_length() + 7) // 8)
        self.seq ^= 1

    def clock(self):

        match self.state:
            case 0:
                self.seedVal = 0
                self.NBbytes = 0
                if self.reset.get() == 0:
                    self._advance()
                    self.state = 1
                    if self.debug:
                        print(f"seedGenerator:reset[0]->active[1] seed={self.seedVal:#x} NBbytes={self.NBbytes}")
            case 1:
                if self.BusInterface.Value_Fetched.get() == 1:
                    self._advance()
                    if self.debug:
                        print(f"seedGenerator:active[1] seed={self.seedVal:#x} NBbytes={self.NBbytes}")

        value_ready = 1 if self.state == 1 else 0
        self.BusInterface.SeedVal.prepare(self.seedVal)
        self.BusInterface.NBbytes.prepare(self.NBbytes)
        self.BusInterface.Value_Ready.prepare(value_ready)
        self.BusInterface.Seq.prepare(self.seq)
