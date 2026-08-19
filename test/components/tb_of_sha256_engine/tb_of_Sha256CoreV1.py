import py4hw
from minuscul_crypto_miner.architecture.Sha256Crcuits import *
from minuscul_crypto_miner.architecture.bus.bus import BusInterface
from sha256_core_v1_test_doubles import Sender, Collector
import hashlib


sys = py4hw.HWSystem()

reset = sys.wire('reset', 1)
py4hw.Constant(sys, 'reset', 0, reset)

# Sender -> Sha256CoreV1
bus_if = BusInterface(sys, 'bus_if')

seed_out = sys.wire('seed_out', 512)
nbbytes_out = sys.wire('nbbytes_out', 8)
digest_out = sys.wire('digest_out', 256)
digest_ready = sys.wire('digest_ready', 1)
digest_fetched = sys.wire('digest_fetched', 1)

engine = Sha256CoreV1(sys, 'sha256_engine', reset, bus_if,
                       seed_out, nbbytes_out, digest_out, digest_ready, digest_fetched,
                       debug=False)

seeds = [
    (0b11110000_11101111_01011101_10110110, 4),
    (0x1, 1),
    (0x2, 1),
    (0xDEADBEEF, 4),
    (0xC0FFEE, 3),
]

snd = Sender(sys, 'snd', bus_if, seeds, debug=False)
col = Collector(sys, 'col', seed_out, nbbytes_out, digest_out, digest_ready, digest_fetched)

sim = sys.getSimulator()
while len(col.captured) < len(seeds):
    sim.clk(1)

passed = True
for seed, nbbytes, digest in col.captured:
    expected = int(hashlib.sha256(seed.to_bytes(nbbytes, byteorder='big')).hexdigest(), 16)
    match = (digest == expected)
    passed = passed and match
    print(f"seed={seed:#x} nbbytes={nbbytes} digest={digest:064x} expected={expected:064x} match={match}")

if passed:
    print("Test Passed")
else:
    print("Test Failed")
