import py4hw
from minuscul_crypto_miner.architecture.Sha256Crcuits import *


sys = py4hw.HWSystem()

input_wire = sys.wire('input', 512)
input_ready = sys.wire('input_ready', 1)
output = sys.wire('output', 512)
output_ready = sys.wire('output_ready', 8)
reset = sys.wire('reset',1)
output_consumed = sys.wire('output_consumed',1)
NBbytes = sys.wire('NBbytes',8)

py4hw.Constant(sys, 'input', 0b11110000_11101111_01011101_10110110, input_wire)
py4hw.Constant(sys, 'input_ready', 1, input_ready)
py4hw.Constant(sys, 'reset', 1, reset)
py4hw.Constant(sys, 'output_consumed',1,output_consumed)
py4hw.Constant(sys, 'NBbytes',4,NBbytes)

# Instantiate the SHA256 engine
engine = sha256_Engine(sys, 'sha256_engine', input_wire, input_ready,NBbytes ,output, output_ready, reset, output_consumed)

wvf = py4hw.Waveform(sys, 'wvf', [input_wire, output,engine.L1_done,output_ready,engine.output_address,engine.output_val])

sim = sys.getSimulator()
while output.get() == 0 :
    sim.clk(1)
    print(f"address = {engine.output_address.get()} | val = {engine.output_val.get()}")
print(f"output {output.get():0256b}")
wvf.gui()