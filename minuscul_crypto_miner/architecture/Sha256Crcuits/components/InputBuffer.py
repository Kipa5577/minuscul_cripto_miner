import py4hw


'''
State machine of the component:
0 = idle       # empty, available to accept a new seed from the bus
1 = holding    # holding a captured seed, presenting it downstream until fetched
'''


class InputBuffer(py4hw.Logic):
    def __init__(self,parent,name,MainBusConnection,NBbytes_OUT,Data_Ready,Data_OUT,Data_Fetched,
                 Seed_Push_OUT,NBbytes_Push_OUT,Push_Valid_OUT,debug=False):
        super().__init__(parent,name)

        self.MainBusConnection = self.addInterfaceSink('MainBusConnection',MainBusConnection) #Used to connect with the bus that hands out seeds

        self.NBbytes_OUT = self.addOut("NBbytes_OUT",NBbytes_OUT) # length in bytes of Data_OUT, forwarded from the bus
        self.Data_Ready = self.addOut("Data_Ready",Data_Ready) # To tell the next block that there is new data ready
        self.Data_OUT = self.addOut("Data_OUT",Data_OUT) # This is the output for the value inside this buffer
        self.Data_Fetched = self.addIn("Data_Fetched",Data_Fetched) # This is the input recived from the next component in the sequance to tell this component that it has fetched the information

        # Direct line to OutputBuffer: 
        self.Seed_Push_OUT = self.addOut("Seed_Push_OUT",Seed_Push_OUT)
        self.NBbytes_Push_OUT = self.addOut("NBbytes_Push_OUT",NBbytes_Push_OUT)
        self.Push_Valid_OUT = self.addOut("Push_Valid_OUT",Push_Valid_OUT)

        self.debug = debug
        self.state = 0
        self.seedVal = 0
        self.nbBytes = 0

    def clock(self):

        slave_available = 0
        value_fetched = 0
        push_valid = 0

        match self.state:
            case 0:
                slave_available = 1
                if self.MainBusConnection.Value_Ready.get() == 1:
                    self.seedVal = self.MainBusConnection.SeedVal.get()
                    self.nbBytes = self.MainBusConnection.NBbytes.get()
                    value_fetched = 1
                    self.state = 1
                    if self.debug:
                        print(f"InputBuffer:idle[0]->holding[1] seed={self.seedVal:#x} NBbytes={self.nbBytes}")
            case 1:
                if self.Data_Fetched.get() == 1:
                    push_valid = 1
                    self.state = 0
                    if self.debug:
                        print(f"InputBuffer:holding[1]->idle[0] pushed seed={self.seedVal:#x} to OutputBuffer")

        # Bus-facing side:
        self.MainBusConnection.Slave_Available.prepare(slave_available)
        self.MainBusConnection.Value_Fetched.prepare(value_fetched)

        # Downstream-facing side: derived from self.state *after* the match
        # block above, not a separately-set local, so it drops to 0 on the
        # exact same cycle the ack is recognized instead of lingering one
        # cycle into the transition (which would let InputFormatter see a
        # stale "ready" and double-capture the same seed).
        self.Data_Ready.prepare(1 if self.state == 1 else 0)
        self.Data_OUT.prepare(self.seedVal)
        self.NBbytes_OUT.prepare(self.nbBytes)

        # OutputBuffer-facing side:
        self.Push_Valid_OUT.prepare(push_valid)
        self.Seed_Push_OUT.prepare(self.seedVal)
        self.NBbytes_Push_OUT.prepare(self.nbBytes)
