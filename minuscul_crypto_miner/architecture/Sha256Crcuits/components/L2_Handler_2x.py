from minuscul_crypto_miner.architecture.Sha256Crcuits.components._L2_HandlerNx import _L2_HandlerNx


class L2_Handler_2x(_L2_HandlerNx):
    # Computes 2 SHA-256 compression rounds combinationally per cycle (32
    # cycles for the full round loop instead of L2_Handler's 64) -- needs a
    # 2-port L1_res_Buffer (see its extra_ports param). See _L2_HandlerNx
    # for the shared FSM/priming design.
    def __init__(self,parent,name,Buffer_addresses,Buffer_vals,output_val,
                 start,startConsumed,done,reset,output_consumed,scheduleDrained,H_init,debug=False):
        super().__init__(parent,name,2,Buffer_addresses,Buffer_vals,output_val,
                          start,startConsumed,done,reset,output_consumed,scheduleDrained,H_init,debug)
