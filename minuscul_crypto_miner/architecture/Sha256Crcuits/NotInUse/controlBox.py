import py4hw 

'''
NOT USED FOR THE MOMENT 
Finite state machine
This finite sate machine should controll things in such a way to make the algoritm stages work in parallel
'''

class ControlBox(py4hw.Logic):
    def __init__(self,parent,name,stage1_start,stage1_done,stage2_start,stage2_done,stage3_start,stage3_done,stage4_start,stage4_done):
        super().__init__(parent,name)

        self.s1_start = self.addOut("s1_start",stage1_start)
        self.s2_start = self.addOut("s2_start",stage2_start)
        self.s3_start = self.addOut("s3_start",stage3_start)
        self.s4_start = self.addOut("s4_start",stage4_start)

        self.s1_done = self.addIn("s1_done",stage1_done)
        self.s2_done = self.addIn("s2_done",stage2_done)
        self.s3_done = self.addIn("s3_done",stage3_done)
        self.s4_done = self.addIn("s4_done",stage4_done)

        print("Control Box Created")

    def click(self):
        pass


