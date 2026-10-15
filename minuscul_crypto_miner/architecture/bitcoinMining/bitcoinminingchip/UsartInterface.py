import py4hw


class ByteStreamInterface(py4hw.Interface):
    # Standard byte stream between the USART and its user (ChipControl), in
    # both directions:
    #   source holds Data/Valid until it sees Ack, then moves to the next byte;
    #   sink pulses Ack for exactly one cycle per byte it takes.
    # Because Ack is a registered signal, the source's Valid stays high for
    # one stale cycle after the Ack pulse -- a sink must ignore Valid until it
    # has seen it go low again (same _settled/pending guard idiom as
    # ResultBus/DifficultyValidator). UsartTx gets this for free by being busy
    # for a whole character time after taking a byte.
    def __init__(self,parent,name):
        super().__init__(parent,name)

        self.Data = self.addSourceToSink("Data",8)    # source -> sink: the byte
        self.Valid = self.addSourceToSink("Valid",1)  # source -> sink: Data holds a byte not yet taken
        self.Ack = self.addSinkToSource("Ack",1)      # sink -> source: one-cycle pulse, byte taken
