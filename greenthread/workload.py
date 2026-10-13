"""Reproducible CPU workloads, run as child processes so OS controls apply to them.

Protocol: the child prints "READY", blocks until it reads "GO" on stdin (so the
scheduler can apply EcoQoS / CPU Sets / priority *before* any work happens),
runs, then prints one JSON line with its own timing.

Kinds:
  hash      - SHA-256 over buffers in T threads (hashlib releases the GIL)
  compress  - zlib level-6 compression in T threads (releases the GIL)
  matmul    - NumPy matrix multiply; BLAS threads set via environment
  probe     - interactive latency probe: 2 ms of work every 20 ms, reports lateness
Work size is in abstract "units" (hash/compress: MiB processed; matmul: GFLOP).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
import zlib

KINDS = ("hash", "compress", "matmul")


def _buf(n: int, seed: int = 7) -> bytes:
    # deterministic, moderately compressible data
    block = bytes((i * seed + (i >> 5)) & 0xFF for i in range(4096))
    return (block * (n // 4096 + 1))[:n]


def run_hash(units: float, threads: int):
    chunk = _buf(1 << 20)
    per = max(1, int(round(units / threads)))

    def worker():
        h = hashlib.sha256()
        for _ in range(per):
            h.update(chunk)
        return h.hexdigest()
    ts = [threading.Thread(target=worker) for _ in range(threads)]
    [t.start() for t in ts]
    [t.join() for t in ts]


def run_compress(units: float, threads: int):
    chunk = _buf(1 << 20, seed=13)
    per = max(1, int(round(units / threads)))

    def worker():
        for _ in range(per):
            zlib.compress(chunk, 6)
    ts = [threading.Thread(target=worker) for _ in range(threads)]
    [t.start() for t in ts]
    [t.join() for t in ts]


def run_matmul(units: float, threads: int):
    import numpy as np
    n = 512
    a = np.random.default_rng(1).random((n, n))
    flops = 2 * n ** 3
    reps = max(1, int(round(units * 1e9 / flops)))
    for _ in range(reps):
        a = (a @ a) / n


def run_probe(duration_s: float, period_ms: float = 20.0, work_ms: float = 2.0):
    lat, end = [], time.perf_counter() + duration_s
    nxt = time.perf_counter()
    while time.perf_counter() < end:
        nxt += period_ms / 1000
        sleep = nxt - time.perf_counter()
        if sleep > 0:
            time.sleep(sleep)
        woke = time.perf_counter()
        x, t_end = 0, woke + work_ms / 1000
        while time.perf_counter() < t_end:
            x += 1
        lat.append((time.perf_counter() - nxt) * 1000)  # response time vs. ideal
    lat.sort()
    q = lambda p: lat[min(len(lat) - 1, int(p * len(lat)))] if lat else 0.0
    return {"n": len(lat), "p50_ms": q(0.5), "p95_ms": q(0.95), "p99_ms": q(0.99),
            "mean_ms": sum(lat) / max(1, len(lat))}


def main(argv=None):
    argv = argv or sys.argv[1:]
    kind, units, threads = argv[0], float(argv[1]), int(argv[2])
    print("READY", flush=True)
    line = sys.stdin.readline().strip()
    if line != "GO":
        return 2
    t0, c0 = time.perf_counter(), time.process_time()
    extra = {}
    if kind == "hash":
        run_hash(units, threads)
    elif kind == "compress":
        run_compress(units, threads)
    elif kind == "matmul":
        run_matmul(units, threads)
    elif kind == "probe":
        extra = run_probe(units)
    else:
        print(json.dumps({"error": f"unknown kind {kind}"}), flush=True)
        return 3
    print(json.dumps({"kind": kind, "units": units, "threads": threads,
                      "wall_s": time.perf_counter() - t0, "cpu_s": time.process_time() - c0,
                      "pid": os.getpid(), **extra}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
