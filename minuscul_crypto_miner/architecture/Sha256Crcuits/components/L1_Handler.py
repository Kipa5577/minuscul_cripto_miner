import py4hw 
from minuscul_crypto_miner.architecture.Sha256Crcuits.components.L1_res_Buffer import L1BufferInterf



'''

State machine of the component:
0 = reset_state
1 = init_state
2 = calculation_state
3 = wait_to_publish # W[0..63] complete, waiting for L2_Handler to finish with L1_res_Buffer
4 = done             

'''


class L1_Handler(py4hw.Logic):
    def __init__(self,parent,name,reset,initImput,inputReady,inputConsumed,outputFORlayer2:L1BufferInterf,done,scheduleDrained,debug=False):
        super().__init__(parent,name)

        self.reset = self.addIn("reset",reset)
        self.init = self.addIn("initInput",initImput) #512 bits
        self.done = self.addIn("done",done)
        self.output = self.addInterfaceSource("out",outputFORlayer2)

        self.inputReady = self.addIn("inputReady",inputReady)
        self.inputConsumed = self.addOut("inputConsumed",inputConsumed) # to InputFormatter: this block has been captured, safe to present the next one
        self.scheduleDrained = self.addIn("scheduleDrained",scheduleDrained) # from L2_Handler: previous schedule fully read, L1_res_Buffer is free to be overwritten

        self.list_W = [0]*64
        self.i = 16
        self.state = 0
        self.buffer_available = True # nothing published yet, so L1_res_Buffer starts out free
        self.debug = debug

        if self.debug:
            print("L1_Handler created:")
            print(f"i = {self.i},state = {self.state},reset = {self.reset.get()}")

    def ror(self, n, rotations, width):
        mask = (1<< width) - 1
        return ((n>>rotations)|(n<<(width-rotations))) & mask


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
                # Initialise de first values
                self.list_W[0] = (self.init.get()>>(32*15))&0b11111111_11111111_11111111_11111111 #32 bit register 
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
                    print(f"L1_Handler:i:{self.i}")
                s0_p1 = self.ror(self.list_W[self.i-15],7,32)
                s0_p2 = self.ror(self.list_W[self.i-15],18,32)
                s0_p3 = self.list_W[self.i-15]>>3
                s1_p1 = self.ror(self.list_W[self.i-2],17,32)
                s1_p2 = self.ror(self.list_W[self.i-2],19,32)
                s1_p3 = self.list_W[self.i-2]>>10
                s0 = s0_p1 ^ s0_p2 ^ s0_p3
                s1 = s1_p1 ^ s1_p2 ^ s1_p3
                self.list_W[self.i] = (self.list_W[self.i-16] + s0 + self.list_W[self.i-7] + s1)& 0xFFFFFFFF
                self.i = self.i + 1

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
                        print(f"L1_Handler:W[{i}] = {self.list_W[i]:032b}")
                


        self.done.prepare(done)
        self.inputConsumed.prepare(input_consumed)
        self.output.DataReady.prepare(done)
        self.output.W0.prepare(self.list_W[0])
        self.output.W1.prepare(self.list_W[1])
        self.output.W2.prepare(self.list_W[2])
        self.output.W3.prepare(self.list_W[3])
        self.output.W4.prepare(self.list_W[4])
        self.output.W5.prepare(self.list_W[5])
        self.output.W6.prepare(self.list_W[6])
        self.output.W7.prepare(self.list_W[7])
        self.output.W8.prepare(self.list_W[8])
        self.output.W9.prepare(self.list_W[9])
        self.output.W10.prepare(self.list_W[10])
        self.output.W11.prepare(self.list_W[11])
        self.output.W12.prepare(self.list_W[12])
        self.output.W13.prepare(self.list_W[13])
        self.output.W14.prepare(self.list_W[14])
        self.output.W15.prepare(self.list_W[15])
        self.output.W16.prepare(self.list_W[16])
        self.output.W17.prepare(self.list_W[17])
        self.output.W18.prepare(self.list_W[18])
        self.output.W19.prepare(self.list_W[19])
        self.output.W20.prepare(self.list_W[20])
        self.output.W21.prepare(self.list_W[21])
        self.output.W22.prepare(self.list_W[22])
        self.output.W23.prepare(self.list_W[23])
        self.output.W24.prepare(self.list_W[24])
        self.output.W25.prepare(self.list_W[25])
        self.output.W26.prepare(self.list_W[26])
        self.output.W27.prepare(self.list_W[27])
        self.output.W28.prepare(self.list_W[28])
        self.output.W29.prepare(self.list_W[29])
        self.output.W30.prepare(self.list_W[30])
        self.output.W31.prepare(self.list_W[31])
        self.output.W32.prepare(self.list_W[32])
        self.output.W33.prepare(self.list_W[33])
        self.output.W34.prepare(self.list_W[34])
        self.output.W35.prepare(self.list_W[35])
        self.output.W36.prepare(self.list_W[36])
        self.output.W37.prepare(self.list_W[37])
        self.output.W38.prepare(self.list_W[38])
        self.output.W39.prepare(self.list_W[39])
        self.output.W40.prepare(self.list_W[40])
        self.output.W41.prepare(self.list_W[41])
        self.output.W42.prepare(self.list_W[42])
        self.output.W43.prepare(self.list_W[43])
        self.output.W44.prepare(self.list_W[44])
        self.output.W45.prepare(self.list_W[45])
        self.output.W46.prepare(self.list_W[46])
        self.output.W47.prepare(self.list_W[47])
        self.output.W48.prepare(self.list_W[48])
        self.output.W49.prepare(self.list_W[49])
        self.output.W50.prepare(self.list_W[50])
        self.output.W51.prepare(self.list_W[51])
        self.output.W52.prepare(self.list_W[52])
        self.output.W53.prepare(self.list_W[53])
        self.output.W54.prepare(self.list_W[54])
        self.output.W55.prepare(self.list_W[55])
        self.output.W56.prepare(self.list_W[56])
        self.output.W57.prepare(self.list_W[57])
        self.output.W58.prepare(self.list_W[58])
        self.output.W59.prepare(self.list_W[59])
        self.output.W60.prepare(self.list_W[60])
        self.output.W61.prepare(self.list_W[61])
        self.output.W62.prepare(self.list_W[62])
        self.output.W63.prepare(self.list_W[63])            



        