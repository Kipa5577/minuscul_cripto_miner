import py4hw
from minuscul_crypto_miner.architecture.bus.bus import ResultBus
from minuscul_crypto_miner.architecture.difficultyValidator.DifficultyValidator import DifficultyValidator
from minuscul_crypto_miner.architecture.difficultyValidator.NBitsDecoder import NBitsDecoder
from minuscul_crypto_miner.architecture.bitcoinMining.miningengine.Sha256Engine import Sha256Engine
from minuscul_crypto_miner.architecture.bitcoinMining.miningengine.BitcoinMiningCore import (
    BitcoinMiningCore,
    L1_VARIANTS,
    L2_VARIANTS,
)


class BitcoinMinerEngine(py4hw.Logic):
    # Top-level Bitcoin double-SHA256 nonce-search array:
    #   Sha256Engine (shared midstate, once per job)
    #   N x BitcoinMiningCore [bitcoin_Nonce_iterator -> L1_Handler/L1_res_Buffer/L2_Handler[H_init=midstate]
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
                 num_lanes=4,l1_words_per_cycle=2,l2_rounds_per_cycle=4,debug=False):
        super().__init__(parent,name)

        if l1_words_per_cycle not in L1_VARIANTS:
            raise ValueError(f"l1_words_per_cycle must be one of {sorted(L1_VARIANTS)}, got {l1_words_per_cycle}")
        if l2_rounds_per_cycle not in L2_VARIANTS:
            raise ValueError(f"l2_rounds_per_cycle must be one of {sorted(L2_VARIANTS)}, got {l2_rounds_per_cycle}")

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
            lane_nonce_as_seed = self.wire(f'lane_{i}_nonce_as_seed',512)
            lane_nbbytes = self.wire(f'lane_{i}_found_nbbytes',8)
            finisher_digest_out = self.wire(f'lane_{i}_finisher_digest_out',256)
            finisher_digest_ready = self.wire(f'lane_{i}_finisher_digest_ready',1)
            finisher_digest_fetched = self.wire(f'lane_{i}_finisher_digest_fetched',1)

            BitcoinMiningCore(self,f'lane_{i}',reset,midstate,midstate_ready,
                              merkle_tail,time_field,bits,
                              lane_nonce_as_seed,lane_nbbytes,finisher_digest_out,
                              finisher_digest_ready,finisher_digest_fetched,
                              lane_id=i,stride=num_lanes,
                              l1_words_per_cycle=l1_words_per_cycle,
                              l2_rounds_per_cycle=l2_rounds_per_cycle,debug=debug)

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
