import py4hw
from minuscul_crypto_miner.architecture.bitcoinMining.bitcoinminingchip.UsartInterface import ByteStreamInterface


'''
State machine of the component (asynchronous 8N1 receiver, LSB first):
0 = idle    # line high, waiting for the falling edge of a start bit
1 = start   # wait half a bit, re-check the line is still low (glitch filter)
2 = data    # sample each of the 8 data bits in the middle of its bit time
3 = stop    # sample the stop bit; 1 -> byte delivered, 0 -> framing error, byte dropped

The received byte is presented on the ByteStreamInterface (Valid held until
Ack). A new byte completing while the previous one is still unacknowledged
overwrites it and counts as an overrun -- UART has no flow control, so the
sink must keep up (ChipControl takes one byte per cycle, far faster than a
character time).

Cycle 0: before the host drives the line, rx reads its raw default 0, which
looks like a start bit; the half-bit re-check in start[1] rejects it.
'''


class UsartRx(py4hw.Logic):
    def __init__(self,parent,name,rx,ByteStreamOut:ByteStreamInterface,clks_per_bit,debug=False):
        super().__init__(parent,name)

        if clks_per_bit < 4:
            raise ValueError(f"clks_per_bit must be >= 4, got {clks_per_bit}")

        self.rx = self.addIn("rx",rx)
        self.out = self.addInterfaceSource("out",ByteStreamOut)

        self.clks_per_bit = clks_per_bit
        self.debug = debug
        self.state = 0
        self.cnt = 0
        self.bit = 0
        self.shift = 0

        self.data = 0
        self.valid = False

        # Error counters (simulation visibility only, no ports)
        self.frame_errors = 0
        self.overruns = 0

    def clock(self):

        line = self.rx.get() & 1

        if self.out.Ack.get() == 1:
            self.valid = False

        match self.state:
            case 0:
                if line == 0:
                    self.cnt = 0
                    self.state = 1
            case 1:
                self.cnt += 1
                if self.cnt == self.clks_per_bit // 2:
                    if line == 0:
                        self.cnt = 0
                        self.bit = 0
                        self.shift = 0
                        self.state = 2
                    else:
                        self.state = 0
            case 2:
                self.cnt += 1
                if self.cnt == self.clks_per_bit:
                    self.cnt = 0
                    self.shift |= line << self.bit
                    self.bit += 1
                    if self.bit == 8:
                        self.state = 3
            case 3:
                self.cnt += 1
                if self.cnt == self.clks_per_bit:
                    if line == 1:
                        if self.valid:
                            self.overruns += 1
                        self.data = self.shift
                        self.valid = True
                        if self.debug:
                            print(f"UsartRx:byte={self.data:#04x}")
                    else:
                        self.frame_errors += 1
                        if self.debug:
                            print("UsartRx:framing error, byte dropped")
                    self.state = 0

        self.out.Data.prepare(self.data)
        self.out.Valid.prepare(1 if self.valid else 0)
