import py4hw
from minuscul_crypto_miner.architecture.bitcoinMining.bitcoinminingchip.UsartInterface import ByteStreamInterface


'''
State machine of the component (asynchronous 8N1 transmitter, LSB first):
0 = idle    # line high; takes the next byte as soon as the source presents one
1 = start   # drive the start bit (0) for one bit time
2 = data    # drive the 8 data bits, LSB first, one bit time each
3 = stop    # drive the stop bit (1) for one bit time, then back to idle

Back-to-back bytes go out with no idle gap between the stop bit and the next
start bit.
'''


class UsartTx(py4hw.Logic):
    def __init__(self,parent,name,tx,ByteStreamIn:ByteStreamInterface,clks_per_bit,debug=False):
        super().__init__(parent,name)

        if clks_per_bit < 4:
            raise ValueError(f"clks_per_bit must be >= 4, got {clks_per_bit}")

        self.tx = self.addOut("tx",tx)
        self.inp = self.addInterfaceSink("inp",ByteStreamIn)

        self.clks_per_bit = clks_per_bit
        self.debug = debug
        self.state = 0
        self.cnt = 0
        self.bit = 0
        self.data = 0

    def clock(self):

        ack = 0

        match self.state:
            case 0:
                if self.inp.Valid.get() == 1:
                    self.data = self.inp.Data.get() & 0xFF
                    ack = 1
                    self.cnt = 0
                    self.state = 1
                    if self.debug:
                        print(f"UsartTx:byte={self.data:#04x}")
            case 1:
                self.cnt += 1
                if self.cnt == self.clks_per_bit:
                    self.cnt = 0
                    self.bit = 0
                    self.state = 2
            case 2:
                self.cnt += 1
                if self.cnt == self.clks_per_bit:
                    self.cnt = 0
                    self.bit += 1
                    if self.bit == 8:
                        self.state = 3
            case 3:
                self.cnt += 1
                if self.cnt == self.clks_per_bit:
                    self.state = 0

        match self.state:
            case 1:
                line = 0
            case 2:
                line = (self.data >> self.bit) & 1
            case _:
                line = 1

        self.tx.prepare(line)
        self.inp.Ack.prepare(ack)
