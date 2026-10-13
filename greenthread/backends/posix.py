"""Linux backend (first-class): user-mode controls only, no root, no kernel module.

Controls (each detected at runtime; failures are reported, never fatal):
  ECO     = SCHED_BATCH + nice 10 + util-clamp max (sched_setattr, SCHED_FLAG_UTIL_CLAMP_MAX)
            Util clamping lowers the frequency the kernel requests *only* with the
            schedutil cpufreq governor and a kernel built with CONFIG_UCLAMP_TASK;
            on intel_pstate (active mode) it is accepted but has no frequency effect.
            `capabilities().notes` says which case applies.
  HALF    = sched_setaffinity to one logical CPU on each of half the physical cores
            (topology from /sys/devices/system/cpu/cpuN/topology).
  ECORES  = sched_setaffinity to efficiency cores (Intel hybrid: /sys/devices/cpu_atom/cpus;
            ARM big.LITTLE: lowest cpu_capacity).
  BOOST   = SCHED_OTHER, clamp removed, all CPUs (nice cannot be lowered without privileges).
Telemetry:
  battery = /sys/class/power_supply/BAT*: energy_now/power_now (or charge_now x voltage_now)
  RAPL    = /sys/class/powercap/intel-rapl:*/energy_uj when readable (package energy,
            also on AC). Usually root-only; `scripts/setup_linux.sh --rapl` can grant read access.
"""
from __future__ import annotations

import ctypes
import os
import platform
from pathlib import Path

import psutil

from .base import (AppliedControls, Backend, Capabilities, CpuSetInfo, Mode, PowerStatus,
                   physical_core_count)

SYSFS = Path("/sys")
SCHED_OTHER, SCHED_BATCH = 0, 3
SCHED_FLAG_UTIL_CLAMP_MIN, SCHED_FLAG_UTIL_CLAMP_MAX = 0x20, 0x40
UCLAMP_ECO_MAX = 512           # of 1024: ask for at most ~half of max performance
_SYSCALLS = {"x86_64": (314, 315), "aarch64": (274, 275), "arm64": (274, 275)}


class SchedAttr(ctypes.Structure):
    _fields_ = [("size", ctypes.c_uint32), ("sched_policy", ctypes.c_uint32), ("sched_flags", ctypes.c_uint64),
                ("sched_nice", ctypes.c_int32), ("sched_priority", ctypes.c_uint32),
                ("sched_runtime", ctypes.c_uint64), ("sched_deadline", ctypes.c_uint64),
                ("sched_period", ctypes.c_uint64), ("sched_util_min", ctypes.c_uint32),
                ("sched_util_max", ctypes.c_uint32)]


def _libc():
    try:
        return ctypes.CDLL(None, use_errno=True)
    except OSError:
        return None


def sched_setattr(pid: int, policy: int, nice: int, util_max: int | None) -> tuple[bool, str]:
    """Set policy/nice (and optionally the util clamp) of every thread of pid."""
    nr = _SYSCALLS.get(platform.machine())
    libc = _libc()
    if not nr or libc is None:
        return False, "sched_setattr unavailable on this architecture"
    a = SchedAttr()
    a.size = ctypes.sizeof(SchedAttr)
    a.sched_policy, a.sched_nice = policy, nice
    if util_max is not None:
        a.sched_flags = SCHED_FLAG_UTIL_CLAMP_MIN | SCHED_FLAG_UTIL_CLAMP_MAX
        a.sched_util_min, a.sched_util_max = 0, util_max
    tids = [pid]
    try:
        tids = [int(t) for t in os.listdir(f"/proc/{pid}/task")]
    except OSError:
        pass
    err = ""
    for tid in tids:
        if libc.syscall(nr[0], tid, ctypes.byref(a), 0) != 0:
            err = os.strerror(ctypes.get_errno())
    return (err == ""), err


def _read(p: Path) -> str | None:
    try:
        return p.read_text().strip()
    except OSError:
        return None


def _cpulist(s: str | None) -> list[int]:
    out = []
    for part in (s or "").split(","):
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-")
            out += list(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return out


def read_topology(root: Path = SYSFS) -> list[CpuSetInfo]:
    """Logical CPU -> (core, efficiency class) from sysfs."""
    cpus = sorted(int(p.name[3:]) for p in (root / "devices/system/cpu").glob("cpu[0-9]*"))
    atom = set(_cpulist(_read(root / "devices/cpu_atom/cpus")))
    caps = {c: _read(root / f"devices/system/cpu/cpu{c}/cpu_capacity") for c in cpus}
    cap_vals = sorted({int(v) for v in caps.values() if v})
    out, core_ids = [], {}
    for c in cpus:
        if _read(root / f"devices/system/cpu/cpu{c}/online") == "0":
            continue
        pkg = _read(root / f"devices/system/cpu/cpu{c}/topology/physical_package_id") or "0"
        cid = _read(root / f"devices/system/cpu/cpu{c}/topology/core_id") or str(c)
        key = (pkg, cid)
        core_ids.setdefault(key, len(core_ids))
        if atom:
            eff = 0 if c in atom else 1
        elif len(cap_vals) > 1 and caps[c]:
            eff = cap_vals.index(int(caps[c]))
        else:
            eff = 0
        out.append(CpuSetInfo(id=c, group=0, logical_index=c, core_index=core_ids[key], efficiency_class=eff))
    return out


def read_battery(root: Path = SYSFS) -> PowerStatus:
    """Battery + AC state from /sys/class/power_supply (no privileges needed)."""
    ps = root / "class/power_supply"
    bats = sorted(p for p in ps.glob("*") if _read(p / "type") == "Battery") if ps.exists() else []
    mains = [p for p in ps.glob("*") if _read(p / "type") in ("Mains", "USB", "USB_C")] if ps.exists() else []
    on_ac = any(_read(p / "online") == "1" for p in mains)
    if not bats:
        return PowerStatus(on_ac=True if not mains else on_ac, percent=None, source="no-battery")
    rem = full = pw = 0.0
    pct, ok, status = [], False, set()
    for b in bats:
        st = _read(b / "status") or ""
        status.add(st)
        en, ef, pn = (_read(b / f) for f in ("energy_now", "energy_full", "power_now"))
        if en is None:   # charge-based batteries: uAh x uV
            cn, cf, v, cur = (_read(b / f) for f in ("charge_now", "charge_full", "voltage_now", "current_now"))
            if cn and v:
                en = str(int(cn) * int(v) / 1e6)
                ef = str(int(cf) * int(v) / 1e6) if cf else None
                pn = str(abs(int(cur)) * int(v) / 1e6) if cur else None
        if en:
            ok = True
            rem += float(en) / 1000.0            # uWh -> mWh
            full += float(ef) / 1000.0 if ef else 0.0
            if pn and st == "Discharging":
                pw += float(pn) / 1000.0         # uW -> mW
        cap = _read(b / "capacity")
        if cap:
            pct.append(float(cap))
    if not mains:   # no mains supply node: infer from battery status
        on_ac = not ("Discharging" in status)
    percent = (100.0 * rem / full) if ok and full else (sum(pct) / len(pct) if pct else None)
    return PowerStatus(on_ac=on_ac, percent=percent, remaining_mwh=rem if ok else None,
                       full_mwh=full or None, discharge_mw=(pw if not on_ac else 0.0) if ok else None,
                       source="sysfs power_supply")


def _rapl_paths(root: Path = SYSFS) -> list[Path]:
    out = []
    for p in sorted((root / "class/powercap").glob("intel-rapl:[0-9]*")):
        if ":" in p.name[len("intel-rapl:"):]:
            continue                      # sub-zones (core/uncore) are inside the package
        e = p / "energy_uj"
        try:
            if e.exists() and os.access(e, os.R_OK):
                int(e.read_text())
                out.append(e)
        except (OSError, ValueError):
            pass
    return out


class RaplMeter:
    """Package energy counter (sum over packages), handling counter wrap-around."""

    def __init__(self, root: Path = SYSFS):
        self.paths = _rapl_paths(root)
        self.max = []
        for p in self.paths:
            m = _read(p.parent / "max_energy_range_uj")
            self.max.append(int(m) if m else 2 ** 32)
        self._last = None
        self._acc = 0.0

    @property
    def available(self) -> bool:
        return bool(self.paths)

    def read_j(self) -> float | None:
        if not self.paths:
            return None
        cur = [int(p.read_text()) for p in self.paths]
        if self._last is not None:
            for i, (a, b) in enumerate(zip(self._last, cur)):
                self._acc += ((b - a) % self.max[i]) / 1e6
        self._last = cur
        return self._acc


class PosixBackend(Backend):
    name = "posix"

    def __init__(self, root: Path = SYSFS):
        self.root = root
        self._caps: Capabilities | None = None
        self._uclamp: bool | None = None

    def _probe_uclamp(self) -> bool:
        import subprocess
        import sys
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(2)"])
        try:
            ok, _ = sched_setattr(p.pid, SCHED_BATCH, 10, UCLAMP_ECO_MAX)
            return ok
        finally:
            p.kill()
            p.wait()

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
        topo = read_topology(self.root) if sys_is_linux() else []
        caps = Capabilities(os_name=f"{platform.system()} {platform.release()}",
                            logical_cpus=os.cpu_count() or 1,
                            physical_cores=len({s.core_index for s in topo}) if topo else physical_core_count(),
                            cpu_model=model, cpu_sets=topo)
        caps.heterogeneous = len({s.efficiency_class for s in topo}) > 1
        caps.priority = True
        caps.affinity = hasattr(os, "sched_setaffinity")
        self._uclamp = self._probe_uclamp() if sys_is_linux() else False
        gov = _read(self.root / "devices/system/cpu/cpu0/cpufreq/scaling_governor")
        drv = _read(self.root / "devices/system/cpu/cpu0/cpufreq/scaling_driver")
        caps.ecoqos = bool(self._uclamp and gov == "schedutil")   # real frequency effect
        if self._uclamp and gov != "schedutil":
            caps.notes.append(f"util-clamp accepted but governor={gov} driver={drv}: no frequency effect "
                              "(ECO = SCHED_BATCH + nice only). For EcoQoS-like behaviour use schedutil.")
        elif not self._uclamp:
            caps.notes.append("util-clamp unsupported/denied by kernel: ECO = SCHED_BATCH + nice 10 only")
        else:
            caps.notes.append("ECO = SCHED_BATCH + nice 10 + util-clamp max (schedutil): lowers CPU frequency")
        ps = read_battery(self.root)
        caps.battery_present = ps.percent is not None
        caps.battery_energy_counter = ps.remaining_mwh is not None
        rapl = RaplMeter(self.root)
        caps.power_meter = "rapl" if rapl.available else ("battery-delta" if caps.battery_energy_counter else "model")
        if not rapl.available and (self.root / "class/powercap/intel-rapl:0").exists():
            caps.notes.append("RAPL present but not readable (root-only); run scripts/setup_linux.sh --rapl")
        self._caps = caps
        return caps

    def power_status(self) -> PowerStatus:
        return read_battery(self.root)

    def energy_meter(self):
        m = RaplMeter(self.root)
        return m if m.available else None

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
                ok, err = sched_setattr(pid, SCHED_OTHER, p.nice(), 1024 if self._uclamp else None)
                (res.actions if ok else res.failures).append("SCHED_OTHER" + ("+uclamp=1024" if self._uclamp else "")
                                                            + ("" if ok else f" ({err})"))
                if caps.affinity:
                    p.cpu_affinity(list(range(caps.logical_cpus)))
                    res.actions.append("cpus=all")
                return res
            if mode.eco:
                ok, err = sched_setattr(pid, SCHED_BATCH, 10, UCLAMP_ECO_MAX if self._uclamp else None)
                if ok:
                    res.actions.append("SCHED_BATCH+nice=10" + (f"+uclamp_max={UCLAMP_ECO_MAX}" if self._uclamp else ""))
                else:
                    p.nice(10)
                    res.actions.append(f"nice=10 (sched_setattr: {err})")
            if mode in (Mode.HALF, Mode.ECO_HALF) and caps.affinity:
                cpus = self.half_core_logicals(caps)
                p.cpu_affinity(cpus)
                res.actions.append(f"affinity={cpus}")
            if mode == Mode.ECORES:
                cpus = self.efficient_logicals(caps)
                if cpus and caps.heterogeneous:
                    p.cpu_affinity(cpus)
                    res.actions.append(f"e-cores={cpus}")
                else:
                    res.failures.append("ECORES: no efficiency cores detected")
        except psutil.Error as e:
            res.failures.append(str(e))
        return res


def sys_is_linux() -> bool:
    return platform.system() == "Linux"
