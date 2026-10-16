import py4hw



class ror(py4hw.Logic):
    def __init__(self,parent,name,n,rotations,width,result):
        super().__init__(parent,name)

        self.n = self.addIn("n",n)
        self.rotations = self.addIn("rotations",rotations)
        self.width = self.addIn("width",width)

        self.res = self.addOut("result",result)

    def clock(self):
        n = self.n.get()
        rotations = self. rotations.get()
        width = self.width.get()
        mask = (1<<width) - 1 
        self.res.prepare(((n>>rotations)|(n<<(width-rotations))) & mask)

