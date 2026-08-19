import py4hw
from minuscul_crypto_miner.architecture.Sha256Crcuits.components import (
    L1_Handler,
    L2_Handler,
    ControlBox,
    InputFormatter,
    InputBuffer,
    OutputBuffer,
    L1_res_Buffer,
    L1BufferInterf,
)


#This is the structural component
class Sha256CoreV1(py4hw.Logic):
    def __init__(self, parent, name, reset, BusConnection,
                 Seed_OUT, NBbytes_OUT, Digest_OUT, Digest_Ready, Digest_Fetched,
                 debug=False):
        super().__init__(parent, name)

        # Top-level ports: BusConnection is this engine's slave port on the
        # shared mining Bus (see bus.py); Seed_OUT/NBbytes_OUT/Digest_OUT
        # match DifficultyValidator's inputs one-for-one so it plugs straight
        # into whatever consumes finished (seed, digest) pairs downstream.
        self.reset = self.addIn("reset",reset)
        self.seed_out = self.addOut("Seed_OUT",Seed_OUT)
        self.nbbytes_out = self.addOut("NBbytes_OUT",NBbytes_OUT)
        self.digest_out = self.addOut("Digest_OUT",Digest_OUT)
        self.digest_ready = self.addOut("Digest_Ready",Digest_Ready)
        self.digest_fetched = self.addIn("Digest_Fetched",Digest_Fetched)

        # InputBuffer <-> InputFormatter
        self.input_buffer_to_input_formatter_message = self.wire("input_buffer_to_input_formatter_message",512)
        self.input_buffer_to_input_formatter_message_length_bytes = self.wire("input_buffer_to_input_formatter_message_length_bytes",8)
        self.input_buffer_to_input_formatter_message_ready = self.wire("input_buffer_to_input_formatter_message_ready")
        self.input_formatter_to_input_buffer_message_fetched = self.wire("input_formatter_to_input_buffer_message_fetched")

        # InputBuffer -> OutputBuffer (direct push line, fired on the same
        # event that lets InputBuffer accept its next seed)
        self.input_buffer_to_output_buffer_seed_push = self.wire("input_buffer_to_output_buffer_seed_push",512)
        self.input_buffer_to_output_buffer_nbbytes_push = self.wire("input_buffer_to_output_buffer_nbbytes_push",8)
        self.input_buffer_to_output_buffer_push_valid = self.wire("input_buffer_to_output_buffer_push_valid")

        # InputFormatter <-> L1_Handler
        self.input_formatter_to_l1_handler_padded_block_ready = self.wire("input_formatter_to_l1_handler_padded_block_ready")
        self.input_formatter_to_l1_handler_padded_block = self.wire("input_formatter_to_l1_handler_padded_block",512)
        self.l1_handler_to_input_formatter_padded_block_consumed = self.wire("l1_handler_to_input_formatter_padded_block_consumed")

        # L1_Handler <-> L1_res_Buffer
        self.l1_handler_to_l1_res_buffer_schedule = L1BufferInterf(self, 'port0')
        self.l1_handler_to_l1_res_buffer_schedule_ready = self.wire("l1_handler_to_l1_res_buffer_schedule_ready",1)

        # L1_res_Buffer <-> L2_Handler
        self.l2_handler_to_l1_res_buffer_schedule_address = self.wire("l2_handler_to_l1_res_buffer_schedule_address",7)
        self.l1_res_buffer_to_l2_handler_schedule_word = self.wire("l1_res_buffer_to_l2_handler_schedule_word",32)
        self.l1_res_buffer_to_l2_handler_schedule_word_ready = self.wire("l1_res_buffer_to_l2_handler_schedule_word_ready",1)
        self.l2_handler_to_l1_res_buffer_schedule_word_ready_consumed = self.wire("l2_handler_to_l1_res_buffer_schedule_word_ready_consumed")

        # L2_Handler -> L1_Handler (backpressure: L1_res_Buffer is free again)
        self.l2_handler_to_l1_handler_schedule_drained = self.wire("l2_handler_to_l1_handler_schedule_drained")

        # L2_Handler <-> OutputBuffer
        self.l2_handler_to_output_buffer_digest = self.wire("l2_handler_to_output_buffer_digest",256)
        self.l2_handler_to_output_buffer_digest_ready = self.wire("l2_handler_to_output_buffer_digest_ready")
        self.output_buffer_to_l2_handler_digest_consumed = self.wire("output_buffer_to_l2_handler_digest_consumed")


        # Initialize components
        inbuf = InputBuffer(self,'inbuf',
                            BusConnection,
                            self.input_buffer_to_input_formatter_message_length_bytes,
                            self.input_buffer_to_input_formatter_message_ready,
                            self.input_buffer_to_input_formatter_message,
                            self.input_formatter_to_input_buffer_message_fetched,
                            self.input_buffer_to_output_buffer_seed_push,
                            self.input_buffer_to_output_buffer_nbbytes_push,
                            self.input_buffer_to_output_buffer_push_valid,
                            debug)
        inp = InputFormatter(self,'inp',
                             self.input_buffer_to_input_formatter_message,
                             self.input_buffer_to_input_formatter_message_ready,
                             self.input_formatter_to_l1_handler_padded_block,
                             self.input_buffer_to_input_formatter_message_length_bytes,
                             self.input_formatter_to_l1_handler_padded_block_ready,
                             self.input_formatter_to_input_buffer_message_fetched,
                             self.l1_handler_to_input_formatter_padded_block_consumed,
                             debug)
        L1 = L1_Handler(self, 'L1',
                        reset,
                        self.input_formatter_to_l1_handler_padded_block,
                        self.input_formatter_to_l1_handler_padded_block_ready,
                        self.l1_handler_to_input_formatter_padded_block_consumed,
                        self.l1_handler_to_l1_res_buffer_schedule,
                        self.l1_handler_to_l1_res_buffer_schedule_ready,
                        self.l2_handler_to_l1_handler_schedule_drained,
                        debug)
        L1buf = L1_res_Buffer(self, 'L1buf',
                              self.l1_handler_to_l1_res_buffer_schedule,
                              self.l2_handler_to_l1_res_buffer_schedule_address,
                              self.l1_res_buffer_to_l2_handler_schedule_word,
                              self.l1_res_buffer_to_l2_handler_schedule_word_ready,
                              self.l2_handler_to_l1_res_buffer_schedule_word_ready_consumed,
                              debug)
        L2 = L2_Handler(self, 'L2',
                        self.l2_handler_to_l1_res_buffer_schedule_address,
                        self.l1_res_buffer_to_l2_handler_schedule_word,
                        self.l2_handler_to_output_buffer_digest,
                        self.l1_res_buffer_to_l2_handler_schedule_word_ready,
                        self.l2_handler_to_l1_res_buffer_schedule_word_ready_consumed,
                        self.l2_handler_to_output_buffer_digest_ready,
                        reset,
                        self.output_buffer_to_l2_handler_digest_consumed,
                        self.l2_handler_to_l1_handler_schedule_drained,
                        debug)
        outbuf = OutputBuffer(self,'outbuf',
                              reset,
                              self.input_buffer_to_output_buffer_seed_push,
                              self.input_buffer_to_output_buffer_nbbytes_push,
                              self.input_buffer_to_output_buffer_push_valid,
                              self.l2_handler_to_output_buffer_digest,
                              self.l2_handler_to_output_buffer_digest_ready,
                              self.output_buffer_to_l2_handler_digest_consumed,
                              Seed_OUT,
                              NBbytes_OUT,
                              Digest_OUT,
                              Digest_Ready,
                              Digest_Fetched,
                              debug)
