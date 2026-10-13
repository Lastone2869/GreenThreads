"""Telemetry: device power sampling, idle baseline, calibration of the power model.

Measured path (Windows laptop on battery): root\\wmi BatteryStatus.DischargeRate
(mW) sampled every few seconds and integrated; cross-checked against the
RemainingCapacity (mWh) delta. This is *whole-device* power, so per-job energy
is (device energy - idle baseline x duration) x the job's share of busy CPU time.
Model path (AC, no battery counter, or other OS): CPU-seconds x per-core watts.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import numpy as np
import psutil

from .accounting import PowerModel
from .backends.base import Backend, PowerStatus


@dataclass
class Sample:
    t: float
    on_ac: bool
    percent: float | None
    remaining_mwh: float | None
    discharge_mw: float | None
    cpu_util: float           # system-wide, 0..1


class PowerSampler:
    def __init__(self, backend: Backend, interval_s: float = 5.0, clock=time.monotonic):
        self.backend, self.interval, self.clock = backend, interval_s, clock
        self.samples: list[Sample] = []
        self._stop = threading.Event()
        self._th: threading.Thread | None = None
        self.idle_w: float | None = None

    def sample_once(self) -> Sample:
        ps: PowerStatus = self.backend.power_status()
        s = Sample(self.clock(), ps.on_ac, ps.percent, ps.remaining_mwh, ps.discharge_mw,
                   psutil.cpu_percent(interval=None) / 100.0)
        self.samples.append(s)
        return s

    def start(self):
        psutil.cpu_percent(interval=None)
        self.sample_once()

        def loop():
            while not self._stop.wait(self.interval):
                try:
                    self.sample_once()
                except Exception:
                    pass
        self._th = threading.Thread(target=loop, daemon=True)
        self._th.start()

    def stop(self):
        self._stop.set()
        if self._th:
            self._th.join(timeout=self.interval + 20)

    def latest(self) -> Sample | None:
        return self.samples[-1] if self.samples else None

    @property
    def measuring(self) -> bool:
        s = self.latest()
        return bool(s and not s.on_ac and s.discharge_mw)

    def device_energy_wh(self, t0: float, t1: float) -> float | None:
        """Integrate DischargeRate over [t0, t1] (only valid on battery)."""
        pts = [(s.t, s.discharge_mw) for s in self.samples if s.discharge_mw and not s.on_ac]
        if len(pts) < 2:
            return None
        ts = np.array([p[0] for p in pts]); mw = np.array([p[1] for p in pts], dtype=float)
        if t1 <= ts[0] or t0 >= ts[-1]:
            return None
        grid = np.linspace(max(t0, ts[0]), min(t1, ts[-1]), 50)
        p = np.interp(grid, ts, mw)
        covered = grid[-1] - grid[0]
        if covered < 0.5 * (t1 - t0):
            return None
        mean_mw = float(np.trapezoid(p, grid) / max(covered, 1e-9))
        return mean_mw / 1000.0 * (t1 - t0) / 3600.0

    def measure_idle(self, seconds: float = 60.0) -> float | None:
        """Average device power while idle (call with nothing else running)."""
        t0 = self.clock()
        time.sleep(seconds)
        pts = [s.discharge_mw for s in self.samples if s.t >= t0 and s.discharge_mw and not s.on_ac]
        if len(pts) >= 2:
            self.idle_w = float(np.median(pts)) / 1000.0
        return self.idle_w


def calibrate_power_model(samples: list[Sample], eco_flags: list[bool] | None = None,
                          logical_cpus: int | None = None) -> PowerModel | None:
    """Least-squares fit P = idle + k * busy_cores from battery samples.

    eco_flags (aligned with samples) lets the fit estimate a separate eco slope.
    Returns None if there is not enough on-battery data.
    """
    n = logical_cpus or psutil.cpu_count() or 1
    rows = [(s, (eco_flags[i] if eco_flags else False)) for i, s in enumerate(samples)
            if s.discharge_mw and not s.on_ac]
    if len(rows) < 8:
        return None
    busy = np.array([r[0].cpu_util * n for r in rows])
    eco = np.array([1.0 if r[1] else 0.0 for r in rows])
    p = np.array([r[0].discharge_mw / 1000.0 for r in rows])
    X = np.column_stack([np.ones_like(busy), busy * (1 - eco), busy * eco])
    coef, *_ = np.linalg.lstsq(X, p, rcond=None)
    idle, kn, ke = coef
    if eco.sum() < 3:
        ke = kn
    return PowerModel(idle_w=float(max(idle, 0.5)), per_core_w_normal=float(max(kn, 0.1)),
                      per_core_w_eco=float(max(ke, 0.1)))
