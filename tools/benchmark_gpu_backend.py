"""Benchmark the dense WS batch backend on the current machine.

Usage:
    python tools/benchmark_gpu_backend.py --rows 1000000

The script intentionally benchmarks the numeric batch stage only. A full
optimizer comparison must use the same candidate planner and search policy.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

# Running this file directly puts ``tools`` on sys.path. Add the application
# root so the benchmark uses the same imports as the GUI and test suite.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.gpu_optimizer import ROW_COUNT, cuda_available, score_melee_ws_batch


def make_rows(count: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    rows = np.zeros((count, ROW_COUNT), dtype=np.float64)
    rows[:, 0] = rng.uniform(100, 180, count)
    rows[:, 1] = rng.uniform(10, 40, count)
    rows[:, 2] = rng.uniform(50, 130, count)
    rows[:, 3] = rng.uniform(5, 30, count)
    rows[:, 4] = rng.uniform(80, 220, count)
    rows[:, 5] = rng.uniform(1.0, 2.0, count)
    rows[:, 6] = rng.uniform(1.0, 1.5, count)
    rows[:, 7] = rng.integers(1, 6, count)
    rows[:, 8] = rng.integers(0, 2, count)
    rows[:, 9:11] = rng.uniform(700, 1800, (count, 2))
    rows[:, 13:15] = rng.uniform(0, 0.25, (count, 2))
    rows[:, 15] = rng.uniform(900, 1600, count)
    rows[:, 16:19] = rng.uniform(0, 0.8, (count, 3))
    rows[:, 19:29] = rng.uniform(0, 0.4, (count, 10))
    rows[:, 30:32] = rng.uniform(0.7, 0.99, (count, 2))
    rows[:, 32] = rng.uniform(180, 900, count)
    rows[:, 33] = rng.uniform(0, 0.6, count)
    rows[:, 34] = rng.uniform(1000, 3000, count)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=250_000)
    parser.add_argument("--seed", type=int, default=20260821)
    args = parser.parse_args()
    rows = make_rows(max(1, args.rows), args.seed)

    for label, prefer_gpu in (("vectorized CPU", False), ("CUDA/CPU auto", True)):
        started = time.perf_counter()
        result = score_melee_ws_batch(rows, prefer_gpu=prefer_gpu)
        elapsed = time.perf_counter() - started
        print(f"{label}: {elapsed:.3f}s ({len(result) / elapsed:,.0f} rows/s)")
    print(f"CUDA available: {cuda_available()}")


if __name__ == "__main__":
    main()
