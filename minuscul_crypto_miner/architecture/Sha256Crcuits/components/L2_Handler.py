import py4hw 

'''

State machine of the component:
0 = reset_state
1 = init_state
2 = calculation_state
3 = wait_for_memory
4 = modify_final_val
5 = done # digest held here until output_consumed, then loops back to reset_state


'''


def ror(n,rotations,width):
    mask = (1<< width) - 1
    return ((n>>rotations)|(n<<(width-rotations))) & mask


def sha256_round(a,b,c,d,e,f,g,h,k,w):
    S1 = (ror(e,6,32)) ^ (ror(e,11,32) ^ (ror(e,25,32)))
    ch = (e & f) ^ ((~e&0xFFFFFFFF) & g)
    temp1 = (h + S1 + ch + k + w) & 0xFFFFFFFF
    S0 = (ror(a,2,32)) ^ (ror(a,13,32) ^ (ror(a,22,32)))
    maj = (a & b) ^ (a & c) ^ (b & c)
    temp2 = (S0 + maj) & 0xFFFFFFFF
    return (temp1+temp2)&0xFFFFFFFF, a, b, c, (d+temp1)&0xFFFFFFFF, e, f, g


class L2_Handler(py4hw.Logic):
    def __init__(self,parent,name,
                 # Memory Connection with the values buffer
                 Buffer_address,Buffer_Val,

                 #Connections to next componet
                 # (output_val sits before start/startConsumed to keep the same
                 # positional order as L2_Handler_2x/L2_Handler_4x and every caller)
                 output_val,start,startConsumed,done,reset,output_consumed,scheduleDrained,H_init,debug=False):
        super().__init__(parent,name)

        # Connection to the previous component 
        self.address = self.addOut("Buffer1_address",Buffer_address)
        self.Val = self.addIn("Buffer1_Val",Buffer_Val)
        self.start = self.addIn("start",start) # Signal form the previous component that tells this component that data is avialable 
        self.startConsumed = self.addOut("startConsumed",startConsumed) # to L1_res_Buffer: ack for 'start', so it can safely drop the held signal

        self.output = self.addOut("output_val",output_val)
        self.done = self.addOut("done",done)
        self.reset = self.addIn("reset",reset)
        self.output_consumed = self.addIn("output_consumed",output_consumed)
        self.scheduleDrained = self.addOut("scheduleDrained",scheduleDrained) # to L1_Handler: L1_res_Buffer is free again
        # 256b chaining value to start the compression from -- standard SHA-256 IV
        # for a from-scratch hash, or a cached midstate to continue a hash whose
        # first block was already compressed elsewhere (see bitcoinMining package).
        self.H_init = self.addIn("H_init",H_init)
        self.debug = debug

        if self.debug:
            print("L2_Handler")

        # Internal variables
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
        self.wait_for_update = 0
        self.finalOutput = 0
        self.digest = 0


    def clock(self):


        # Internal variables
        done = 0
        output = 0
        address = 0
        schedule_drained = 0
        start_consumed = 0
        match self.state:

            case 0:
                if self.start.get() == 1:
                    self.state = 1
                    start_consumed = 1
                    if self.debug:
                        print("L2_Handler:reset[0]->init_state[1]")
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
                address = self.i # request W[0], 2-cycle latency (address reg + data reg) away
                self.state = 3
                if self.debug:
                    print("L2_Handler:init_state[1]->prefetch[3]")
            case 2:

                fetched_val = self.Val.get() # W[self.i], requested two cycles ago
                self.a,self.b,self.c,self.d,self.e,self.f,self.g,self.h = sha256_round(
                    self.a,self.b,self.c,self.d,self.e,self.f,self.g,self.h,
                    self.Klist[self.i],fetched_val)

                self.i = self.i+1


                if self.i == 64 :
                    self.state = 4
                    schedule_drained = 1 # this was the last read of L1_res_Buffer for this schedule
                    if self.debug:
                        print("L2_Handler:calculation_state[2]->modify_final_val[4]")
                else:
                    # Stay in state 2 every cycle: request W[self.i+1] now so it
                    # is sitting in Val two rounds from now, keeping the 2-cycle
                    # L1_res_Buffer read latency permanently hidden behind
                    # this round's compute (no more per-round wait state).
                    address = min(self.i + 1, 63)
                    if self.debug:
                        print(f"L2_Handler:calculation_state[2] round {self.i}")
            case 3:
                # One-time second prefetch: W[0] was already requested back in
                # init_state[1], so request W[1] here. With two reads now in
                # flight, calculation_state[2] always finds Val ready and never
                # needs to fall back into this wait state again.
                address = min(self.i + 1, 63)
                self.state = 2
                if self.debug:
                    print("L2_Handler:prefetch[3]->calculation_state[2]")
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
                    print(f"L2_Handler:modify_final_val[4]->done[5] digest={self.digest:064x}")
            case 5:
                done = 1
                output = self.digest
                if self.output_consumed.get() == 1:
                    self.state = 0
                    if self.debug:
                        print("L2_Handler:done[5]->reset_state[0]")

        # assigning the outputs
        self.done.prepare(done)
        self.output.prepare(output)
        self.address.prepare(address)
        self.scheduleDrained.prepare(schedule_drained)
        self.startConsumed.prepare(start_consumed)

