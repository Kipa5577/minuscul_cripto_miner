import py4hw


class BusInterface(py4hw.Interface): # this bus sends 512 bit words at once
    def __init__(self,parent,name):
        super().__init__(parent,name)

        self.SeedVal = self.addSourceToSink("SeedVal",512)          # master -> slave: the seed to hash
        self.NBbytes = self.addSourceToSink("NBbytes",8)            # master -> slave: length in bytes of SeedVal
        self.Value_Ready = self.addSourceToSink("Value_Ready",1)    # master -> slave: SeedVal is valid, addressed to this slave
        self.Seq = self.addSourceToSink("Seq",1)                    # master -> master-side consumer (e.g. Bus): flips every time a genuinely NEW value is presented
        self.Value_Fetched = self.addSinkToSource("Value_Fetched",1) # slave -> master: I latched SeedVal
        self.Slave_Available = self.addSinkToSource("Slave_Available",1) # slave -> master: I'm idle, I can accept a new seed


'''
State machine of the component:
0 = idle             # waiting for the seed generator to present a valid seed
1 = dispatching       # driving the seed to the selected slave, waiting for its ack
'''


class Bus(py4hw.Logic):
    def __init__(self,parent,name,InputBusInterface,OutputBusInterfaceList,debug=False): # This bus routes a single seed stream to the first available sha256 engine
        super().__init__(parent,name)

        self.master = self.addInterfaceSink("master",InputBusInterface)

        self.slaves = []
        for idx, slave_interface in enumerate(OutputBusInterfaceList):
            self.slaves.append(self.addInterfaceSource('slave_{}'.format(idx),slave_interface))

        self.debug = debug
        self.state = 0
        self.selected_slave = -1

        # Guards against dispatching the same still-outstanding master value
        # twice: with several slaves pulling in parallel, Bus can complete a
        # full dispatch round-trip (pick a slave, get its ack, go back to
        # idle) faster than the master can produce a genuinely new value --
        # Value_Ready alone can't tell "same request, not yet acked" from
        # "a new request", since it's just held high the whole time. Seq
        # flips only when the master truly has something new.
        self.last_dispatched_seq = None

    def find_available_slave(self):
        for idx, slave in enumerate(self.slaves):
            if slave.Slave_Available.get() == 1:
                return idx
        return None

    def clock(self):

        master_fetched = 0
        seed = self.master.SeedVal.get()
        nbbytes = self.master.NBbytes.get()

        match self.state:
            case 0:
                seq = self.master.Seq.get()
                if self.master.Value_Ready.get() == 1 and seq != self.last_dispatched_seq:
                    target = self.find_available_slave()
                    if target is not None:
                        # Select *and* drive the slave in this same cycle
                        # (Value_Ready below uses self.state/self.selected_slave
                        # as updated right here) instead of only deciding this
                        # cycle and driving the next -- saves a full cycle off
                        # every dispatch's latency.
                        self.selected_slave = target
                        self.last_dispatched_seq = seq
                        self.state = 1
                        if self.debug:
                            print(f"Bus:idle[0]->dispatching[1] (slave {target})")

            case 1:
                selected = self.slaves[self.selected_slave]
                if selected.Value_Fetched.get() == 1:
                    master_fetched = 1
                    self.state = 0
                    if self.debug:
                        print(f"Bus:dispatching[1]->idle[0] (slave {self.selected_slave} accepted)")

        for idx, slave in enumerate(self.slaves):
            slave.SeedVal.prepare(seed)
            slave.NBbytes.prepare(nbbytes)
            slave.Value_Ready.prepare(1 if (self.state == 1 and idx == self.selected_slave) else 0)

        self.master.Value_Fetched.prepare(master_fetched)


'''
State machine of the component:
0 = idle          # waiting for any engine to report a finished digest
1 = forwarding    # relaying the selected engine's result until it's fetched downstream
'''


class ResultBus(py4hw.Logic):
    # The mirror image of Bus: instead of one source fanning a seed stream
    # OUT to N sha256 engines, this merges N engines' finished (seed,
    # nbbytes, digest) results back IN to a single stream for whatever
    # consumes them next (e.g. DifficultyValidator). Same priority-scan
    # idiom as Bus (first engine with a result wins), just reversed.
    def __init__(self,parent,name,EngineOutputs,Seed_OUT,NBbytes_OUT,Digest_OUT,Digest_Ready,Digest_Fetched,debug=False):
        super().__init__(parent,name)

        # EngineOutputs: list of (seed_out, nbbytes_out, digest_out, digest_ready, digest_fetched) wire tuples,
        # one per Sha256CoreV1 instance (its own Seed_OUT/NBbytes_OUT/Digest_OUT/Digest_Ready/Digest_Fetched ports).
        self.seed_ins = []
        self.nbbytes_ins = []
        self.digest_ins = []
        self.digest_ready_ins = []
        self.digest_fetched_outs = []

        for idx, (seed_out, nbbytes_out, digest_out, digest_ready, digest_fetched) in enumerate(EngineOutputs):
            self.seed_ins.append(self.addIn(f'engine_{idx}_seed', seed_out))
            self.nbbytes_ins.append(self.addIn(f'engine_{idx}_nbbytes', nbbytes_out))
            self.digest_ins.append(self.addIn(f'engine_{idx}_digest', digest_out))
            self.digest_ready_ins.append(self.addIn(f'engine_{idx}_digest_ready', digest_ready))
            self.digest_fetched_outs.append(self.addOut(f'engine_{idx}_digest_fetched', digest_fetched))

        self.Seed_OUT = self.addOut("Seed_OUT",Seed_OUT)
        self.NBbytes_OUT = self.addOut("NBbytes_OUT",NBbytes_OUT)
        self.Digest_OUT = self.addOut("Digest_OUT",Digest_OUT)
        self.Digest_Ready = self.addOut("Digest_Ready",Digest_Ready)
        self.Digest_Fetched = self.addIn("Digest_Fetched",Digest_Fetched)

        self.debug = debug
        self.state = 0
        self.selected = -1
        self.seed = 0
        self.nbBytes = 0
        self.digest = 0

        # Guards against re-selecting an engine we just acked before it has
        # had a chance to see that ack and drop its own Digest_Ready: acking
        # is a registered signal, so for one cycle after forwarding_[1] acks
        # engine X, X's Digest_Ready still reads stale-high. Without this,
        # the very next idle[0] tick would pick X again for the same
        # already-consumed digest.
        self._settled = [True] * len(self.digest_ready_ins)

    def find_ready_engine(self):
        for idx, ready in enumerate(self.digest_ready_ins):
            if ready.get() == 1 and self._settled[idx]:
                return idx
        return None

    def clock(self):

        fetched_flags = [0] * len(self.digest_fetched_outs)

        for idx, ready in enumerate(self.digest_ready_ins):
            if ready.get() == 0:
                self._settled[idx] = True

        match self.state:
            case 0:
                target = self.find_ready_engine()
                if target is not None:
                    self.selected = target
                    self.seed = self.seed_ins[target].get()
                    self.nbBytes = self.nbbytes_ins[target].get()
                    self.digest = self.digest_ins[target].get()
                    self.state = 1
                    if self.debug:
                        print(f"ResultBus:idle[0]->forwarding[1] engine={target} seed={self.seed:#x}")

            case 1:
                if self.Digest_Fetched.get() == 1:
                    fetched_flags[self.selected] = 1
                    self._settled[self.selected] = False
                    self.state = 0
                    if self.debug:
                        print(f"ResultBus:forwarding[1]->idle[0] engine={self.selected} acked")

        for fetched_out, flag in zip(self.digest_fetched_outs, fetched_flags):
            fetched_out.prepare(flag)

        self.Digest_Ready.prepare(1 if self.state == 1 else 0)
        self.Seed_OUT.prepare(self.seed)
        self.NBbytes_OUT.prepare(self.nbBytes)
        self.Digest_OUT.prepare(self.digest)
