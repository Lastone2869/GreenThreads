"""Linux/POSIX backend. Not the target platform: it exists so the whole pipeline
(engine, ML, experiments) can run and be tested off-Windows with *real* process
control (nice + sched_setaffinity). ECO here is nice(10), which changes priority
but not CPU frequency, so energy claims from this backend are model-based only.
"""
from __future__ import annotations

import os
import platform
from pathlib import Path

import psutil

from .base import AppliedControls, Backend, Capabilities, Mode, PowerStatus, physical_core_count


def _rapl_path() -> Path | None:
    p = Path("/sys/class/powercap/intel-rapl:0/energy_uj")
    try:
        if p.exists() and os.access(p, os.R_OK):
            p.read_text()
            return p
    except OSError:
        pass
    return None


class PosixBackend(Backend):
    name = "posix"

    def __init__(self):
        self._caps: Capabilities | None = None

    def capabilities(self) -> Capabilities:
        if self._caps:
            return self._caps
        model = platform.processor()
        try:
            for line in Path("/proc/cpuinfo").read_text().splitlines():
                if line.startswith("model name"):
                    model = line.split(":", 1)[1].strip()
                    break
        except OSError:
            pass
        caps = Capabilities(os_name=f"{platform.system()} {platform.release()}",
                            logical_cpus=os.cpu_count() or 1,
                            physical_cores=physical_core_count(), cpu_model=model)
        caps.priority = True
        caps.affinity = hasattr(os, "sched_setaffinity")
        caps.ecoqos = False
        b = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
        caps.battery_present = b is not None
        caps.power_meter = "rapl" if _rapl_path() else "model"
        caps.notes.append("Non-Windows backend: ECO = nice(10) only (no EcoQoS); for testing")
        self._caps = caps
        return caps

    def power_status(self) -> PowerStatus:
        b = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
        if b is None:
            return PowerStatus(on_ac=True, percent=None, source="no-battery")
        return PowerStatus(on_ac=bool(b.power_plugged), percent=float(b.percent), source="psutil")

    def apply(self, pid: int, mode: Mode, caps: Capabilities) -> AppliedControls:
        res = AppliedControls(mode=Mode(mode))
        if mode == Mode.NORMAL:
            res.actions.append("none (OS default)")
            return res
        try:
            p = psutil.Process(pid)
        except psutil.Error as e:
            res.failures.append(str(e))
            return res
        try:
            if mode == Mode.BOOST:
                try:
                    p.nice(0)
                    res.actions.append("nice=0")
                except psutil.AccessDenied:
                    res.failures.append("nice=0 (raising priority needs privileges)")
                if caps.affinity:
                    p.cpu_affinity(list(range(caps.logical_cpus)))
                    res.actions.append("cpus=all")
                return res
            if mode.eco:
                p.nice(10)
                res.actions.append("nice=10")
            if mode in (Mode.HALF, Mode.ECO_HALF) and caps.affinity:
                cpus = self.half_core_logicals(caps)
                p.cpu_affinity(cpus)
                res.actions.append(f"affinity={cpus}")
            if mode == Mode.ECORES:
                res.failures.append("ECORES unsupported on this backend")
        except psutil.Error as e:
            res.failures.append(str(e))
        return res


class RaplMeter:
    """Package energy counter (Linux, Intel RAPL) when readable."""

    def __init__(self):
        self.path = _rapl_path()

    def read_j(self) -> float | None:
        if not self.path:
            return None
        return int(self.path.read_text()) / 1e6
