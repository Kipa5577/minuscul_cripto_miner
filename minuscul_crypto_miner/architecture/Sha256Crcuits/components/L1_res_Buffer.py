import py4hw


class L1BufferInterf(py4hw.Interface):
    def __init__(self,parent,name):
        super().__init__(parent,name)

        self.W = []
        #64 words of 32 bits each 
        for i in range(0,64):
            self.W.append(self.addSourceToSink(f'W{i}',32))
            
        self.DataReady = self.addSourceToSink('DataReady',1)
        


class L1_res_Buffer(py4hw.Logic):
    def __init__(self,parent,name,L1Connection:L1BufferInterf,output_address,output_val,values_updated,start_consumed,debug=False,extra_ports=None):
        super().__init__(parent,name)

        self.address = self.addIn("output_address",output_address)
        self.val = self.addOut("output_val",output_val)
        self.L1 = self.addInterfaceSink("L1Connection",L1Connection)
        self.values_updated = self.addOut("values_updated",values_updated)
        self.start_consumed = self.addIn("start_consumed",start_consumed) # from L2_Handler: it has captured this "start", safe to drop values_updated


        self.extra_addresses = []
        self.extra_vals = []
        for idx, (extra_address, extra_val) in enumerate(extra_ports or []):
            self.extra_addresses.append(self.addIn(f"extra_output_address_{idx}",extra_address))
            self.extra_vals.append(self.addOut(f"extra_output_val_{idx}",extra_val))

        self.Buffers = [0]*64
        self.pending = False # held (not a one-shot pulse) so L2_Handler can never miss it by being busy elsewhere when it fires
        self.debug = debug

        if self.debug:
            print("L1_res_Buffer")


    def clock(self):


        add = self.address.get()
        self.val.prepare(self.Buffers[add])

        for extra_address, extra_val in zip(self.extra_addresses, self.extra_vals):
            extra_val.prepare(self.Buffers[extra_address.get()])

        dataReady = self.L1.DataReady.get()

        if dataReady:
            self.pending = True
            if self.debug:
                print("L1_res_Buffer:DataLoaded")


            for i in range(0,64):
                self.Buffers[i] = self.L1.W[i].get()

        if self.start_consumed.get() == 1:
            self.pending = False

        self.values_updated.prepare(1 if self.pending else 0)


        