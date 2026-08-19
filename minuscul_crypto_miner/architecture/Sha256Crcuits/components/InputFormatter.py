import py4hw

'''
States
0 = idle         # waiting for InputBuffer to present a seed
1 = presenting   # holding the padded block + start pulse until L1_Handler acks it
2 = cooldown     # one tick gap after firing output_fetched
'''


class InputFormatter(py4hw.Logic): # for the moment this input component can handle only words smaller than 512 bits
    def __init__(self,parent,name,input,input_ready,output,NBbytes,preparationDone,output_fetched,input_consumed,debug=False):
        super().__init__(parent,name)

        self.input = self.addIn("Input",input) # the input is between 1 and 512 bits
        self.input_ready = self.addIn("Input_ready",input_ready) # from InputBuffer.Data_Ready
        self.NBbytes = self.addIn("NBbytes",NBbytes)
        self.output = self.addOut("output",output) # the output should be 512 no matter what
        self.outputVal = 0

        self.preparationDone = self.addOut("preparationDone",preparationDone) # to L1_Handler (start pulse)
        self.output_fetched = self.addOut("output_fetched",output_fetched) # to InputBuffer.Data_Fetched
        self.input_consumed = self.addIn("input_consumed",input_consumed) # from L1_Handler: it has actually captured this block

        self.state = 0
        self.debug = debug

        if self.debug:
            print("InputFormatter initialised")


    def clock(self):

        done = 0
        fetched = 0

        match self.state:

            case 0:
                if self.input_ready.get() == 1:
                    val = self.input.get()
                    length = self.NBbytes.get() + 1
                    inputBits = self.NBbytes.get()*8
                    val = ((val<<8) | 0b1000_0000)
                    while length%64!=56:
                        val = val << 8
                        length = length+1
                    self.outputVal = (val << 64) | inputBits
                    done = 1
                    self.state = 1
                    if self.debug:
                        print("InputFormatter:idle[0]->presenting[1]")
            case 1:
                done = 1
                # Only fetch InputBuffer's next seed once L1_Handler has actually captured this one 
                if self.input_consumed.get() == 1:
                    fetched = 1
                    self.state = 2
                    if self.debug:
                        print("InputFormatter:presenting[1]->cooldown[2]")
            case 2:
                self.state = 0
                if self.debug:
                    print("InputFormatter:cooldown[2]->idle[0]")

        self.output.prepare(self.outputVal)
        self.preparationDone.prepare(done)
        self.output_fetched.prepare(fetched)
