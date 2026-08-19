"""Sweeps NUM_ENGINES through benchmark_multiple_Sha256CoreV1.py and plots
the resulting throughput / cycle metrics to find the optimum engine count.

Each sweep point runs the benchmark in its own subprocess (py4hw builds a
fresh HWSystem with module-level wire names, so reusing one process across
engine counts would collide) and parses the metrics it prints to stdout.
"""

import re
import subprocess
import sys
from pathlib import Path

import matplotlib.pyplot as plt

BENCHMARK_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "tb_of_multiple_Sha256CoreV1.py"
    / "benchmark_multiple_Sha256CoreV1.py"
)

# Sweep variables
NUM_ENGINES_SWEEP = [1, 2, 4, 6, 8, 10, 12, 16, 20, 24, 32]
NUM_BENCHMARK_HASHES = 200  # kept constant across the sweep

METRIC_PATTERNS = {
    "hashes_per_second": re.compile(r"estimated throughput\s+= ([\d,]+) hashes/s"),
    "hashes_per_second_per_engine": re.compile(r"estimated throughput / engine\s+= ([\d,]+) hashes/s"),
    "avg_cycles_per_hash": re.compile(r"avg cycles / hash \(aggregate\)\s+= ([\d.]+)"),
}


def run_benchmark(num_engines):
    env = {
        "BENCH_NUM_ENGINES": str(num_engines),
        "BENCH_NUM_HASHES": str(NUM_BENCHMARK_HASHES),
    }
    result = subprocess.run(
        [sys.executable, str(BENCHMARK_SCRIPT)],
        capture_output=True,
        text=True,
        env={**__import__("os").environ, **env},
        check=True,
    )
    output = result.stdout
    metrics = {}
    for name, pattern in METRIC_PATTERNS.items():
        match = pattern.search(output)
        if match is None:
            raise RuntimeError(f"could not find '{name}' in benchmark output:\n{output}")
        metrics[name] = float(match.group(1).replace(",", ""))
    return metrics


def main():
    results = {name: [] for name in METRIC_PATTERNS}

    for num_engines in NUM_ENGINES_SWEEP:
        print(f"--- running benchmark: NUM_ENGINES={num_engines} ---")
        metrics = run_benchmark(num_engines)
        for name, value in metrics.items():
            results[name].append(value)
        print(f"    throughput={metrics['hashes_per_second']:,.0f} hashes/s, "
              f"per-engine={metrics['hashes_per_second_per_engine']:,.0f} hashes/s, "
              f"avg cycles/hash={metrics['avg_cycles_per_hash']:.2f}")

    fig, axes = plt.subplots(3, 1, figsize=(8, 10), sharex=True)

    axes[0].plot(NUM_ENGINES_SWEEP, results["hashes_per_second"], marker="o")
    axes[0].set_ylabel("hashes/s")
    axes[0].set_title("Estimated throughput (aggregate)")

    axes[1].plot(NUM_ENGINES_SWEEP, results["hashes_per_second_per_engine"], marker="o", color="tab:orange")
    axes[1].set_ylabel("hashes/s per engine")
    axes[1].set_title("Estimated throughput / engine")

    axes[2].plot(NUM_ENGINES_SWEEP, results["avg_cycles_per_hash"], marker="o", color="tab:green")
    axes[2].set_ylabel("cycles / hash")
    axes[2].set_title("Avg cycles / hash (aggregate)")
    axes[2].set_xlabel("NUM_ENGINES")

    for ax in axes:
        ax.grid(True, linestyle="--", alpha=0.5)

    fig.tight_layout()
    plt.show()


if __name__ == "__main__":
    main()
