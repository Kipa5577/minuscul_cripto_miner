from minuscul_crypto_miner.architecture.Sha256Crcuits.components._L2_HandlerNx import _L2_HandlerNx


class L2_Handler_4x(_L2_HandlerNx):
    # Computes 4 SHA-256 compression rounds combinationally per cycle (16
    # cycles for the full round loop instead of L2_Handler's 64) -- needs a
    # 4-port L1_res_Buffer (see its extra_ports param). Roughly double the
    # combinational depth per cycle of L2_Handler_2x -- real Fmax impact is
    # unverifiable here (no synthesis/timing closure in this behavioral
    # model), so treat this as the more speculative of the two variants.
    # See _L2_HandlerNx for the shared FSM/priming design.
    def __init__(self,parent,name,Buffer_addresses,Buffer_vals,output_val,
                 start,startConsumed,done,reset,output_consumed,scheduleDrained,H_init,debug=False):
        super().__init__(parent,name,4,Buffer_addresses,Buffer_vals,output_val,
                          start,startConsumed,done,reset,output_consumed,scheduleDrained,H_init,debug)
