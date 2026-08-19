import py4hw
from minuscul_crypto_miner.architecture.Sha256Crcuits import (
    Sha256CoreV1,
    L1_Handler,
    L2_Handler,
    OutputBuffer,
    L1_res_Buffer,
    L1BufferInterf,
)
from minuscul_crypto_miner.architecture.bus.bus import Bus, BusInterface, ResultBus
from minuscul_crypto_miner.architecture.difficultyValidator.DifficultyValidator import DifficultyValidator
from minuscul_crypto_miner.architecture.difficultyValidator.NBitsDecoder import NBitsDecoder
from minuscul_crypto_miner.architecture.bitcoinMining.Sha256Engine import Sha256Engine
from minuscul_crypto_miner.architecture.bitcoinMining.bitcoin_Nonce_iterator import bitcoin_Nonce_iterator
from minuscul_crypto_miner.architecture.bitcoinMining.DigestBridge import DigestBridge
from minuscul_crypto_miner.architecture.bitcoinMining.NonceTracker import NonceTracker


class BitcoinMinerEngine(py4hw.Logic):
    # Top-level Bitcoin double-SHA256 nonce-search array:
    #   Sha256Engine (shared midstate, once per job)
    #   N x [bitcoin_Nonce_iterator -> L1_Handler/L1_res_Buffer/L2_Handler[H_init=midstate]
    #        -> OutputBuffer -> DigestBridge -> Sha256CoreV1 (finisher) -> NonceTracker]
    #   -> ResultBus -> DifficultyValidator (target from NBitsDecoder(nBits))
    #
    # See docs: this replaces seedGenerator/Bus for the mining path entirely
    # -- each lane owns its own nonce range outright, no shared dispatch bus
    # is needed (Bus/seedGenerator remain used, unchanged, by the generic
    # single-hash testbenches).
    def __init__(self,parent,name,reset,
                 header_first64bytes,new_job,merkle_tail,time_field,bits,nBits,
                 result_ready,found_nonce_out,found_nbbytes_out,found_digest_out,result_fetched,
                 num_lanes=4,debug=False):
        super().__init__(parent,name)

        self.reset = self.addIn("reset",reset)
        self.header_first64bytes = self.addIn("header_first64bytes",header_first64bytes)
        self.new_job = self.addIn("new_job",new_job)
        self.merkle_tail = self.addIn("merkle_tail",merkle_tail)
        self.time_field = self.addIn("time_field",time_field)
        self.bits = self.addIn("bits",bits)
        self.nBits = self.addIn("nBits",nBits)
        self.result_ready = self.addOut("result_ready",result_ready)
        self.found_nonce_out = self.addOut("found_nonce_out",found_nonce_out)
        self.found_nbbytes_out = self.addOut("found_nbbytes_out",found_nbbytes_out)
        self.found_digest_out = self.addOut("found_digest_out",found_digest_out)
        self.result_fetched = self.addIn("result_fetched",result_fetched)

        self.debug = debug

        # --- Shared midstate over the first 64 (constant) header bytes -----
        midstate = self.wire("midstate",256)
        midstate_ready = self.wire("midstate_ready",1)
        Sha256Engine(self,'midstate_engine',reset,header_first64bytes,new_job,
                     midstate,midstate_ready,debug)

        # --- N nonce-search lanes -------------------------------------------
        engine_outputs = []
        for i in range(num_lanes):
            block_out = self.wire(f'lane_{i}_block',512)
            block_ready = self.wire(f'lane_{i}_block_ready',1)
            block_consumed = self.wire(f'lane_{i}_block_consumed',1)
            nonce_out = self.wire(f'lane_{i}_nonce',32)

            # start_enable=midstate_ready gates each lane's very first
            # activation directly on the shared midstate actually being
            # valid (see bitcoin_Nonce_iterator's docstring for why this
            # must be midstate_ready itself, not a latched copy of it).
            bitcoin_Nonce_iterator(self,f'nonce_iter_{i}',reset,midstate_ready,
                                    merkle_tail,time_field,bits,
                                    block_out,block_ready,block_consumed,nonce_out,
                                    lane_id=i,stride=num_lanes,debug=debug)

            l1_to_buffer = L1BufferInterf(self,f'lane_{i}_l1_to_buffer')
            l1_done = self.wire(f'lane_{i}_l1_done',1)
            schedule_drained = self.wire(f'lane_{i}_schedule_drained',1)

            L1_Handler(self,f'L1_{i}',reset,block_out,block_ready,block_consumed,
                       l1_to_buffer,l1_done,schedule_drained,debug)

            l2_address = self.wire(f'lane_{i}_l2_address',7)
            l2_word = self.wire(f'lane_{i}_l2_word',32)
            l2_word_ready = self.wire(f'lane_{i}_l2_word_ready',1)
            l2_word_ready_consumed = self.wire(f'lane_{i}_l2_word_ready_consumed',1)

            L1_res_Buffer(self,f'L1buf_{i}',l1_to_buffer,l2_address,l2_word,
                          l2_word_ready,l2_word_ready_consumed,debug)

            l2_digest1 = self.wire(f'lane_{i}_l2_digest1',256)
            l2_done = self.wire(f'lane_{i}_l2_done',1)
            l2_output_consumed = self.wire(f'lane_{i}_l2_output_consumed',1)

            L2_Handler(self,f'L2_{i}',l2_address,l2_word,l2_digest1,l2_word_ready,
                       l2_word_ready_consumed,l2_done,reset,l2_output_consumed,
                       schedule_drained,midstate,debug)

            # OutputBuffer's FIFO pairing isn't semantically used here (its
            # Seed_OUT/NBbytes_OUT are discarded -- DigestBridge only reads
            # Digest_OUT), but it still provides the multi-in-flight-capable
            # ready/fetched handshake stage 2 needs.
            stage1_seed_out_unused = self.wire(f'lane_{i}_stage1_seed_out_UNUSED',32)
            stage1_nbbytes_out_unused = self.wire(f'lane_{i}_stage1_nbbytes_out_UNUSED',8)
            digest1 = self.wire(f'lane_{i}_digest1',256)
            digest1_ready = self.wire(f'lane_{i}_digest1_ready',1)
            digest1_fetched = self.wire(f'lane_{i}_digest1_fetched',1)
            stage1_nbbytes_push = self.wire(f'lane_{i}_stage1_nbbytes_push',8)
            py4hw.Constant(self,f'lane_{i}_stage1_nbbytes_push_const',4,stage1_nbbytes_push)

            OutputBuffer(self,f'stage1_outbuf_{i}',reset,
                        nonce_out,stage1_nbbytes_push,block_consumed,
                        l2_digest1,l2_done,l2_output_consumed,
                        stage1_seed_out_unused,stage1_nbbytes_out_unused,digest1,
                        digest1_ready,digest1_fetched,debug)

            # --- Finisher: stock Sha256CoreV1, standard IV/padding, hashes digest1 ---
            slave_if = BusInterface(self,f'lane_{i}_stage2_bus')
            DigestBridge(self,f'digest_bridge_{i}',digest1,digest1_ready,digest1_fetched,
                        slave_if,debug)

            finisher_seed_out_unused = self.wire(f'lane_{i}_finisher_seed_out_UNUSED',512)
            finisher_nbbytes_out_unused = self.wire(f'lane_{i}_finisher_nbbytes_out_UNUSED',8)
            finisher_digest_out = self.wire(f'lane_{i}_finisher_digest_out',256)
            finisher_digest_ready = self.wire(f'lane_{i}_finisher_digest_ready',1)
            finisher_digest_fetched = self.wire(f'lane_{i}_finisher_digest_fetched',1)

            Sha256CoreV1(self,f'finisher_{i}',reset,slave_if,
                        finisher_seed_out_unused,finisher_nbbytes_out_unused,
                        finisher_digest_out,finisher_digest_ready,finisher_digest_fetched,
                        debug)

            # --- Nonce tracking: pair the finisher's digest back to its nonce ---
            lane_nonce_as_seed = self.wire(f'lane_{i}_nonce_as_seed',512)
            NonceTracker(self,f'nonce_tracker_{i}',nonce_out,block_consumed,
                        finisher_digest_ready,finisher_digest_fetched,
                        lane_nonce_as_seed,debug)

            lane_nbbytes = self.wire(f'lane_{i}_found_nbbytes',8)
            py4hw.Constant(self,f'lane_{i}_found_nbbytes_const',4,lane_nbbytes)

            engine_outputs.append((lane_nonce_as_seed,lane_nbbytes,finisher_digest_out,
                                   finisher_digest_ready,finisher_digest_fetched))

        # --- Merge lanes and validate against the job's difficulty ------------
        merged_seed_out = self.wire("merged_seed_out",512)
        merged_nbbytes_out = self.wire("merged_nbbytes_out",8)
        merged_digest_out = self.wire("merged_digest_out",256)
        merged_digest_ready = self.wire("merged_digest_ready",1)
        merged_digest_fetched = self.wire("merged_digest_fetched",1)

        ResultBus(self,'result_bus',engine_outputs,
                 merged_seed_out,merged_nbbytes_out,merged_digest_out,
                 merged_digest_ready,merged_digest_fetched,debug)

        target = self.wire("target",256)
        NBitsDecoder(self,'nbits_decoder',nBits,target,debug)

        DifficultyValidator(self,'validator',reset,target,
                            merged_digest_out,merged_digest_ready,merged_digest_fetched,
                            merged_seed_out,merged_nbbytes_out,
                            result_ready,found_nonce_out,found_nbbytes_out,found_digest_out,
                            result_fetched,debug)
