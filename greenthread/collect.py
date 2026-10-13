"""Training-data collection: run the workload matrix under every supported mode
on *this* machine and record runtime, CPU time, background load and (on battery)
measured energy. Output: CSV consumed by `greenthread train`."""
from __future__ import annotations

import csv
import random
import subprocess
import sys
import time
from pathlib import Path

import psutil

from .accounting import BatteryCarbonLedger, PowerModel
from .backends.base import Backend
from .carbon import StaticSource, CarbonSignal
from .config import Config, PACKAGE_ROOT
from .engine import Engine
from .jobs import Decision, Job
from .policy import Policy
from .telemetry import PowerSampler

FIELDS = ["sample_id", "timestamp", "kind", "units", "threads", "mode", "runtime_s", "cpu_s",
          "bg_load", "bg_threads", "on_ac", "logical_cpus", "physical_cores", "energy_wh_measured",
          "energy_wh_model", "battery_pct", "control_failures", "machine"]


class FixedModePolicy(Policy):
    def __init__(self, mode: str):
        self.mode, self.name = mode, f"fixed-{mode}"

    def decide(self, job, ctx):
        return Decision(job.id, ctx.t, self.mode, "collect")


DEFAULT_MATRIX = {
    "hash": [400, 800, 1600, 3200],      # MiB hashed
    "compress": [100, 200, 400, 800],    # MiB compressed
    "matmul": [20, 40, 80, 160],         # GFLOP
}


class BackgroundLoad:
    """Optional competing load (1..n threads of hashing) to vary contention."""

    def __init__(self, threads: int):
        self.threads, self.proc = threads, None

    def __enter__(self):
        if self.threads > 0:
            self.proc = subprocess.Popen([sys.executable, "-c",
                                          "import hashlib,threading\n"
                                          "b=bytes(1<<20)\n"
                                          "def w():\n  h=hashlib.sha256()\n  while True: h.update(b)\n"
                                          f"[threading.Thread(target=w,daemon=True).start() for _ in range({self.threads})]\n"
                                          "import time\nwhile True: time.sleep(1)"], cwd=str(PACKAGE_ROOT))
            time.sleep(0.5)
        return self

    def __exit__(self, *a):
        if self.proc:
            self.proc.kill()
            self.proc.wait()


def collect(cfg: Config, backend: Backend, out_csv: str | Path, reps: int = 2,
            matrix: dict | None = None, thread_opts: list[int] | None = None,
            bg_opts: list[int] | None = None, scale: float = 1.0, seed: int = 0,
            measure_idle_s: float = 0.0, verbose: bool = True, limit: int | None = None,
            idle_w: float | None = None) -> Path:
    caps = backend.capabilities()
    modes = [m.value for m in caps.supported_modes() if m.value in cfg.scheduler.allow_modes]
    matrix = matrix or DEFAULT_MATRIX
    n = caps.logical_cpus
    thread_opts = thread_opts or sorted({1, max(1, n // 2), n})
    bg_opts = bg_opts if bg_opts is not None else [0, max(1, n // 4)]
    rng = random.Random(seed)
    plan = [(k, u * scale, t, m, b) for k, us in matrix.items() for u in us for t in thread_opts
            for m in modes for b in bg_opts for _ in range(reps)]
    rng.shuffle(plan)   # randomised order avoids thermal/temporal confounding by mode
    if limit:
        plan = plan[:limit]
    sampler = PowerSampler(backend, cfg.energy.sample_interval_s)
    sampler.start()
    if measure_idle_s > 0:
        if verbose:
            print(f"measuring idle baseline for {measure_idle_s:.0f}s - leave the machine alone")
        sampler.measure_idle(measure_idle_s)
    elif idle_w is not None:
        sampler.idle_w = idle_w      # from `greenthread idle`
    pm = PowerModel(cfg.energy.idle_w, cfg.energy.per_core_w_normal, cfg.energy.per_core_w_eco)
    ledger = BatteryCarbonLedger(None, cfg.energy.charge_efficiency, cfg.carbon.static_value)
    src = StaticSource(cfg.carbon.static_value)
    sig = CarbonSignal(src, cfg.carbon.static_value)
    out = Path(out_csv)
    out.parent.mkdir(parents=True, exist_ok=True)
    new = not out.exists()
    machine = f"{caps.cpu_model} | {caps.os_name} {caps.os_build}".strip()
    with open(out, "a", newline="") as fh:
        wr = csv.DictWriter(fh, FIELDS)
        if new:
            wr.writeheader()
        for i, (kind, units, threads, mode, bg) in enumerate(plan):
            with BackgroundLoad(bg):
                bg_load = psutil.cpu_percent(interval=0.5) / 100.0
                eng = Engine(cfg, backend, FixedModePolicy(mode), sig, src, ledger, pm, sampler, verbose=False)
                eng.cfg.scheduler.escalate = False
                job = Job(f"c{i}", kind, units, threads, 0.0, 1e9)
                res = eng.run([job])[0]
            ps = sampler.latest()
            wr.writerow({"sample_id": i, "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"), "kind": kind,
                         "units": units, "threads": threads, "mode": mode, "runtime_s": round(res.runtime_s, 4),
                         "cpu_s": round(res.cpu_s, 4), "bg_load": round(bg_load, 3), "bg_threads": bg,
                         "on_ac": int(res.on_ac), "logical_cpus": n, "physical_cores": caps.physical_cores,
                         "energy_wh_measured": "" if res.energy_source != "measured-battery" else round(res.energy_wh, 6),
                         "energy_wh_model": round(res.energy_wh_model, 6),
                         "battery_pct": "" if not ps or ps.percent is None else ps.percent,
                         "control_failures": ";".join(res.control_failures), "machine": machine})
            fh.flush()
            if verbose:
                print(f"[{i+1}/{len(plan)}] {kind:<8} u={units:<6g} T={threads} {mode:<8} bg={bg} "
                      f"-> {res.runtime_s:.2f}s cpu={res.cpu_s:.2f}s")
    sampler.stop()
    return out
