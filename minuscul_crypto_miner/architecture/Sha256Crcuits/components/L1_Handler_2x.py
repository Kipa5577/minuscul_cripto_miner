import py4hw
from minuscul_crypto_miner.architecture.Sha256Crcuits.components.L1_res_Buffer import L1BufferInterf
from minuscul_crypto_miner.architecture.Sha256Crcuits.components.L1_Handler import sha256_schedule_word


'''

State machine of the component (identical shape to L1_Handler):
0 = reset_state
1 = init_state
2 = calculation_state
3 = wait_to_publish # W[0..63] complete, waiting for L2_Handler to finish with L1_res_Buffer
4 = done

'''


class L1_Handler_2x(py4hw.Logic):
    def __init__(self,parent,name,reset,initImput,inputReady,inputConsumed,outputFORlayer2:L1BufferInterf,done,scheduleDrained,debug=False):
        super().__init__(parent,name)

        self.reset = self.addIn("reset",reset)
        self.init = self.addIn("initInput",initImput) #512 bits
        self.done = self.addIn("done",done)
        self.output = self.addInterfaceSource("out",outputFORlayer2)

        self.inputReady = self.addIn("inputReady",inputReady)
        self.inputConsumed = self.addOut("inputConsumed",inputConsumed)
        self.scheduleDrained = self.addIn("scheduleDrained",scheduleDrained)

        self.list_W = [0]*64
        self.i = 16
        self.state = 0
        self.buffer_available = True
        self.debug = debug

        if self.debug:
            print("L1_Handler_2x created:")
            print(f"i = {self.i},state = {self.state},reset = {self.reset.get()}")

    def clock(self):

        if self.scheduleDrained.get() == 1:
            self.buffer_available = True

        done = 0
        input_consumed = 0
        match self.state:
            case 0:
                for i in range(64):
                    self.list_W[i] = 0

                if self.inputReady.get() == 1:
                    self.state = 1
                    if self.debug:
                        print("State:reset_state[0]->init_state[1]")
            case 1:
                self.list_W[0] = (self.init.get()>>(32*15))&0b11111111_11111111_11111111_11111111
                self.list_W[1] = (self.init.get()>>(32*14))&0b11111111_11111111_11111111_11111111
                self.list_W[2] = (self.init.get()>>(32*13))&0b11111111_11111111_11111111_11111111
                self.list_W[3] = (self.init.get()>>(32*12))&0b11111111_11111111_11111111_11111111
                self.list_W[4] = (self.init.get()>>(32*11))&0b11111111_11111111_11111111_11111111
                self.list_W[5] = (self.init.get()>>(32*10))&0b11111111_11111111_11111111_11111111
                self.list_W[6] = (self.init.get()>>(32*9))&0b11111111_11111111_11111111_11111111
                self.list_W[7] = (self.init.get()>>(32*8))&0b11111111_11111111_11111111_11111111
                self.list_W[8] = (self.init.get()>>(32*7))&0b11111111_11111111_11111111_11111111
                self.list_W[9] = (self.init.get()>>(32*6))&0b11111111_11111111_11111111_11111111
                self.list_W[10] = (self.init.get()>>(32*5))&0b11111111_11111111_11111111_11111111
                self.list_W[11] = (self.init.get()>>(32*4))&0b11111111_11111111_11111111_11111111
                self.list_W[12] = (self.init.get()>>(32*3))&0b11111111_11111111_11111111_11111111
                self.list_W[13] = (self.init.get()>>(32*2))&0b11111111_11111111_11111111_11111111
                self.list_W[14] = (self.init.get()>>(32*1))&0b11111111_11111111_11111111_11111111
                self.list_W[15] = (self.init.get()>>(32*0))&0b11111111_11111111_11111111_11111111

                input_consumed = 1
                self.state = 2
                if self.debug:
                    print("init_state[1]->calculation_state[2]")

            case 2:
                if self.debug:
                    print(f"L1_Handler_2x:i:{self.i}")
                self.list_W[self.i] = sha256_schedule_word(self.list_W,self.i)
                self.list_W[self.i+1] = sha256_schedule_word(self.list_W,self.i+1)
                self.i = self.i + 2

                if self.i == 64 :
                    self.state = 3
                    if self.debug:
                        print("calculation_state[2]->wait_to_publish[3]")

            case 3:
                if self.buffer_available:
                    self.state = 4
                    if self.debug:
                        print("wait_to_publish[3]->done[4]")

            case 4:
                done = 1
                self.buffer_available = False
                self.i = 16
                self.state = 0
                if self.debug:
                    print("done[4]->reset_state[0]")
                    for i in range(64):
                        print(f"L1_Handler_2x:W[{i}] = {self.list_W[i]:032b}")

        self.done.prepare(done)
        self.inputConsumed.prepare(input_consumed)
        self.output.DataReady.prepare(done)
        for i in range(0,64):
            self.output.W[i].prepare(self.list_W[i])

