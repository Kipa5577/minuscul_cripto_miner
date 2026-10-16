import py4hw
from minuscul_crypto_miner.architecture.bus.bus import ResultBus
from minuscul_crypto_miner.architecture.difficultyValidator.NBitsDecoder import NBitsDecoder
from minuscul_crypto_miner.architecture.bitcoinMining.miningengine import Sha256Engine, SecondBlockPrecompute
from minuscul_crypto_miner.architecture.bitcoinMining.bitcoinminingchip.UsartInterface import ByteStreamInterface
from minuscul_crypto_miner.architecture.bitcoinMining.bitcoinminingchip.UsartRx import UsartRx
from minuscul_crypto_miner.architecture.bitcoinMining.bitcoinminingchip.UsartTx import UsartTx
from minuscul_crypto_miner.architecture.bitcoinMining.bitcoinminingchip.ChipControl import ChipControl
from minuscul_crypto_miner.architecture.bitcoinMining.BitcoinMiningChipB.PipelinedMiningCore import PipelinedMiningCore


class BitcoinMiningChipB(py4hw.Logic):
    # Chip variant B: same pins, USART protocol, registers, Control and
    # result tree as BitcoinMiningChip (chip A), but every core is a fully
    # pipelined PipelinedMiningCore: one double-SHA256 per clock cycle per
    # core, instead of one per 16 cycles.
    #
    #   uart_rx -> UsartRx --ByteStream--> ChipControl --ByteStream--> UsartTx -> uart_tx
    #
    #   ChipControl --job fields (broadcast wires)--> Sha256Engine (shared midstate, once per job)
    #                                              -> SecondBlockPrecompute (rounds 0-2, once per job)
    #                                              -> num_cores x PipelinedMiningCore
    #   each core compares in its last stage (hits only leave the core)
    #   cores -> ResultBus per group of cores_per_arbiter -> top ResultBus -> ChipControl
    #
    # Always Bitcoin byte order and rounds 0-2 precomputed (chip A's
    # bitcoin_shortcuts=True behaviour); the round-61 early reject is not
    # used, it buys no throughput in a fully pipelined core.
    #
    # rounds_per_stage (1/2/4): rounds chained per pipeline stage. Throughput
    # is 1 hash/cycle/core either way; it trades pipeline depth (127/64/33
    # stages of registers) against logic per stage (clock frequency). The
    # default 4 matches chip A's default 4 rounds/cycle logic depth, so the
    # two chips can be compared at the same clock. Purely structural (no clock()).
    def __init__(self,parent,name,reset,uart_rx,uart_tx,
                 num_cores=4,cores_per_arbiter=4,clks_per_bit=16,
                 rounds_per_stage=4,hit_fifo_depth=4,debug=False):
        super().__init__(parent,name)

        if num_cores < 1 or num_cores > 32:
            raise ValueError(f"num_cores must be in 1..32 (ACTIVE_CORES_MASK is 32 bits), got {num_cores}")
        if cores_per_arbiter < 1:
            raise ValueError(f"cores_per_arbiter must be >= 1, got {cores_per_arbiter}")

        self.reset = self.addIn("reset",reset)
        self.uart_rx = self.addIn("uart_rx",uart_rx)
        self.uart_tx = self.addOut("uart_tx",uart_tx)

        # --- USART ---------------------------------------------------------
        usart_rx_to_control = ByteStreamInterface(self,'usart_rx_to_control')
        control_to_usart_tx = ByteStreamInterface(self,'control_to_usart_tx')
        UsartRx(self,'usart_rx',uart_rx,usart_rx_to_control,clks_per_bit,debug)
        UsartTx(self,'usart_tx',uart_tx,control_to_usart_tx,clks_per_bit,debug)

        # --- Job broadcast wires ---------------------------------------------
        control_to_sha256_engine_new_job = self.wire('control_to_sha256_engine_new_job',1)
        control_to_all_header_first64bytes = self.wire('control_to_all_header_first64bytes',512)
        control_to_cores_merkle_tail = self.wire('control_to_cores_merkle_tail',32)
        control_to_cores_time = self.wire('control_to_cores_time',32)
        control_to_cores_bits = self.wire('control_to_cores_bits',32)
        control_to_cores_nonce_stride = self.wire('control_to_cores_nonce_stride',32)

        # --- Shared midstate engine + per-job precompute of rounds 0-2 ----------
        # Control waits for state3_ready (it follows the midstate's low/high
        # pattern 3 cycles later) before enabling the cores.
        sha256_engine_to_cores_midstate = self.wire('sha256_engine_to_cores_midstate',256)
        sha256_engine_to_precompute_midstate_ready = self.wire('sha256_engine_to_precompute_midstate_ready',1)
        Sha256Engine(self,'midstate_engine',reset,control_to_all_header_first64bytes,
                     control_to_sha256_engine_new_job,
                     sha256_engine_to_cores_midstate,sha256_engine_to_precompute_midstate_ready,debug)

        precompute_to_cores_state3 = self.wire('precompute_to_cores_state3',256)
        precompute_to_control_state3_ready = self.wire('precompute_to_control_state3_ready',1)
        SecondBlockPrecompute(self,'precompute',sha256_engine_to_cores_midstate,
                              sha256_engine_to_precompute_midstate_ready,
                              control_to_cores_merkle_tail,control_to_cores_time,control_to_cores_bits,
                              precompute_to_cores_state3,precompute_to_control_state3_ready,debug)

        # --- Difficulty target, shared by every core's last stage ----------------
        target = self.wire('nbits_decoder_to_cores_target',256)
        NBitsDecoder(self,'nbits_decoder',control_to_cores_bits,target,debug,bitcoin_byte_order=True)

        # --- Fully pipelined mining cores -----------------------------------------
        core_enables = []
        core_nonce_starts = []
        core_idles = []
        core_outputs = []
        for i in range(num_cores):
            enable = self.wire(f'control_to_core_{i}_enable',1)
            nonce_start = self.wire(f'control_to_core_{i}_nonce_start',32)
            idle = self.wire(f'core_{i}_to_control_idle',1)

            hit_nonce = self.wire(f'core_{i}_to_arbiter_hit_nonce',512)
            hit_nbbytes = self.wire(f'core_{i}_to_arbiter_hit_nbbytes',8)
            hit_digest = self.wire(f'core_{i}_to_arbiter_hit_digest',256)
            hit_ready = self.wire(f'core_{i}_to_arbiter_hit_ready',1)
            hit_fetched = self.wire(f'arbiter_to_core_{i}_hit_fetched',1)

            PipelinedMiningCore(self,f'core_{i}',reset,enable,
                                sha256_engine_to_cores_midstate,precompute_to_cores_state3,
                                control_to_cores_merkle_tail,control_to_cores_time,control_to_cores_bits,
                                nonce_start,control_to_cores_nonce_stride,target,
                                hit_nonce,hit_nbbytes,hit_digest,hit_ready,hit_fetched,idle,
                                rounds_per_stage=rounds_per_stage,hit_fifo_depth=hit_fifo_depth,debug=debug)

            core_enables.append(enable)
            core_nonce_starts.append(nonce_start)
            core_idles.append(idle)
            core_outputs.append((hit_nonce,hit_nbbytes,hit_digest,hit_ready,hit_fetched))

        # --- Result arbiter tree (hits only) -------------------------------------
        arbiter_busys = [hit_ready for _,_,_,hit_ready,_ in core_outputs]
        group_outputs = []
        for g, first in enumerate(range(0,num_cores,cores_per_arbiter)):
            group = core_outputs[first:first+cores_per_arbiter]

            merged_nonce = self.wire(f'arbiter_{g}_to_top_arbiter_nonce',512)
            merged_nbbytes = self.wire(f'arbiter_{g}_to_top_arbiter_nbbytes',8)
            merged_digest = self.wire(f'arbiter_{g}_to_top_arbiter_digest',256)
            merged_ready = self.wire(f'arbiter_{g}_to_top_arbiter_ready',1)
            merged_fetched = self.wire(f'top_arbiter_to_arbiter_{g}_fetched',1)
            ResultBus(self,f'arbiter_{g}',group,
                      merged_nonce,merged_nbbytes,merged_digest,merged_ready,merged_fetched,debug)

            arbiter_busys.append(merged_ready)
            group_outputs.append((merged_nonce,merged_nbbytes,merged_digest,merged_ready,merged_fetched))

        top_arbiter_to_control_nonce = self.wire('top_arbiter_to_control_nonce',512)
        top_arbiter_to_control_nbbytes_UNUSED = self.wire('top_arbiter_to_control_nbbytes_UNUSED',8)
        top_arbiter_to_control_digest = self.wire('top_arbiter_to_control_digest',256)
        top_arbiter_to_control_result_ready = self.wire('top_arbiter_to_control_result_ready',1)
        control_to_top_arbiter_result_fetched = self.wire('control_to_top_arbiter_result_fetched',1)
        ResultBus(self,'top_arbiter',group_outputs,
                  top_arbiter_to_control_nonce,top_arbiter_to_control_nbbytes_UNUSED,
                  top_arbiter_to_control_digest,top_arbiter_to_control_result_ready,
                  control_to_top_arbiter_result_fetched,debug)

        # --- Control ---------------------------------------------------------------
        # Unchanged from chip A. A frame stalled for 20 character times resyncs the parser.
        ChipControl(self,'control',reset,usart_rx_to_control,control_to_usart_tx,
                    control_to_sha256_engine_new_job,precompute_to_control_state3_ready,
                    control_to_all_header_first64bytes,control_to_cores_merkle_tail,
                    control_to_cores_time,control_to_cores_bits,
                    core_enables,core_nonce_starts,control_to_cores_nonce_stride,core_idles,
                    arbiter_busys,top_arbiter_to_control_result_ready,
                    top_arbiter_to_control_nonce,top_arbiter_to_control_digest,
                    control_to_top_arbiter_result_fetched,
                    rx_timeout_cycles=20*10*clks_per_bit,debug=debug)
