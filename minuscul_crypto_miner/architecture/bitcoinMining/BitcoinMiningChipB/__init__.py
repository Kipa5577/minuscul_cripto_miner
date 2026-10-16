"""Bitcoin mining chip, variant B: fully pipelined cores (one double-SHA256 per cycle per core)."""

from minuscul_crypto_miner.architecture.bitcoinMining.BitcoinMiningChipB.PipelinedMiningCore import PipelinedMiningCore
from minuscul_crypto_miner.architecture.bitcoinMining.BitcoinMiningChipB.BitcoinMiningChipB import BitcoinMiningChipB

__all__ = [
    "PipelinedMiningCore",
    "BitcoinMiningChipB",
]
