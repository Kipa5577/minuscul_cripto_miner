import py4hw


'''
State machine of the component:
0 = idle          # waiting for stage 1 to present a finished digest
1 = dispatching   # driving digest1 onto stage 2's BusInterface, waiting for its ack
'''


class DigestBridge(py4hw.Logic):
    # Point-to-point adapter between the mining pipeline's two stages:
    # stage 1 (midstate-continuation core, OutputBuffer-style
    # digest/ready/fetched handshake) and stage 2 (a stock Sha256CoreV1
    # finisher, which expects a BusInterface-shaped drive on its InputBuffer
    # port). Fixed 1:1 pairing, so unlike Bus there is no slave-selection scan.
    def __init__(self,parent,name,Digest_IN,Digest_Ready_IN,Digest_Fetched_OUT,
                 Stage2BusInterface,debug=False):
        super().__init__(parent,name)

        self.Digest_IN = self.addIn("Digest_IN",Digest_IN)
        self.Digest_Ready_IN = self.addIn("Digest_Ready_IN",Digest_Ready_IN)
        self.Digest_Fetched_OUT = self.addOut("Digest_Fetched_OUT",Digest_Fetched_OUT)
        self.slave = self.addInterfaceSource("slave",Stage2BusInterface)

        self.debug = debug
        self.state = 0
        self.seed = 0

        # Guards against re-dispatching the same digest we just acked before
        # Digest_Ready_IN has had a chance to actually drop (registered
        # signal, so it reads stale-high for a cycle after the ack) --
        # same idiom as ResultBus's _settled / DifficultyValidator's
        # pending_examination.
        self._settled = True

    def clock(self):

        digest_fetched = 0

        if self.Digest_Ready_IN.get() == 0:
            self._settled = True

        match self.state:
            case 0:
                if self.Digest_Ready_IN.get() == 1 and self._settled and self.slave.Slave_Available.get() == 1:
                    self.seed = self.Digest_IN.get()
                    self.state = 1
                    if self.debug:
                        print(f"DigestBridge:idle[0]->dispatching[1] digest1={self.seed:#x}")

            case 1:
                if self.slave.Value_Fetched.get() == 1:
                    digest_fetched = 1
                    self._settled = False
                    self.state = 0
                    if self.debug:
                        print("DigestBridge:dispatching[1]->idle[0] (stage 2 accepted)")

        self.slave.SeedVal.prepare(self.seed)
        self.slave.NBbytes.prepare(32) # digest1 is always exactly 32 bytes
        self.slave.Value_Ready.prepare(1 if self.state == 1 else 0)
        self.slave.Seq.prepare(0) # unused: InputBuffer never reads Seq
        self.Digest_Fetched_OUT.prepare(digest_fetched)
