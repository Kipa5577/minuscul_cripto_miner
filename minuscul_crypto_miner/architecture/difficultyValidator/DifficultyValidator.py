import py4hw


'''
State machine of the component:
0 = idle       # waiting for a finished digest from the sha256 engine
1 = compare    # digest just latched, checked against Target this same cycle
2 = reporting  # a passing digest is held here until the collector fetches it

'''


class DifficultyValidator(py4hw.Logic):
    def __init__(self,parent,name,reset,Target,Digest_IN,Digest_Ready,Digest_Fetched,
                 Seed_IN,NBbytes_IN,Result_Ready,Seed_OUT,NBbytes_OUT,Digest_OUT,Result_Fetched,debug=False):
        super().__init__(parent,name)

        self.reset = self.addIn("reset",reset)
        self.Target = self.addIn("Target",Target) # 256b threshold: a digest is valid iff digest <= Target

        # From the sha256 engine
        self.Digest_IN = self.addIn("Digest_IN",Digest_IN)
        self.Digest_Ready = self.addIn("Digest_Ready",Digest_Ready)
        self.Digest_Fetched = self.addOut("Digest_Fetched",Digest_Fetched) # ack back to the engine, every cycle a digest is compared

        # Passthrough of the seed that produced Digest_IN
        self.Seed_IN = self.addIn("Seed_IN",Seed_IN)
        self.NBbytes_IN = self.addIn("NBbytes_IN",NBbytes_IN)

        # To whatever collects/broadcasts a found block. NBbytes_OUT matters
        # just as much as Seed_OUT/Digest_OUT: a seed integer alone is
        # ambiguous (e.g. seed=3 as 1 byte vs 2 bytes hashes to a different
        # digest), so anything downstream that needs to reconstruct or
        # re-verify the actual hashed bytes needs this too.
        self.Result_Ready = self.addOut("Result_Ready",Result_Ready)
        self.Seed_OUT = self.addOut("Seed_OUT",Seed_OUT)
        self.NBbytes_OUT = self.addOut("NBbytes_OUT",NBbytes_OUT)
        self.Digest_OUT = self.addOut("Digest_OUT",Digest_OUT)
        self.Result_Fetched = self.addIn("Result_Fetched",Result_Fetched)

        self.debug = debug
        self.state = 0
        self.seed = 0
        self.nbBytes = 0
        self.digest = 0

        # Guards against re-examining the same still-held Digest_Ready
        # assertion more than once: acking is a registered signal, so for a
        # cycle or two after we ack, the producer's Digest_Ready can still
        # read stale-high before it actually drops. Without this, every
        # rejected digest would get examined (and acked back to the engine)
        # multiple times.
        self.pending_examination = False

    def clock(self):

        digest_fetched = 0
        result_ready = 0

        ready_now = self.Digest_Ready.get() == 1
        if not ready_now:
            self.pending_examination = False

        match self.state:
            case 0:
                if ready_now and not self.pending_examination:
                    digest = self.Digest_IN.get()
                    target = self.Target.get()
                    digest_fetched = 1
                    self.pending_examination = True

                    if digest <= target:
                        self.digest = digest
                        self.seed = self.Seed_IN.get()
                        self.nbBytes = self.NBbytes_IN.get()
                        self.state = 2
                        if self.debug:
                            print(f"DifficultyValidator:idle[0]->reporting[2] digest={digest:064x} <= target={target:064x}")
                    elif self.debug:
                        print(f"DifficultyValidator: digest={digest:064x} > target={target:064x}, rejected")

            case 2:
                result_ready = 1
                if self.Result_Fetched.get() == 1:
                    self.state = 0
                    if self.debug:
                        print("DifficultyValidator:reporting[2]->idle[0]")

        self.Digest_Fetched.prepare(digest_fetched)
        self.Result_Ready.prepare(result_ready)
        self.Seed_OUT.prepare(self.seed)
        self.NBbytes_OUT.prepare(self.nbBytes)
        self.Digest_OUT.prepare(self.digest)
