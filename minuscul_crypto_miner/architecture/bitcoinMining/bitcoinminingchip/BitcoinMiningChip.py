import py4hw
from minuscul_crypto_miner.architecture.bus.bus import ResultBus
from minuscul_crypto_miner.architecture.difficultyValidator.NBitsDecoder import NBitsDecoder
from minuscul_crypto_miner.architecture.bitcoinMining.miningengine import Sha256Engine, BitcoinMiningCore, SecondBlockPrecompute
from minuscul_crypto_miner.architecture.bitcoinMining.bitcoinminingchip.UsartInterface import ByteStreamInterface
from minuscul_crypto_miner.architecture.bitcoinMining.bitcoinminingchip.UsartRx import UsartRx
from minuscul_crypto_miner.architecture.bitcoinMining.bitcoinminingchip.UsartTx import UsartTx
from minuscul_crypto_miner.architecture.bitcoinMining.bitcoinminingchip.ChipControl import ChipControl


class BitcoinMiningChip(py4hw.Logic):
    # Top-level mining chip, only external pins are reset + the two UART lines:
    #
    #   uart_rx -> UsartRx --ByteStream--> ChipControl --ByteStream--> UsartTx -> uart_tx
    #
    #   ChipControl --job fields (broadcast wires)--> Sha256Engine (shared midstate, once per job)
    #                                              \-> num_cores x BitcoinMiningCore
    #   each core ends in its own DifficultyValidator (hits only leave the core)
    #   cores -> ResultBus per group of cores_per_arbiter -> top ResultBus -> ChipControl
    #
    # Difficulty is checked inside every core, so the arbiters only ever carry
    # winning digests: a ResultBus moves ~1 transfer per 3 cycles, which
    # capped the chip at ~9 cores per arbiter when every digest went through
    # it (see performance_evaluation.md). The target comes from the job's own
    # `bits` field (nBits), it is not sent separately. Purely structural (no clock()).
    #
    # pipeline='fused' (default): each core's two SHA-256 stages are
    # Sha256Compressor (message schedule computed inside the round loop),
    # 64/l2_rounds_per_cycle + 1 cycles per hash; l1_words_per_cycle is unused.
    # pipeline='l1_l2': the original L1_Handler/L1_res_Buffer/L2_Handler +
    # Sha256CoreV1 finisher stages (L1-bound, ~28 cycles/hash at L1=2, L2=4).
    #
    # bitcoin_shortcuts=True (default, needs pipeline='fused'):
    #   - SecondBlockPrecompute runs the first 3 rounds of the second header
    #     block once per job (they only depend on merkle_tail/time/bits); every
    #     core's first compressor starts at round 3.
    #   - each core's finisher rejects at round 61 when the digest's top word
    #     already exceeds the target's (Sha256Compressor early_reject).
    #   - the job is interpreted in real Bitcoin byte order: the bits field is
    #     decoded as the little-endian nBits it is in a serialised header, and
    #     digests are compared to the target as little-endian numbers. (Hashing
    #     always uses the raw header bytes as sent, so it is unaffected.)
    #   Together: 64/k cycles per hash instead of 64/k + 1 (16 vs 17 at k=4).
    #   bitcoin_shortcuts=False keeps the previous big-endian interpretation.
    def __init__(self,parent,name,reset,uart_rx,uart_tx,
                 num_cores=4,cores_per_arbiter=4,clks_per_bit=16,pipeline='fused',bitcoin_shortcuts=True,
                 l1_words_per_cycle=2,l2_rounds_per_cycle=4,debug=False):
        super().__init__(parent,name)

        if num_cores < 1 or num_cores > 32:
            raise ValueError(f"num_cores must be in 1..32 (ACTIVE_CORES_MASK is 32 bits), got {num_cores}")
        if cores_per_arbiter < 1:
            raise ValueError(f"cores_per_arbiter must be >= 1, got {cores_per_arbiter}")
        if bitcoin_shortcuts and pipeline != 'fused':
            raise ValueError("bitcoin_shortcuts=True needs pipeline='fused'")

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

        # --- Shared midstate engine ---------------------------------------------
        sha256_engine_to_cores_midstate = self.wire('sha256_engine_to_cores_midstate',256)
        sha256_engine_to_control_midstate_ready = self.wire('sha256_engine_to_control_midstate_ready',1)
        Sha256Engine(self,'midstate_engine',reset,control_to_all_header_first64bytes,
                     control_to_sha256_engine_new_job,
                     sha256_engine_to_cores_midstate,sha256_engine_to_control_midstate_ready,debug)

        # --- Per-job precompute of the second block's first 3 rounds ----------
        # With the shortcuts on, Control waits for this (it follows the
        # midstate's low/high pattern 3 cycles later) instead of the midstate.
        if bitcoin_shortcuts:
            precompute_to_cores_state3 = self.wire('precompute_to_cores_state3',256)
            precompute_to_control_state3_ready = self.wire('precompute_to_control_state3_ready',1)
            SecondBlockPrecompute(self,'precompute',sha256_engine_to_cores_midstate,
                                  sha256_engine_to_control_midstate_ready,
                                  control_to_cores_merkle_tail,control_to_cores_time,control_to_cores_bits,
                                  precompute_to_cores_state3,precompute_to_control_state3_ready,debug)
            job_ready_to_control = precompute_to_control_state3_ready
        else:
            precompute_to_cores_state3 = None
            job_ready_to_control = sha256_engine_to_control_midstate_ready

        # --- Difficulty target, shared by every core's validator ---------------
        target = self.wire('nbits_decoder_to_cores_target',256)
        NBitsDecoder(self,'nbits_decoder',control_to_cores_bits,target,debug,
                     bitcoin_byte_order=bitcoin_shortcuts)

        # --- Mining cores ------------------------------------------------------
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

            BitcoinMiningCore(self,f'core_{i}',reset,sha256_engine_to_cores_midstate,enable,
                              control_to_cores_merkle_tail,control_to_cores_time,control_to_cores_bits,
                              hit_nonce,hit_nbbytes,hit_digest,hit_ready,hit_fetched,
                              lane_id=i,stride=num_cores,
                              nonce_start=nonce_start,nonce_stride=control_to_cores_nonce_stride,
                              idle_out=idle,target=target,pipeline=pipeline,
                              precomputed_state=precompute_to_cores_state3,
                              early_reject=bitcoin_shortcuts,bitcoin_byte_order=bitcoin_shortcuts,
                              l1_words_per_cycle=l1_words_per_cycle,
                              l2_rounds_per_cycle=l2_rounds_per_cycle,debug=debug)

            core_enables.append(enable)
            core_nonce_starts.append(nonce_start)
            core_idles.append(idle)
            core_outputs.append((hit_nonce,hit_nbbytes,hit_digest,hit_ready,hit_fetched))

        # --- Result arbiter tree (hits only) -------------------------------------
        # A core's validator holding an un-fetched hit counts as busy for the
        # drain, on top of each arbiter forwarding one.
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
        # A frame stalled for 20 character times (10 bits each) resyncs the parser.
        ChipControl(self,'control',reset,usart_rx_to_control,control_to_usart_tx,
                    control_to_sha256_engine_new_job,job_ready_to_control,
                    control_to_all_header_first64bytes,control_to_cores_merkle_tail,
                    control_to_cores_time,control_to_cores_bits,
                    core_enables,core_nonce_starts,control_to_cores_nonce_stride,core_idles,
                    arbiter_busys,top_arbiter_to_control_result_ready,
                    top_arbiter_to_control_nonce,top_arbiter_to_control_digest,
                    control_to_top_arbiter_result_fetched,
                    rx_timeout_cycles=20*10*clks_per_bit,debug=debug)
