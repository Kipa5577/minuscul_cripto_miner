import py4hw


# Drives the master side of a BusInterface directly against the input Sha256CoreV1's BusConnection

class Sender(py4hw.Logic):
    def __init__(self, parent, name, iface, seeds, debug=False):
        super().__init__(parent, name)
        self.iface = self.addInterfaceSource("iface", iface)
        self.seeds = list(seeds)  # list of (seed_value, nbbytes)
        self.idx = 0
        self.presenting = False
        self.debug = debug

    def clock(self):
        if not self.presenting and self.idx < len(self.seeds):
            self.presenting = True
            if self.debug:
                print(f"Sender:presenting seed #{self.idx} = {self.seeds[self.idx]}")

        if self.presenting:
            seed, nbbytes = self.seeds[self.idx]
            self.iface.SeedVal.prepare(seed)
            self.iface.NBbytes.prepare(nbbytes)
        else:
            self.iface.SeedVal.prepare(0)
            self.iface.NBbytes.prepare(0)

        self.iface.Value_Ready.prepare(1 if self.presenting else 0)

        if self.presenting and self.iface.Value_Fetched.get() == 1:
            self.presenting = False
            self.idx += 1


# A consumer: only capture once per Digest_Ready assertion 
class Collector(py4hw.Logic):
    def __init__(self, parent, name, seed_out, nbbytes_out, digest_out, digest_ready, digest_fetched):
        super().__init__(parent, name)
        self.seed_out = self.addIn('seed_out', seed_out)
        self.nbbytes_out = self.addIn('nbbytes_out', nbbytes_out)
        self.digest_out = self.addIn('digest_out', digest_out)
        self.digest_ready = self.addIn('digest_ready', digest_ready)
        self.digest_fetched = self.addOut('digest_fetched', digest_fetched)
        self.captured = []
        self.pending_ack = False

    def clock(self):
        fetched = 0
        if self.digest_ready.get() == 1 and not self.pending_ack:
            self.captured.append((self.seed_out.get(), self.nbbytes_out.get(), self.digest_out.get()))
            fetched = 1
            self.pending_ack = True
        elif self.digest_ready.get() == 0:
            self.pending_ack = False
        self.digest_fetched.prepare(fetched)
