import py4hw
from minuscul_crypto_miner.architecture.Sha256Crcuits import (
    Sha256CoreV1,
    Sha256Compressor,
    L1_Handler,
    L1_Handler_2x,
    L2_Handler,
    L2_Handler_2x,
    L2_Handler_4x,
    OutputBuffer,
    L1_res_Buffer,
    L1BufferInterf,
)
from minuscul_crypto_miner.architecture.Sha256Crcuits.components.Sha256Compressor import ROUNDS_PER_CYCLE_OPTIONS
from minuscul_crypto_miner.architecture.bus.bus import BusInterface
from minuscul_crypto_miner.architecture.difficultyValidator.DifficultyValidator import DifficultyValidator
from minuscul_crypto_miner.architecture.bitcoinMining.miningengine.bitcoin_Nonce_iterator import bitcoin_Nonce_iterator
from minuscul_crypto_miner.architecture.bitcoinMining.miningengine.DigestBridge import DigestBridge
from minuscul_crypto_miner.architecture.bitcoinMining.miningengine.NonceTracker import NonceTracker

L1_VARIANTS = {1: L1_Handler, 2: L1_Handler_2x}
L2_VARIANTS = {1: L2_Handler, 2: L2_Handler_2x, 4: L2_Handler_4x}


class BitcoinMiningCore(py4hw.Logic):
    # One nonce-search lane, i.e. everything BitcoinMinerEngine does per lane
    # *except* the shared midstate Sha256Engine, which is fed in through
    # the midstate/start_enable ports so several cores can share one:
    #
    #   bitcoin_Nonce_iterator -> [first SHA-256, H_init=midstate] -> [finisher SHA-256]
    #   -> NonceTracker (+ optional DifficultyValidator)
    #
    # The two SHA-256 stages come in two flavours (pipeline=):
    #   'l1_l2' (default): L1_Handler/L1_res_Buffer/L2_Handler -> OutputBuffer
    #       -> DigestBridge -> Sha256CoreV1 finisher. The L1 schedule expansion
    #       is the bottleneck (~28 cycles/hash at L1=2, L2=4).
    #   'fused': two Sha256Compressor stages (schedule computed on the fly
    #       inside the round loop), 64/l2_rounds_per_cycle + 1 cycles/hash
    #       (17 at 4 rounds/cycle); l1_words_per_cycle is unused.
    #
    # Bitcoin shortcuts (pipeline='fused' only):
    #   precomputed_state: the job-wide state after the first 3 rounds of the
    #       second header block (SecondBlockPrecompute); the first compressor
    #       starts at round 3.
    #   early_reject: the finisher stops at round 61 when the digest's top
    #       word already exceeds the target's (needs target, and only makes
    #       sense with bitcoin_byte_order).
    #   bitcoin_byte_order: the validator compares the digest little-endian,
    #       as the Bitcoin network does.
    #
    # Outputs the (nonce_as_seed, nbbytes, digest, digest_ready, digest_fetched)
    # tuple ResultBus expects from each engine. Purely structural (no clock()),
    # see py4hw's leaf-only clocking.
    #
    # The lane's nonce offset/stride is either fixed at elaboration time
    # (lane_id/stride) or, when the nonce_start/nonce_stride wires are given,
    # sampled at runtime each time start_enable rises.
    # idle_out (optional) is 1 while no nonce of this lane is in flight
    # between L1 and the finisher's output.
    #
    # target (optional): when given, a DifficultyValidator is placed right
    # behind the finisher and the output tuple carries *hits only* (digest <=
    # target) instead of every digest. This keeps per-hash traffic inside the
    # core, so whatever merges several cores (ResultBus, ~1 transfer every 3
    # cycles) only ever sees the rare winning hashes and can't become the
    # throughput bottleneck. idle_out does not cover a hit the validator is
    # still holding -- that is visible as digest_ready itself, so a drain
    # check must watch both.
    def __init__(self,parent,name,reset,midstate,start_enable,
                 merkle_tail,time_field,bits,
                 nonce_as_seed_out,nbbytes_out,digest_out,digest_ready,digest_fetched,
                 lane_id=0,stride=1,nonce_start=None,nonce_stride=None,idle_out=None,
                 target=None,pipeline='l1_l2',
                 precomputed_state=None,early_reject=False,bitcoin_byte_order=False,
                 l1_words_per_cycle=2,l2_rounds_per_cycle=4,debug=False):
        super().__init__(parent,name)

        if pipeline == 'fused':
            if l2_rounds_per_cycle not in ROUNDS_PER_CYCLE_OPTIONS:
                raise ValueError(f"l2_rounds_per_cycle must be one of {ROUNDS_PER_CYCLE_OPTIONS} with pipeline='fused', got {l2_rounds_per_cycle}")
        elif pipeline == 'l1_l2':
            if l1_words_per_cycle not in L1_VARIANTS:
                raise ValueError(f"l1_words_per_cycle must be one of {sorted(L1_VARIANTS)}, got {l1_words_per_cycle}")
            if l2_rounds_per_cycle not in L2_VARIANTS:
                raise ValueError(f"l2_rounds_per_cycle must be one of {sorted(L2_VARIANTS)}, got {l2_rounds_per_cycle}")
        else:
            raise ValueError(f"pipeline must be 'l1_l2' or 'fused', got {pipeline!r}")
        if (precomputed_state is not None or early_reject) and pipeline != 'fused':
            raise ValueError("precomputed_state/early_reject need pipeline='fused'")
        if early_reject and (target is None or not bitcoin_byte_order):
            raise ValueError("early_reject needs target and bitcoin_byte_order=True")

        block_out = self.wire('block',512)
        block_ready = self.wire('block_ready',1)
        block_consumed = self.wire('block_consumed',1)
        nonce_out = self.wire('nonce',32)

        # start_enable gates each activation directly on the shared midstate
        # actually being valid (see bitcoin_Nonce_iterator's docstring for
        # why this must be midstate_ready itself, not a latched copy of it).
        bitcoin_Nonce_iterator(self,'nonce_iter',reset,start_enable,
                               merkle_tail,time_field,bits,
                               block_out,block_ready,block_consumed,nonce_out,
                               lane_id=lane_id,stride=stride,
                               nonce_start=nonce_start,nonce_stride=nonce_stride,debug=debug)

        if target is None:
            # every digest leaves the core
            finisher_digest, finisher_ready, finisher_fetched = digest_out, digest_ready, digest_fetched
            tracked_nonce, nbbytes = nonce_as_seed_out, nbbytes_out
        else:
            # digests stay inside the core until the validator passes one
            finisher_digest = self.wire('finisher_to_validator_digest',256)
            finisher_ready = self.wire('finisher_to_validator_digest_ready',1)
            finisher_fetched = self.wire('validator_to_finisher_digest_fetched',1)
            tracked_nonce = self.wire('nonce_tracker_to_validator_nonce',512)
            nbbytes = self.wire('nbbytes',8)

        if pipeline == 'fused':
            self._build_fused_stages(reset,midstate,block_out,block_ready,block_consumed,
                                     finisher_digest,finisher_ready,finisher_fetched,
                                     l2_rounds_per_cycle,precomputed_state,
                                     target if early_reject else None,debug)
        else:
            self._build_l1_l2_stages(reset,midstate,block_out,block_ready,block_consumed,nonce_out,
                                     finisher_digest,finisher_ready,finisher_fetched,
                                     l1_words_per_cycle,l2_rounds_per_cycle,debug)

        # --- Nonce tracking: pair the finisher's digest back to its nonce ---
        NonceTracker(self,'nonce_tracker',nonce_out,block_consumed,
                     finisher_ready,finisher_fetched,
                     tracked_nonce,empty_out=idle_out,debug=debug)

        py4hw.Constant(self,'nbbytes_const',4,nbbytes)

        if target is not None:
            DifficultyValidator(self,'validator',reset,target,
                                finisher_digest,finisher_ready,finisher_fetched,
                                tracked_nonce,nbbytes,
                                digest_ready,nonce_as_seed_out,nbbytes_out,digest_out,
                                digest_fetched,debug,bitcoin_byte_order=bitcoin_byte_order)

    def _build_l1_l2_stages(self,reset,midstate,block_out,block_ready,block_consumed,nonce_out,
                            finisher_digest,finisher_ready,finisher_fetched,
                            l1_words_per_cycle,l2_rounds_per_cycle,debug):
        # pipeline='l1_l2': L1_Handler/L1_res_Buffer/L2_Handler[H_init=midstate]
        # -> OutputBuffer -> DigestBridge -> Sha256CoreV1 (finisher)
        l1_to_buffer = L1BufferInterf(self,'l1_to_buffer')
        l1_done = self.wire('l1_done',1)
        schedule_drained = self.wire('schedule_drained',1)

        L1_VARIANTS[l1_words_per_cycle](self,'L1',reset,block_out,block_ready,block_consumed,
                                         l1_to_buffer,l1_done,schedule_drained,debug)

        l2_address = self.wire('l2_address',7)
        l2_word = self.wire('l2_word',32)
        l2_word_ready = self.wire('l2_word_ready',1)
        l2_word_ready_consumed = self.wire('l2_word_ready_consumed',1)

        # Extra L1_res_Buffer read ports for round-unrolled L2 variants
        # (L2_Handler_2x/L2_Handler_4x): mirrors Sha256CoreV1.py's
        # identical extra_ports construction.
        extra_ports = []
        for p in range(1, l2_rounds_per_cycle):
            extra_ports.append((
                self.wire(f'l2_address_{p}',7),
                self.wire(f'l2_word_{p}',32),
            ))

        L1_res_Buffer(self,'L1buf',l1_to_buffer,l2_address,l2_word,
                      l2_word_ready,l2_word_ready_consumed,debug,
                      extra_ports=extra_ports)

        l2_digest1 = self.wire('l2_digest1',256)
        l2_done = self.wire('l2_done',1)
        l2_output_consumed = self.wire('l2_output_consumed',1)

        L2_Variant = L2_VARIANTS[l2_rounds_per_cycle]
        if l2_rounds_per_cycle == 1:
            L2_Variant(self,'L2',l2_address,l2_word,l2_digest1,l2_word_ready,
                       l2_word_ready_consumed,l2_done,reset,l2_output_consumed,
                       schedule_drained,midstate,debug)
        else:
            addresses = [l2_address] + [a for a,v in extra_ports]
            vals = [l2_word] + [v for a,v in extra_ports]
            L2_Variant(self,'L2',addresses,vals,l2_digest1,l2_word_ready,
                       l2_word_ready_consumed,l2_done,reset,l2_output_consumed,
                       schedule_drained,midstate,debug)

        # OutputBuffer's FIFO pairing isn't semantically used here (its
        # Seed_OUT/NBbytes_OUT are discarded -- DigestBridge only reads
        # Digest_OUT), but it still provides the multi-in-flight-capable
        # ready/fetched handshake stage 2 needs.
        stage1_seed_out_unused = self.wire('stage1_seed_out_UNUSED',32)
        stage1_nbbytes_out_unused = self.wire('stage1_nbbytes_out_UNUSED',8)
        digest1 = self.wire('digest1',256)
        digest1_ready = self.wire('digest1_ready',1)
        digest1_fetched = self.wire('digest1_fetched',1)
        stage1_nbbytes_push = self.wire('stage1_nbbytes_push',8)
        py4hw.Constant(self,'stage1_nbbytes_push_const',4,stage1_nbbytes_push)

        OutputBuffer(self,'stage1_outbuf',reset,
                     nonce_out,stage1_nbbytes_push,block_consumed,
                     l2_digest1,l2_done,l2_output_consumed,
                     stage1_seed_out_unused,stage1_nbbytes_out_unused,digest1,
                     digest1_ready,digest1_fetched,debug)

        # --- Finisher: stock Sha256CoreV1, standard IV/padding, hashes digest1 ---
        slave_if = BusInterface(self,'stage2_bus')
        DigestBridge(self,'digest_bridge',digest1,digest1_ready,digest1_fetched,
                     slave_if,debug)

        finisher_seed_out_unused = self.wire('finisher_seed_out_UNUSED',512)
        finisher_nbbytes_out_unused = self.wire('finisher_nbbytes_out_UNUSED',8)

        Sha256CoreV1(self,'finisher',reset,slave_if,
                     finisher_seed_out_unused,finisher_nbbytes_out_unused,
                     finisher_digest,finisher_ready,finisher_fetched,
                     debug,l2_rounds_per_cycle=l2_rounds_per_cycle,l1_words_per_cycle=l1_words_per_cycle)

    def _build_fused_stages(self,reset,midstate,block_out,block_ready,block_consumed,
                            finisher_digest,finisher_ready,finisher_fetched,
                            rounds_per_cycle,precomputed_state,reject_target,debug):
        # pipeline='fused': two Sha256Compressor stages, schedule computed on
        # the fly (no L1_Handler/L1_res_Buffer), 64/rounds_per_cycle + 1 cycles
        # per block each, chained directly:
        #   sha1: second header block, H_init = midstate
        #   sha2: digest1 with its fixed 32-byte padding, H_init = standard IV
        SHA256_STANDARD_IV = 0x6a09e667bb67ae853c6ef372a54ff53a510e527f9b05688c1f83d9ab5be0cd19
        standard_iv = self.wire('standard_iv',256)
        py4hw.Constant(self,'standard_iv_const',SHA256_STANDARD_IV,standard_iv)

        sha1_to_sha2_digest = self.wire('sha1_to_sha2_digest',256)
        sha1_to_sha2_digest_ready = self.wire('sha1_to_sha2_digest_ready',1)
        sha2_to_sha1_digest_consumed = self.wire('sha2_to_sha1_digest_consumed',1)

        Sha256Compressor(self,'sha1',reset,block_out,block_ready,block_consumed,midstate,
                         sha1_to_sha2_digest,sha1_to_sha2_digest_ready,sha2_to_sha1_digest_consumed,
                         rounds_per_cycle=rounds_per_cycle,
                         start_round=3 if precomputed_state is not None else 0,
                         state_init=precomputed_state,debug=debug)
        Sha256Compressor(self,'sha2',reset,sha1_to_sha2_digest,sha1_to_sha2_digest_ready,
                         sha2_to_sha1_digest_consumed,standard_iv,
                         finisher_digest,finisher_ready,finisher_fetched,
                         rounds_per_cycle=rounds_per_cycle,pad_digest_input=True,
                         early_reject=reject_target is not None,reject_target=reject_target,debug=debug)
