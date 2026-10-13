"""Backend interface: the only layer that touches OS scheduling controls."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum


class Mode(str, Enum):
    NORMAL = "NORMAL"      # untouched: OS default scheduling (the baseline)
    ECO = "ECO"            # EcoQoS (power throttling) + below-normal priority
    HALF = "HALF"          # restricted to half of the physical cores (CPU Sets / affinity)
    ECO_HALF = "ECO_HALF"  # both of the above
    ECORES = "ECORES"      # efficiency-class cores only (hybrid CPUs; detected)
    BOOST = "BOOST"        # escalation: explicit HighQoS + normal priority, all cores

    @property
    def eco(self) -> bool:
        return self in (Mode.ECO, Mode.ECO_HALF)

    @property
    def restricted(self) -> bool:
        return self in (Mode.HALF, Mode.ECO_HALF, Mode.ECORES)


@dataclass
class CpuSetInfo:
    id: int
    group: int
    logical_index: int
    core_index: int
    efficiency_class: int


@dataclass
class Capabilities:
    os_name: str
    os_build: str = ""
    logical_cpus: int = 1
    physical_cores: int = 1
    cpu_model: str = ""
    cpu_sets: list[CpuSetInfo] = field(default_factory=list)
    heterogeneous: bool = False
    ecoqos: bool = False
    priority: bool = False
    affinity: bool = False
    cpu_rate_cap: bool = False
    battery_present: bool = False
    battery_energy_counter: bool = False   # remaining capacity in mWh readable
    power_meter: str = "model"            # battery-delta | rapl | model
    notes: list[str] = field(default_factory=list)

    def supported_modes(self) -> list[Mode]:
        modes = [Mode.NORMAL]
        if self.ecoqos or self.priority:
            modes.append(Mode.ECO)
        if self.affinity and self.physical_cores >= 2:
            modes.append(Mode.HALF)
            if self.ecoqos or self.priority:
                modes.append(Mode.ECO_HALF)
        if self.heterogeneous and self.affinity:
            modes.append(Mode.ECORES)
        return modes


@dataclass
class PowerStatus:
    on_ac: bool
    percent: float | None          # 0..100, None if no battery
    remaining_mwh: float | None = None
    full_mwh: float | None = None
    discharge_mw: float | None = None
    source: str = "unknown"


@dataclass
class AppliedControls:
    mode: Mode
    actions: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures


class Backend:
    name = "base"

    def capabilities(self) -> Capabilities:  # pragma: no cover - interface
        raise NotImplementedError

    def power_status(self) -> PowerStatus:  # pragma: no cover - interface
        raise NotImplementedError

    def apply(self, pid: int, mode: Mode, caps: Capabilities) -> AppliedControls:  # pragma: no cover
        raise NotImplementedError

    def cleanup(self, pid: int) -> None:
        """Release per-process resources (e.g. job handles)."""

    def energy_meter(self):
        """A meter with read_j() -> cumulative joules (e.g. RAPL), or None."""
        return None

    def probe(self, caps: "Capabilities") -> dict:
        """Launch a throwaway sleeper and check every supported control really applies."""
        import subprocess
        import sys
        import time
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"])
        try:
            time.sleep(0.2)
            out = {}
            for m in caps.supported_modes() + [Mode.BOOST]:
                r = self.apply(proc.pid, m, caps)
                out[m.value] = {"ok": r.ok, "actions": r.actions, "failures": r.failures}
            if hasattr(self, "cap_cpu_rate"):
                out["cpu_rate_cap"] = self.cap_cpu_rate(proc.pid, 50)
            return out
        finally:
            proc.kill()
            proc.wait()
            self.cleanup(proc.pid)

    # helpers shared by backends -------------------------------------------------
    @staticmethod
    def half_core_logicals(caps: Capabilities) -> list[int]:
        """One logical CPU per physical core, for half of the physical cores."""
        if caps.cpu_sets:
            by_core: dict[int, CpuSetInfo] = {}
            for s in caps.cpu_sets:
                by_core.setdefault(s.core_index, s)
            cores = sorted(by_core.values(), key=lambda s: s.core_index)
            n = max(1, len(cores) // 2)
            return [c.logical_index for c in cores[:n]]
        n = max(1, caps.physical_cores // 2)
        step = max(1, caps.logical_cpus // max(1, caps.physical_cores))
        return [i * step for i in range(n)]

    @staticmethod
    def efficient_logicals(caps: Capabilities) -> list[int]:
        if not caps.cpu_sets:
            return []
        lowest = min(s.efficiency_class for s in caps.cpu_sets)
        return [s.logical_index for s in caps.cpu_sets if s.efficiency_class == lowest]


def physical_core_count() -> int:
    try:
        import psutil
        return psutil.cpu_count(logical=False) or os.cpu_count() or 1
    except Exception:
        return os.cpu_count() or 1
