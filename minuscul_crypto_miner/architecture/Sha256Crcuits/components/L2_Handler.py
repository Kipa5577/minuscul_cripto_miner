import py4hw 

'''

State machine of the component:
0 = reset_state
1 = init_state 
2 = prepare_calculation
2 = calculation_state
3 = wait_for_memory
4 = Modify_Final_val
5 = done # result ready to be put in to the output


'''



class SecondLayer(py4hw.Logic):
    def __init__(self,parent,name,Buffer1_address,Buffer1_Val,output_val,start,done,reset,output_consumed):
        super().__init__(parent,name)

        self.address = self.addOut("Buffer1_address",Buffer1_address)
        self.Val = self.addIn("Buffer1_Val",Buffer1_Val)
        self.start = self.addIn("start",start)

        self.output = self.addOut("output_val",output_val)
        self.done = self.addOut("done",done)
        self.reset = self.addIn("reset",reset)

        print("SecondLayer")

        # Internal variables
        self.h0_init = 0x6a09e667
        self.h1_init = 0xbb67ae85
        self.h2_init = 0x3c6ef372
        self.h3_init = 0xa54ff53a
        self.h4_init = 0x510e527f
        self.h5_init = 0x9b05688c
        self.h6_init = 0x1f83d9ab
        self.h7_init = 0x5be0cd19

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


    def ror(self,n,rotations,width):
        mask = (1<< width) - 1
        return ((n>>rotations)|(n<<(width-rotations))) & mask


    def clock(self):


        # Internal variables
        done = 0
        output = 0
        address = 0 
        match self.state:

            case 0:
                self.Klist = [0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
                0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
                0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
                0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
                0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
                0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
                0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
                0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2]


                if self.start.get() == 1:
                    self.state = 1
                    print("SecondLayer:reset[0]->init_state[1]")
            case 1:
                self.Klist = [0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
                    0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
                    0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
                    0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
                    0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
                    0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
                    0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
                    0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2]


                self.h0 = self.h0_init
                self.h1 = self.h1_init
                self.h2 = self.h2_init
                self.h3 = self.h3_init
                self.h4 = self.h4_init
                self.h5 = self.h5_init
                self.h6 = self.h6_init
                self.h7 = self.h7_init

                self.a=self.h0
                self.b=self.h1
                self.c=self.h2
                self.d=self.h3
                self.e=self.h4
                self.f=self.h5
                self.g=self.h6
                self.h=self.h7
                
                self.i = 0
                address = self.i
                self.state = 3
                print("SecondLayer:init_state[1]->wait_for_memory[3]")
            case 2:

                fetched_val = self.Val.get()
                S1 = (self.ror(self.e,6,32)) ^ (self.ror(self.e,11,32) ^ (self.ror(self.e,25,32)))
                ch = (self.e & self.f) ^ ((~self.e&0xFFFFFFFF) & self.g)
                temp1 = (self.h + S1 + ch + self.Klist[self.i] + fetched_val) & 0xFFFFFFFF
                S0 = (self.ror(self.a,2,32)) ^ (self.ror(self.a,13,32) ^ (self.ror(self.a,22,32)))
                maj = (self.a & self.b) ^ (self.a & self.c) ^ (self.b & self.c)
                temp2 = (S0 + maj) & 0xFFFFFFFF
                self.h = self.g
                self.g = self.f
                self.f = self.e
                self.e = (self.d + temp1) & 0xFFFFFFFF
                self.d = self.c 
                self.c = self.b 
                self.b = self.a 
                self.a = (temp1 + temp2) & 0xFFFFFFFF

                self.i = self.i+1
                
                
                if self.i == 64 :
                    self.state = 4
                    print("SecondLayer:prepare_calculation[2]->Modify_Final_val[4]")
                else:
                    address = self.i
                    self.state = 3
                    print("SecondLayer:prepare_calculation[2]->wait_for_memory[3]")
            case 3:

                self.state = 2
                print("SecondLayer:wait_for_memory[3]->prepare_calculation[2]")
            case 4:
                self.h0 = (self.h0 + self.a)&0xFFFFFFFF
                self.h1 = (self.h1 + self.b)&0xFFFFFFFF
                self.h2 = (self.h2 + self.c)&0xFFFFFFFF
                self.h3 = (self.h3 + self.d)&0xFFFFFFFF
                self.h4 = (self.h4 + self.e)&0xFFFFFFFF
                self.h5 = (self.h5 + self.f)&0xFFFFFFFF
                self.h6 = (self.h6 + self.g)&0xFFFFFFFF
                self.h7 = (self.h7 + self.h)&0xFFFFFFFF

                output = (self.h0<<224)|(self.h1<<192)|(self.h2<<160)|(self.h3<<128)|(self.h4<<96)|(self.h5<<64)|(self.h6<<32)|self.h7

        # assigning the outputs
        self.done.prepare(done)
        self.output.prepare(output)
        self.address.prepare(address)

