import py4hw


'''
State machine of the component:
0 = idle        # waiting for a finished digest from L2_Handler
1 = presenting  # holding a (seed, NBbytes, digest) triple until it's fetched

A FIFO was used because the Sha256CoreV1 can have multiple calculations in the pipeline so we want to correctly associate to each seed to the correct reuslt

'''


class OutputBuffer(py4hw.Logic):
    def __init__(self,parent,name,reset,
                 Seed_Push_IN,NBbytes_Push_IN,Push_Valid_IN,
                 Digest_IN,Digest_Valid_IN,Digest_Consumed_OUT,
                 Seed_OUT,NBbytes_OUT,Digest_OUT,Digest_Ready,Digest_Fetched,
                 debug=False):
        super().__init__(parent,name)

        self.reset = self.addIn("reset",reset)

        # From the InputBuffer to recive the seedOfThe next calculation
        self.Seed_Push_IN = self.addIn("Seed_Push_IN",Seed_Push_IN)
        self.NBbytes_Push_IN = self.addIn("NBbytes_Push_IN",NBbytes_Push_IN)
        self.Push_Valid_IN = self.addIn("Push_Valid_IN",Push_Valid_IN)

        # From L2_Handler
        self.Digest_IN = self.addIn("Digest_IN",Digest_IN)
        self.Digest_Valid_IN = self.addIn("Digest_Valid_IN",Digest_Valid_IN)
        self.Digest_Consumed_OUT = self.addOut("Digest_Consumed_OUT",Digest_Consumed_OUT) # ack back to L2_Handler

        # To the OutputBuss
        self.Seed_OUT = self.addOut("Seed_OUT",Seed_OUT)
        self.NBbytes_OUT = self.addOut("NBbytes_OUT",NBbytes_OUT)
        self.Digest_OUT = self.addOut("Digest_OUT",Digest_OUT)
        self.Digest_Ready = self.addOut("Digest_Ready",Digest_Ready)
        self.Digest_Fetched = self.addIn("Digest_Fetched",Digest_Fetched)

        self.debug = debug
        self.state = 0
        self.seed_fifo = []

        self.seed = 0
        self.nbBytes = 0
        self.digest = 0

    def clock(self):

        # The push side channel is independent of this component's own state machine
        if self.Push_Valid_IN.get() == 1:
            self.seed_fifo.append((self.Seed_Push_IN.get(), self.NBbytes_Push_IN.get()))
            if self.debug:
                print(f"OutputBuffer:pushed seed={self.seed_fifo[-1][0]:#x} NBbytes={self.seed_fifo[-1][1]} (fifo depth={len(self.seed_fifo)})")

        digest_consumed = 0

        match self.state:
            case 0:
                if self.Digest_Valid_IN.get() == 1:
                    digest_consumed = 1

                    if len(self.seed_fifo) == 0:
                        # A digest arrived with no matching seed pushed earlier
                        if self.debug:
                            print("OutputBuffer:WARNING digest arrived with empty seed FIFO, dropping it")
                    else:
                        self.seed, self.nbBytes = self.seed_fifo.pop(0)
                        self.digest = self.Digest_IN.get()
                        self.state = 1
                        if self.debug:
                            print(f"OutputBuffer:idle[0]->presenting[1] seed={self.seed:#x} digest={self.digest:064x}")

            case 1:
                if self.Digest_Fetched.get() == 1:
                    self.state = 0
                    if self.debug:
                        print("OutputBuffer:presenting[1]->idle[0]")

        self.Digest_Consumed_OUT.prepare(digest_consumed)
        self.Digest_Ready.prepare(1 if self.state == 1 else 0)
        self.Seed_OUT.prepare(self.seed)
        self.NBbytes_OUT.prepare(self.nbBytes)
        self.Digest_OUT.prepare(self.digest)
