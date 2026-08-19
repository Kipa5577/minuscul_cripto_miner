import py4hw
from minuscul_crypto_miner.architecture.Sha256Crcuits.components import (
    FirstLayer,
    SecondLayer,
    ControlBox,
    input_handler,
    FirstLayerOutputBuffer,
    L1BufferInterf,
)


#This is the structural component 
class sha256_Engine(py4hw.Logic):
    def __init__(self, parent, name, input_wire, input_ready,NBbytes, output, output_ready,reset,output_consumed):
        super().__init__(parent, name)

        self.input = self.addIn("Input", input_wire)
        self.input_ready = self.addIn("Input_ready", input_ready)
        self.NBbytes = self.addIn("NBnytes",NBbytes)
        self.reset = self.addIn("reset",reset)
        self.output_consumed = self.addIn("output_consumed",output_consumed)

        self.output = self.addOut("output", output)
        self.output_ready = self.addOut("output_ready", output_ready)

        #Wire 
        self.L1_input_ready = self.wire("L1_input_ready")
        self.output_inp_L1 = self.wire("output_inp_L1",512)
        self.L1_to_buffer = L1BufferInterf(self, 'port0')
        self.L1_done = self.wire("L1_done",1)
        self.output_address = self.wire("output_address",7)
        self.output_val = self.wire("output_val",32)
        self.L2_start = self.wire("L2_start",1)


        # Initialize components (stubs for now)
        inp = input_handler(self,'inp',self.input,self.output_inp_L1,self.NBbytes,self.L1_input_ready)
        L1 = FirstLayer(self, 'L1',reset,self.output_inp_L1,self.L1_input_ready,self.L1_to_buffer,self.L1_done)
        L1buf = FirstLayerOutputBuffer(self, 'L1buf',self.L1_to_buffer,self.output_address,self.output_val,self.L2_start)
        L2 = SecondLayer(self, 'L2',self.output_address,self.output_val,output,self.L2_start,output_ready,reset,self.output_consumed)


        

