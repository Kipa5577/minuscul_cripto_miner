import py4hw
from minuscul_crypto_miner.architecture.bitcoinMining.bitcoinminingchip import protocol as P


# Host side of BitcoinMiningChip's UART link, shared by the testbench,
# benchmark and animation. It serialises/deserialises the 8N1 line itself,
# independently of the chip's UsartRx/UsartTx, so the line-level encoding is
# really checked rather than tested against itself.

EASY_NBITS = 0x20100000   # target = 2**252, ~1 hit every 16 hashes
HARD_NBITS = 0x1d00ffff   # Bitcoin's genesis difficulty: never hit in simulation

CMD_NAMES = {
    P.CMD_NEW_JOB: "NEW_JOB", P.CMD_ABORT_JOB: "ABORT_JOB", P.CMD_PING: "PING",
    P.CMD_READ_REG: "READ_REG", P.CMD_WRITE_REG: "WRITE_REG",
    P.RSP_JOB_ACK: "JOB_ACK", P.RSP_JOB_NACK: "JOB_NACK", P.RSP_RESULT: "RESULT",
    P.RSP_PONG: "PONG", P.RSP_REG_VALUE: "REG_VALUE", P.RSP_WRITE_ACK: "WRITE_ACK",
    P.RSP_REG_NACK: "REG_NACK",
}


def make_header(seed, nbits, bitcoin_byte_order=True):
    # 80-byte header whose bits field (bytes 72..75) is the nBits the chip
    # derives its target from; the nonce (bytes 76..79) is filled by the chip.
    # bitcoin_byte_order: nBits serialised little-endian, as in a real header
    # (matches BitcoinMiningChip(bitcoin_shortcuts=True), the default).
    header = bytearray((i*seed + 3) % 256 for i in range(80))
    header[72:76] = nbits.to_bytes(4,'little' if bitcoin_byte_order else 'big')
    return bytes(header)


def target_of(nbits):
    return (nbits & 0xFFFFFF) << (8*((nbits >> 24) - 3))


def meets_target(digest_bytes, nbits, bitcoin_byte_order=True):
    # Bitcoin compares the digest as a little-endian number
    return int.from_bytes(digest_bytes,'little' if bitcoin_byte_order else 'big') <= target_of(nbits)


def frame_cycles(n_payload_bytes, clks_per_bit):
    # SOF + CMD + LEN(2) + payload + CHK, 10 line bits per byte (8N1)
    return (5 + n_payload_bytes) * 10 * clks_per_bit


class HostUart(py4hw.Logic):
    # Host-side 8N1 UART + frame decoder: drives the chip's uart_rx line from
    # a queue of bytes, decodes every frame the chip sends back on uart_tx.
    # frames: list of (cmd, payload, checksum_ok); frame_cycles: the cycle
    # count at which each one was fully received.
    def __init__(self,parent,name,to_chip,from_chip,clks_per_bit):
        super().__init__(parent,name)
        self.to_chip = self.addOut('to_chip',to_chip)
        self.from_chip = self.addIn('from_chip',from_chip)
        self.cpb = clks_per_bit

        self.cycle = 0
        self.tx_levels = []

        self.rx_state = 0
        self.rx_cnt = 0
        self.rx_bits = []
        self.rx_bytes = []
        self.frames = []
        self.frame_cycles = []

    def send(self,data):
        for b in data:
            levels = [0] + [(b >> i) & 1 for i in range(8)] + [1]
            for level in levels:
                self.tx_levels += [level] * self.cpb

    def sending(self):
        return len(self.tx_levels) > 0

    def _decode_frames(self):
        while self.rx_bytes:
            if self.rx_bytes[0] != P.SOF:
                self.rx_bytes.pop(0)
                continue
            if len(self.rx_bytes) < 4:
                return
            length = (self.rx_bytes[2] << 8) | self.rx_bytes[3]
            if len(self.rx_bytes) < 5 + length:
                return
            cmd = self.rx_bytes[1]
            payload = bytes(self.rx_bytes[4:4+length])
            chk = self.rx_bytes[4+length]
            del self.rx_bytes[:5+length]
            self.frames.append((cmd, payload, chk == P.checksum(cmd,payload)))
            self.frame_cycles.append(self.cycle)

    def clock(self):
        self.cycle += 1
        self.to_chip.prepare(self.tx_levels.pop(0) if self.tx_levels else 1)

        line = self.from_chip.get()
        match self.rx_state:
            case 0:
                if line == 0:
                    self.rx_state = 1
                    self.rx_cnt = 0
                    self.rx_bits = []
            case 1:
                self.rx_cnt += 1
                # sample start bit at half a bit, then every full bit
                if self.rx_cnt == self.cpb // 2 + self.cpb * len(self.rx_bits):
                    self.rx_bits.append(line)
                    if self.rx_bits == [1]:
                        # not a start bit after all (line's cycle-0 default is 0)
                        self.rx_state = 0
                    elif len(self.rx_bits) == 10:
                        start, *data, stop = self.rx_bits
                        assert start == 0 and stop == 1, f"host saw bad UART character {self.rx_bits}"
                        self.rx_bytes.append(sum(bit << i for i, bit in enumerate(data)))
                        self.rx_state = 0
                        self._decode_frames()
