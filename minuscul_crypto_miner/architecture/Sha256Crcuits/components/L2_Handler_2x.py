import py4hw
from minuscul_crypto_miner.architecture.Sha256Crcuits.components.L2_Handler import sha256_round


'''

State machine of the component (identical shape to L2_Handler, the only
1-round/cycle variant -- port count changes the compute body, not the FSM):
0 = reset_state
1 = init_state
2 = calculation_state
3 = prefetch
4 = modify_final_val
5 = done # digest held here until output_consumed, then loops back to reset_state

'''


class L2_Handler_2x(py4hw.Logic):
    def __init__(self,parent,name,Buffer_addresses,Buffer_vals,output_val,
                 start,startConsumed,done,reset,output_consumed,scheduleDrained,H_init,debug=False):
        super().__init__(parent,name)

        assert len(Buffer_addresses) == 2
        assert len(Buffer_vals) == 2

        self.address0 = self.addOut("Buffer_address_0",Buffer_addresses[0])
        self.address1 = self.addOut("Buffer_address_1",Buffer_addresses[1])
        self.val0 = self.addIn("Buffer_val_0",Buffer_vals[0])
        self.val1 = self.addIn("Buffer_val_1",Buffer_vals[1])
        self.start = self.addIn("start",start)
        self.startConsumed = self.addOut("startConsumed",startConsumed)

        self.output = self.addOut("output_val",output_val)
        self.done = self.addOut("done",done)
        self.reset = self.addIn("reset",reset)
        self.output_consumed = self.addIn("output_consumed",output_consumed)
        self.scheduleDrained = self.addOut("scheduleDrained",scheduleDrained)
        self.H_init = self.addIn("H_init",H_init)
        self.debug = debug

        if self.debug:
            print("L2_Handler_2x")

        self.Klist = [0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
             0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
             0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
             0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
             0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
             0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
             0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
             0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2]

        self.a=0
        self.b=0
        self.c=0
        self.d=0
        self.e=0
        self.f=0
        self.g=0
        self.h=0

        self.h0 = 0
        self.h1 = 0
        self.h2 = 0
        self.h3 = 0
        self.h4 = 0
        self.h5 = 0
        self.h6 = 0
        self.h7 = 0

        self.i = 0

        self.state = 0
        self.digest = 0


    def clock(self):

        done = 0
        output = 0
        address0 = 0
        address1 = 0
        schedule_drained = 0
        start_consumed = 0

        match self.state:

            case 0:
                if self.start.get() == 1:
                    self.state = 1
                    start_consumed = 1
                    if self.debug:
                        print("L2_Handler_2x:reset[0]->init_state[1]")
            case 1:
                h_init = self.H_init.get()
                self.h0 = (h_init>>224)&0xFFFFFFFF
                self.h1 = (h_init>>192)&0xFFFFFFFF
                self.h2 = (h_init>>160)&0xFFFFFFFF
                self.h3 = (h_init>>128)&0xFFFFFFFF
                self.h4 = (h_init>>96)&0xFFFFFFFF
                self.h5 = (h_init>>64)&0xFFFFFFFF
                self.h6 = (h_init>>32)&0xFFFFFFFF
                self.h7 = h_init&0xFFFFFFFF

                self.a=self.h0
                self.b=self.h1
                self.c=self.h2
                self.d=self.h3
                self.e=self.h4
                self.f=self.h5
                self.g=self.h6
                self.h=self.h7

                self.i = 0
                address0 = 0 # request batch0 (words 0..1)
                address1 = 1
                self.state = 3
                if self.debug:
                    print("L2_Handler_2x:init_state[1]->prefetch[3]")
            case 2:
                fetched0 = self.val0.get() # batch requested two cycles ago
                fetched1 = self.val1.get()

                self.a,self.b,self.c,self.d,self.e,self.f,self.g,self.h = sha256_round(
                    self.a,self.b,self.c,self.d,self.e,self.f,self.g,self.h,
                    self.Klist[self.i],fetched0)
                self.a,self.b,self.c,self.d,self.e,self.f,self.g,self.h = sha256_round(
                    self.a,self.b,self.c,self.d,self.e,self.f,self.g,self.h,
                    self.Klist[self.i+1],fetched1)

                self.i = self.i + 2

                if self.i == 64:
                    self.state = 4
                    schedule_drained = 1 # this was the last read of L1_res_Buffer for this schedule
                    if self.debug:
                        print("L2_Handler_2x:calculation_state[2]->modify_final_val[4]")
                else:
                    # 2 batches ahead of the just-advanced index, same
                    # latency-hiding pattern as L2_Handler's single-port loop.
                    address0 = min(self.i+2,63)
                    address1 = min(self.i+3,63)
                    if self.debug:
                        print(f"L2_Handler_2x:calculation_state[2] rounds {self.i}..{self.i+1}")
            case 3:
                address0 = min(2,63) # request batch1 (words 2..3)
                address1 = min(3,63)
                self.state = 2
                if self.debug:
                    print("L2_Handler_2x:prefetch[3]->calculation_state[2]")
            case 4:
                self.h0 = (self.h0 + self.a)&0xFFFFFFFF
                self.h1 = (self.h1 + self.b)&0xFFFFFFFF
                self.h2 = (self.h2 + self.c)&0xFFFFFFFF
                self.h3 = (self.h3 + self.d)&0xFFFFFFFF
                self.h4 = (self.h4 + self.e)&0xFFFFFFFF
                self.h5 = (self.h5 + self.f)&0xFFFFFFFF
                self.h6 = (self.h6 + self.g)&0xFFFFFFFF
                self.h7 = (self.h7 + self.h)&0xFFFFFFFF

                self.digest = (self.h0<<224)|(self.h1<<192)|(self.h2<<160)|(self.h3<<128)|(self.h4<<96)|(self.h5<<64)|(self.h6<<32)|self.h7
                self.state = 5
                if self.debug:
                    print(f"L2_Handler_2x:modify_final_val[4]->done[5] digest={self.digest:064x}")
            case 5:
                done = 1
                output = self.digest
                if self.output_consumed.get() == 1:
                    self.state = 0
                    if self.debug:
                        print("L2_Handler_2x:done[5]->reset_state[0]")

        # assigning the outputs
        self.done.prepare(done)
        self.output.prepare(output)
        self.address0.prepare(address0)
        self.address1.prepare(address1)
        self.scheduleDrained.prepare(schedule_drained)
        self.startConsumed.prepare(start_consumed)
