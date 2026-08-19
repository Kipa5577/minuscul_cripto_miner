import py4hw

'''
States 
0 = idle
1 = prepare_output
2 = waiting_fetch


'''



class input_handler(py4hw.Logic): # for the moment this input component can handle only words smaller than 512 bits
    def __init__(self,parent,name,input,output,NBbytes,preparationDone):
        super().__init__(parent,name)

        self.input = self.addIn("Input",input) # the input is between 1 and 512 bits
        self.NBbytes = self.addIn("NBbytes",NBbytes)
        self.output = self.addOut("output",output) # the output should be 512 no matter what 
        self.outputVal = 0 

        self.preparationDone = self.addOut("preparationDone",preparationDone)
        self.state = 1

        print("input_handler initialised")


    def clock(self):

        done = 0 
        

        match self.state:

            case 0:

                print("input_handler:idle[0]->prepare_output[1]")
                self.state = 1
            case 1:
                val = self.input.get()
                length = self.NBbytes.get() + 1
                inputBits = self.NBbytes.get()*8
                val = ((val<<8) | 0b1000_0000)
                while length%64!=56:
                    val = val << 8
                    length = length+1
                output = (val << 64) | inputBits
                #print(f"input_handler:Output:{output:b}")
                print("input_handler:prepare_output[1]->waiting_fetch[2]")
                self.state = 2
                self.outputVal = output
                done = 1 
            case 2:

                done = 1
                print("waiting_fetch[2]")
                self.state = 3 
        

        self.output.prepare(self.outputVal)
        self.preparationDone.prepare(done)