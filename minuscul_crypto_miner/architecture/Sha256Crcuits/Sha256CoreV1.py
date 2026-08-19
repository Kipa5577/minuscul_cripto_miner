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
from minuscul_crypto_miner.architecture.Sha256Crcuits.components.L2_Handler_2x import L2_Handler_2x
from minuscul_crypto_miner.architecture.Sha256Crcuits.components.L2_Handler_4x import L2_Handler_4x
from minuscul_crypto_miner.architecture.Sha256Crcuits.components.L1_Handler_2x import L1_Handler_2x

_L2_VARIANTS = {1: L2_Handler, 2: L2_Handler_2x, 4: L2_Handler_4x}
_L1_VARIANTS = {1: L1_Handler, 2: L1_Handler_2x}


#This is the structural component
class Sha256CoreV1(py4hw.Logic):
    def __init__(self, parent, name, reset, BusConnection,
                 Seed_OUT, NBbytes_OUT, Digest_OUT, Digest_Ready, Digest_Fetched,
                 debug=False, l2_rounds_per_cycle=1, l1_words_per_cycle=1):
        super().__init__(parent, name)

        if l2_rounds_per_cycle not in _L2_VARIANTS:
            raise ValueError(f"l2_rounds_per_cycle must be one of {sorted(_L2_VARIANTS)}, got {l2_rounds_per_cycle}")
        if l1_words_per_cycle not in _L1_VARIANTS:
            raise ValueError(f"l1_words_per_cycle must be one of {sorted(_L1_VARIANTS)}, got {l1_words_per_cycle}")

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

        # L2_Handler's starting chaining value: standard SHA-256 IV, since
        # this core always hashes a message from scratch (never continues a
        # cached midstate -- see architecture/bitcoinMining for that).
        SHA256_STANDARD_IV = 0x6a09e667bb67ae853c6ef372a54ff53a510e527f9b05688c1f83d9ab5be0cd19
        self.standard_iv = self.wire("standard_iv",256)
        py4hw.Constant(self,'standard_iv_const',SHA256_STANDARD_IV,self.standard_iv)


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
        L1_Variant = _L1_VARIANTS[l1_words_per_cycle]
        L1 = L1_Variant(self, 'L1',
                        reset,
                        self.input_formatter_to_l1_handler_padded_block,
                        self.input_formatter_to_l1_handler_padded_block_ready,
                        self.l1_handler_to_input_formatter_padded_block_consumed,
                        self.l1_handler_to_l1_res_buffer_schedule,
                        self.l1_handler_to_l1_res_buffer_schedule_ready,
                        self.l2_handler_to_l1_handler_schedule_drained,
                        debug)
        # Extra L1_res_Buffer read ports for round-unrolled L2 variants
        # (L2_Handler_2x/L2_Handler_4x): a single read port only delivers
        # 1 schedule word/cycle no matter how far ahead you prefetch, so
        # computing >1 round/cycle needs that many parallel ports -- see
        # L1_res_Buffer's extra_ports and _L2_HandlerNx's docstring.
        extra_ports = []
        for p in range(1, l2_rounds_per_cycle):
            extra_ports.append((
                self.wire(f"l2_handler_to_l1_res_buffer_schedule_address_{p}",7),
                self.wire(f"l1_res_buffer_to_l2_handler_schedule_word_{p}",32),
            ))

        L1buf = L1_res_Buffer(self, 'L1buf',
                              self.l1_handler_to_l1_res_buffer_schedule,
                              self.l2_handler_to_l1_res_buffer_schedule_address,
                              self.l1_res_buffer_to_l2_handler_schedule_word,
                              self.l1_res_buffer_to_l2_handler_schedule_word_ready,
                              self.l2_handler_to_l1_res_buffer_schedule_word_ready_consumed,
                              debug,
                              extra_ports=extra_ports)

        L2_Variant = _L2_VARIANTS[l2_rounds_per_cycle]
        if l2_rounds_per_cycle == 1:
            L2 = L2_Variant(self, 'L2',
                            self.l2_handler_to_l1_res_buffer_schedule_address,
                            self.l1_res_buffer_to_l2_handler_schedule_word,
                            self.l2_handler_to_output_buffer_digest,
                            self.l1_res_buffer_to_l2_handler_schedule_word_ready,
                            self.l2_handler_to_l1_res_buffer_schedule_word_ready_consumed,
                            self.l2_handler_to_output_buffer_digest_ready,
                            reset,
                            self.output_buffer_to_l2_handler_digest_consumed,
                            self.l2_handler_to_l1_handler_schedule_drained,
                            self.standard_iv,
                            debug)
        else:
            addresses = [self.l2_handler_to_l1_res_buffer_schedule_address] + [a for a,v in extra_ports]
            vals = [self.l1_res_buffer_to_l2_handler_schedule_word] + [v for a,v in extra_ports]
            L2 = L2_Variant(self, 'L2',
                            addresses,
                            vals,
                            self.l2_handler_to_output_buffer_digest,
                            self.l1_res_buffer_to_l2_handler_schedule_word_ready,
                            self.l2_handler_to_l1_res_buffer_schedule_word_ready_consumed,
                            self.l2_handler_to_output_buffer_digest_ready,
                            reset,
                            self.output_buffer_to_l2_handler_digest_consumed,
                            self.l2_handler_to_l1_handler_schedule_drained,
                            self.standard_iv,
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
