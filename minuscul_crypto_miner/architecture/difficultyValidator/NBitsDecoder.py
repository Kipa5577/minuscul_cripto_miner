import py4hw


class NBitsDecoder(py4hw.Logic):
    # Expands Bitcoin's compact 4-byte difficulty encoding into the 256-bit
    # threshold DifficultyValidator compares digests against.
    # nBits = [exponent:8][mantissa:24], target = mantissa * 256^(exponent-3).
    def __init__(self,parent,name,nBits,target,debug=False):
        super().__init__(parent,name)

        self.nBits = self.addIn("nBits",nBits)
        self.target = self.addOut("target",target)
        self.debug = debug

    def clock(self):
        n = self.nBits.get() & 0xFFFFFFFF
        exponent = (n>>24)&0xFF
        mantissa = n & 0xFFFFFF
        shift = 8*(exponent-3)

        if shift >= 0:
            target = (mantissa << shift) & ((1<<256)-1)
        else:
            target = mantissa >> (-shift)

        if self.debug:
            print(f"NBitsDecoder:nBits={n:#010x} exponent={exponent} mantissa={mantissa:#08x} target={target:064x}")

        self.target.prepare(target)
