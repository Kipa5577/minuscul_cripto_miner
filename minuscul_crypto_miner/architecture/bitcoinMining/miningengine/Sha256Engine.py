import py4hw
from minuscul_crypto_miner.architecture.Sha256Crcuits import (
    L1_Handler,
    L2_Handler,
    L1_res_Buffer,
    L1BufferInterf,
)


'''
State machine of the component:
0 = idle        # waiting for a new job's first-64-header-bytes block
1 = loading     # holding input_ready until L1_Handler captures the block
2 = l1_running  # waiting for L1_Handler to finish expanding the schedule
3 = l2_running  # waiting for L2_Handler to finish the 64 compression rounds
4 = ready       # midstate held valid until a new job arrives

Note: a new_job pulse arriving while in states 1-3 (previous job's midstate
still being computed) or right as state 4 restarts loading briefly invalidates
midstate_out for lanes sampling it at that exact moment -- fine for a single
canned job (this benchmark's scope), but a real multi-job design would need
to gate lanes from starting a new nonce attempt during a reload, or double
buffer the midstate register.
'''


class _Sha256EngineController(py4hw.Logic):
    # py4hw's simulator only clocks leaf components (no children), so the FSM
    # driving L1_Handler/L2_Handler must live in its own leaf class, separate
    # from Sha256Engine's structural (children-only) wiring below.
    def __init__(self,parent,name,new_job,l1_input_ready,l1_input_consumed,l1_done,
                 l2_done,l2_digest,l2_output_consumed,midstate_out,midstate_ready,debug=False):
        super().__init__(parent,name)

        self.new_job = self.addIn("new_job",new_job)
        self.l1_input_ready = self.addOut("l1_input_ready",l1_input_ready)
        self.l1_input_consumed = self.addIn("l1_input_consumed",l1_input_consumed)
        self.l1_done = self.addIn("l1_done",l1_done)
        self.l2_done = self.addIn("l2_done",l2_done)
        self.l2_digest = self.addIn("l2_digest",l2_digest)
        self.l2_output_consumed = self.addOut("l2_output_consumed",l2_output_consumed)
        self.midstate_out = self.addOut("midstate_out",midstate_out)
        self.midstate_ready = self.addOut("midstate_ready",midstate_ready)

        self.debug = debug
        self.state = 0
        self.digest = 0

    def clock(self):

        l1_input_ready = 0
        l2_output_consumed = 0

        match self.state:
            case 0:
                if self.new_job.get() == 1:
                    self.state = 1
                    if self.debug:
                        print("Sha256Engine:idle[0]->loading[1]")
            case 1:
                l1_input_ready = 1
                if self.l1_input_consumed.get() == 1:
                    self.state = 2
                    if self.debug:
                        print("Sha256Engine:loading[1]->l1_running[2]")
            case 2:
                if self.l1_done.get() == 1:
                    self.state = 3
                    if self.debug:
                        print("Sha256Engine:l1_running[2]->l2_running[3]")
            case 3:
                if self.l2_done.get() == 1:
                    self.digest = self.l2_digest.get()
                    l2_output_consumed = 1
                    self.state = 4
                    if self.debug:
                        print(f"Sha256Engine:l2_running[3]->ready[4] midstate={self.digest:064x}")
            case 4:
                if self.new_job.get() == 1:
                    self.state = 1
                    if self.debug:
                        print("Sha256Engine:ready[4]->loading[1] (new job)")

        self.midstate_out.prepare(self.digest)
        self.midstate_ready.prepare(1 if self.state == 4 else 0)
        self.l1_input_ready.prepare(l1_input_ready)
        self.l2_output_consumed.prepare(l2_output_consumed)


class Sha256Engine(py4hw.Logic):
    # Computes the SHA-256 midstate (chaining value) after compressing just
    # the first 64 (constant, per-job) bytes of an 80-byte Bitcoin header --
    # exactly one block, so no InputFormatter/padding is involved. Reuses
    # L1_Handler/L1_res_Buffer/L2_Handler directly, the same way Sha256CoreV1
    # does internally, just without the input-formatting front end.
    def __init__(self,parent,name,reset,header_first64bytes,new_job,midstate_out,midstate_ready,debug=False):
        super().__init__(parent,name)

        # L1_Handler <-> L1_res_Buffer
        self.l1_to_buffer_schedule = L1BufferInterf(self,'l1_to_buffer_schedule')
        self.l1_input_ready = self.wire("l1_input_ready",1)
        self.l1_input_consumed = self.wire("l1_input_consumed",1)
        self.l1_done = self.wire("l1_done",1)

        # L1_res_Buffer <-> L2_Handler
        self.l2_to_buffer_address = self.wire("l2_to_buffer_address",7)
        self.buffer_to_l2_word = self.wire("buffer_to_l2_word",32)
        self.buffer_to_l2_word_ready = self.wire("buffer_to_l2_word_ready",1)
        self.l2_word_ready_consumed = self.wire("l2_word_ready_consumed",1)

        # L2_Handler -> L1_Handler (backpressure, unused here since we only
        # ever load one block at a time, but required by both interfaces)
        self.schedule_drained = self.wire("schedule_drained",1)

        # L2_Handler outputs
        self.l2_digest = self.wire("l2_digest",256)
        self.l2_done = self.wire("l2_done",1)
        self.l2_output_consumed = self.wire("l2_output_consumed",1)

        # Standard SHA-256 IV: this is always the very first block of the
        # message, so the compression always starts from scratch.
        SHA256_STANDARD_IV = 0x6a09e667bb67ae853c6ef372a54ff53a510e527f9b05688c1f83d9ab5be0cd19
        self.standard_iv = self.wire("standard_iv",256)
        py4hw.Constant(self,'standard_iv_const',SHA256_STANDARD_IV,self.standard_iv)

        L1_Handler(self,'L1',reset,header_first64bytes,self.l1_input_ready,
                   self.l1_input_consumed,self.l1_to_buffer_schedule,self.l1_done,
                   self.schedule_drained,debug)
        L1_res_Buffer(self,'L1buf',self.l1_to_buffer_schedule,self.l2_to_buffer_address,
                      self.buffer_to_l2_word,self.buffer_to_l2_word_ready,
                      self.l2_word_ready_consumed,debug)
        L2_Handler(self,'L2',self.l2_to_buffer_address,self.buffer_to_l2_word,self.l2_digest,
                   self.buffer_to_l2_word_ready,self.l2_word_ready_consumed,self.l2_done,
                   reset,self.l2_output_consumed,self.schedule_drained,self.standard_iv,debug)

        _Sha256EngineController(self,'controller',new_job,self.l1_input_ready,
                                self.l1_input_consumed,self.l1_done,self.l2_done,
                                self.l2_digest,self.l2_output_consumed,
                                midstate_out,midstate_ready,debug)
