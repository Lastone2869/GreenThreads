"""Native Windows 10/11 backend using documented Win32 APIs via ctypes.

APIs used (all user-mode, no driver, no kernel change):
  * GetSystemPowerStatus                      - AC/battery state, percent
  * root\\wmi BatteryStatus (via PowerShell CIM) - remaining capacity (mWh), discharge rate (mW)
  * GetSystemCpuSetInformation                - CPU Set ids, core index, EfficiencyClass
  * SetProcessDefaultCpuSets / SetProcessAffinityMask - core placement
  * SetProcessInformation(ProcessPowerThrottling, EXECUTION_SPEED) - EcoQoS / HighQoS
  * SetPriorityClass                          - BELOW_NORMAL for background jobs
  * CreateJobObject + JOBOBJECT_CPU_RATE_CONTROL_INFORMATION - optional CPU hard cap

GreenThread only applies controls to processes it launched itself.
"""
from __future__ import annotations

import ctypes
import json
import platform
import subprocess
import sys
import time
from ctypes import wintypes

from .base import (AppliedControls, Backend, Capabilities, CpuSetInfo, Mode,
                   PowerStatus, physical_core_count)

if sys.platform == "win32":  # pragma: no cover - exercised on Windows only
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    # 64-bit safety: HANDLE return/arg types must not be truncated to c_int.
    H, B, U, P = wintypes.HANDLE, wintypes.BOOL, wintypes.ULONG, ctypes.c_void_p
    _sigs = {
        "OpenProcess": (H, [wintypes.DWORD, B, wintypes.DWORD]),
        "CloseHandle": (B, [H]),
        "GetSystemPowerStatus": (B, [P]),
        "GetSystemCpuSetInformation": (B, [P, U, ctypes.POINTER(U), H, U]),
        "SetProcessDefaultCpuSets": (B, [H, P, U]),
        "SetProcessAffinityMask": (B, [H, ctypes.c_size_t]),
        "GetProcessAffinityMask": (B, [H, ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(ctypes.c_size_t)]),
        "SetProcessInformation": (B, [H, ctypes.c_int, P, wintypes.DWORD]),
        "SetPriorityClass": (B, [H, wintypes.DWORD]),
        "CreateJobObjectW": (H, [P, wintypes.LPCWSTR]),
        "SetInformationJobObject": (B, [H, ctypes.c_int, P, wintypes.DWORD]),
        "AssignProcessToJobObject": (B, [H, H]),
    }
    for _n, (_r, _a) in _sigs.items():
        _f = getattr(kernel32, _n)
        _f.restype, _f.argtypes = _r, _a
else:  # allow import on other platforms for static checks/tests
    kernel32 = None

PROCESS_TERMINATE = 0x0001
PROCESS_SET_QUOTA = 0x0100
PROCESS_SET_INFORMATION = 0x0200
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_SET_LIMITED_INFORMATION = 0x2000
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

NORMAL_PRIORITY_CLASS = 0x00000020
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000

ProcessPowerThrottling = 4
PROCESS_POWER_THROTTLING_CURRENT_VERSION = 1
PROCESS_POWER_THROTTLING_EXECUTION_SPEED = 0x1

JobObjectCpuRateControlInformation = 15
JOB_OBJECT_CPU_RATE_CONTROL_ENABLE = 0x1
JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP = 0x4


class SYSTEM_POWER_STATUS(ctypes.Structure):
    _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                ("BatteryLifeTime", wintypes.DWORD), ("BatteryFullLifeTime", wintypes.DWORD)]


# ULONG/DWORD are 32-bit on Windows (LLP64); use fixed-width types so the layout
# is identical to winnt.h on every platform (wintypes.ULONG is 64-bit on Linux).
U32 = ctypes.c_uint32


class PROCESS_POWER_THROTTLING_STATE(ctypes.Structure):
    _fields_ = [("Version", U32), ("ControlMask", U32), ("StateMask", U32)]   # 12 bytes


class JOBOBJECT_CPU_RATE_CONTROL_INFORMATION(ctypes.Structure):
    _fields_ = [("ControlFlags", U32), ("CpuRate", U32)]                      # 8 bytes


def parse_cpu_set_buffer(raw: bytes) -> list[CpuSetInfo]:
    """Parse a SYSTEM_CPU_SET_INFORMATION array (layout per winnt.h).

    Offsets: Size u32 @0, Type u32 @4, Id u32 @8, Group u16 @12,
    LogicalProcessorIndex u8 @14, CoreIndex u8 @15, LastLevelCacheIndex u8 @16,
    NumaNodeIndex u8 @17, EfficiencyClass u8 @18.
    """
    out, off = [], 0
    while off + 19 <= len(raw):
        size = int.from_bytes(raw[off:off + 4], "little")
        if size == 0:
            break
        typ = int.from_bytes(raw[off + 4:off + 8], "little")
        if typ == 0:  # CpuSetInformation
            out.append(CpuSetInfo(
                id=int.from_bytes(raw[off + 8:off + 12], "little"),
                group=int.from_bytes(raw[off + 12:off + 14], "little"),
                logical_index=raw[off + 14], core_index=raw[off + 15],
                efficiency_class=raw[off + 18]))
        off += size
    return out


def _ps_json(command: str, timeout: float = 15.0):
    """Run a PowerShell CIM query and parse JSON output; None on failure."""
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command + " | ConvertTo-Json -Compress"],
            capture_output=True, text=True, timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if proc.returncode != 0 or not proc.stdout.strip():
            return None
        return json.loads(proc.stdout)
    except Exception:
        return None


class BatteryPoller:  # pragma: no cover - requires Windows
    """One long-lived PowerShell process that prints BatteryStatus once per second.

    Spawning PowerShell per reading costs ~0.2-0.5 s of CPU and would itself
    distort the power being measured; a single persistent poller does not.
    Note: firmware may refresh DischargeRate only every few seconds, so energy
    labels are only trustworthy for jobs of roughly >= 15 s.
    """
    SCRIPT = ("while($true){ $b = Get-CimInstance -Namespace root/wmi -ClassName BatteryStatus | "
              "Where-Object {$_.Voltage -gt 0} | Select-Object -First 1 RemainingCapacity,DischargeRate,"
              "ChargeRate,PowerOnline; if($b){ $b | ConvertTo-Json -Compress } else { '{}' }; "
              "[Console]::Out.Flush(); Start-Sleep -Milliseconds 1000 }")

    def __init__(self):
        import threading
        self.latest: dict | None = None
        self.t_latest = 0.0
        self.proc = subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-Command", self.SCRIPT],
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.proc.stdout:
            try:
                d = json.loads(line)
                if d.get("RemainingCapacity"):
                    self.latest, self.t_latest = d, time.monotonic()
            except Exception:
                continue

    def get(self, max_age_s: float = 5.0) -> dict | None:
        if self.latest and time.monotonic() - self.t_latest <= max_age_s:
            return self.latest
        return None

    def close(self):
        try:
            self.proc.kill()
        except Exception:
            pass


class WindowsBackend(Backend):  # pragma: no cover - requires Windows
    name = "windows"

    def __init__(self):
        if kernel32 is None:
            raise RuntimeError("WindowsBackend requires Windows")
        self._jobs: dict[int, int] = {}
        self._caps: Capabilities | None = None
        self._wmi_ok: bool | None = None
        self._poller: BatteryPoller | None = None

    def _battery_reading(self):
        """Fast path via the persistent poller; one-shot query as fallback."""
        if self._wmi_ok is False:
            return None
        if self._poller is None:
            try:
                self._poller = BatteryPoller()
                import atexit
                atexit.register(self._poller.close)
                for _ in range(40):          # first reading takes ~1-3 s
                    if self._poller.get():
                        break
                    time.sleep(0.1)
            except Exception:
                self._poller = None
        d = self._poller.get() if self._poller else None
        return d or self._wmi_battery()

    # --- discovery -----------------------------------------------------------
    def _cpu_sets(self) -> list[CpuSetInfo]:
        ret = wintypes.ULONG(0)
        kernel32.GetSystemCpuSetInformation(None, 0, ctypes.byref(ret), None, 0)
        if ret.value == 0:
            return []
        buf = ctypes.create_string_buffer(ret.value)
        if not kernel32.GetSystemCpuSetInformation(ctypes.cast(buf, ctypes.c_void_p), ret.value, ctypes.byref(ret), None, 0):
            return []
        return parse_cpu_set_buffer(buf.raw[:ret.value])

    def _cpu_model(self) -> str:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
                return winreg.QueryValueEx(k, "ProcessorNameString")[0].strip()
        except Exception:
            return platform.processor()

    def _wmi_battery(self):
        data = _ps_json("Get-CimInstance -Namespace root/wmi -ClassName BatteryStatus | "
                        "Select-Object RemainingCapacity,DischargeRate,ChargeRate,PowerOnline,Voltage")
        if data is None:
            return None
        if isinstance(data, list):
            data = [d for d in data if d.get("Voltage")] or data
            data = data[0]
        return data

    def _wmi_full_capacity(self):
        data = _ps_json("Get-CimInstance -Namespace root/wmi -ClassName BatteryFullChargedCapacity | "
                        "Select-Object FullChargedCapacity")
        if isinstance(data, list):
            data = data[0] if data else None
        return (data or {}).get("FullChargedCapacity")

    def capabilities(self) -> Capabilities:
        if self._caps:
            return self._caps
        build = sys.getwindowsversion().build
        caps = Capabilities(os_name="Windows 11" if build >= 22000 else "Windows 10",
                            os_build=str(build), logical_cpus=__import__("os").cpu_count() or 1,
                            physical_cores=physical_core_count(), cpu_model=self._cpu_model())
        caps.cpu_sets = self._cpu_sets()
        if caps.cpu_sets:
            caps.physical_cores = len({s.core_index for s in caps.cpu_sets})
            caps.heterogeneous = len({s.efficiency_class for s in caps.cpu_sets}) > 1
        caps.priority = caps.affinity = True
        caps.ecoqos = build >= 22000  # EcoQoS level exists from Windows 11
        if not caps.ecoqos:
            caps.notes.append("EcoQoS unavailable before Windows 11; ECO uses priority + LowQoS only")
        caps.cpu_rate_cap = True
        ps = self.power_status()
        caps.battery_present = ps.percent is not None
        wb = self._wmi_battery() if caps.battery_present else None
        self._wmi_ok = bool(wb and wb.get("RemainingCapacity"))
        caps.battery_energy_counter = self._wmi_ok
        caps.power_meter = "battery-delta" if self._wmi_ok else "model"
        if not caps.heterogeneous:
            caps.notes.append("Homogeneous cores: ECORES mode disabled; HALF/ECO used instead")
        if caps.battery_present and not self._wmi_ok:
            caps.notes.append("BatteryStatus WMI class unavailable: energy uses utilisation model")
        self._caps = caps
        return caps

    def power_status(self) -> PowerStatus:
        s = SYSTEM_POWER_STATUS()
        if not kernel32.GetSystemPowerStatus(ctypes.byref(s)):
            return PowerStatus(on_ac=True, percent=None, source="unavailable")
        no_battery = s.BatteryFlag == 128 or s.BatteryLifePercent == 255
        status = PowerStatus(on_ac=(s.ACLineStatus != 0),
                             percent=None if no_battery else float(s.BatteryLifePercent),
                             source="GetSystemPowerStatus")
        if not no_battery and self._wmi_ok is not False:
            wb = self._battery_reading()
            if wb and wb.get("RemainingCapacity"):
                status.remaining_mwh = float(wb["RemainingCapacity"])
                status.discharge_mw = float(wb.get("DischargeRate") or 0)
                status.source += "+WMI BatteryStatus"
                if self._wmi_ok is None:
                    self._wmi_ok = True
        return status

    def full_capacity_mwh(self) -> float | None:
        v = self._wmi_full_capacity()
        return float(v) if v else None

    # --- control -------------------------------------------------------------
    def _open(self, pid: int):
        h = kernel32.OpenProcess(PROCESS_SET_INFORMATION | PROCESS_QUERY_INFORMATION |
                                 PROCESS_SET_LIMITED_INFORMATION | PROCESS_SET_QUOTA |
                                 PROCESS_TERMINATE, False, pid)
        if not h:
            raise OSError(ctypes.get_last_error(), f"OpenProcess({pid}) failed")
        return h

    def _set_qos(self, h, eco: bool | None) -> bool:
        st = PROCESS_POWER_THROTTLING_STATE(PROCESS_POWER_THROTTLING_CURRENT_VERSION, 0, 0)
        if eco is True:
            st.ControlMask = st.StateMask = PROCESS_POWER_THROTTLING_EXECUTION_SPEED
        elif eco is False:  # explicit HighQoS
            st.ControlMask = PROCESS_POWER_THROTTLING_EXECUTION_SPEED
        return bool(kernel32.SetProcessInformation(h, ProcessPowerThrottling,
                                                   ctypes.byref(st), ctypes.sizeof(st)))

    def _set_cpus(self, h, logicals: list[int], caps: Capabilities) -> str:
        ids = [s.id for s in caps.cpu_sets if s.logical_index in logicals and s.group == 0]
        if ids:
            arr = (U32 * len(ids))(*ids)
            if kernel32.SetProcessDefaultCpuSets(h, ctypes.cast(arr, ctypes.c_void_p), len(ids)):
                return f"cpusets={ids}"
        mask = 0
        for i in logicals:
            mask |= 1 << i
        if kernel32.SetProcessAffinityMask(h, ctypes.c_size_t(mask)):
            return f"affinity=0x{mask:x}"
        raise OSError(ctypes.get_last_error(), "CPU set and affinity both failed")

    def _clear_cpus(self, h) -> None:
        kernel32.SetProcessDefaultCpuSets(h, None, 0)
        sysmask, procmask = ctypes.c_size_t(), ctypes.c_size_t()
        if kernel32.GetProcessAffinityMask(h, ctypes.byref(procmask), ctypes.byref(sysmask)):
            kernel32.SetProcessAffinityMask(h, sysmask)

    def cap_cpu_rate(self, pid: int, percent: float) -> bool:
        h = self._open(pid)
        try:
            job = kernel32.CreateJobObjectW(None, None)
            info = JOBOBJECT_CPU_RATE_CONTROL_INFORMATION(
                JOB_OBJECT_CPU_RATE_CONTROL_ENABLE | JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP,
                max(1, min(10000, int(percent * 100))))
            ok = (kernel32.SetInformationJobObject(job, JobObjectCpuRateControlInformation,
                                                   ctypes.byref(info), ctypes.sizeof(info))
                  and kernel32.AssignProcessToJobObject(job, h))
            if ok:
                self._jobs[pid] = job
            else:
                kernel32.CloseHandle(job)
            return bool(ok)
        finally:
            kernel32.CloseHandle(h)

    def apply(self, pid: int, mode: Mode, caps: Capabilities) -> AppliedControls:
        res = AppliedControls(mode=Mode(mode))
        if mode == Mode.NORMAL:
            res.actions.append("none (OS default)")
            return res
        try:
            h = self._open(pid)
        except OSError as e:
            res.failures.append(str(e))
            return res
        try:
            if mode == Mode.BOOST:
                self._clear_cpus(h)
                res.actions.append("cpus=all")
                (res.actions if self._set_qos(h, False) else res.failures).append("HighQoS")
                (res.actions if kernel32.SetPriorityClass(h, NORMAL_PRIORITY_CLASS) else res.failures).append("priority=NORMAL")
                return res
            if mode.eco:
                # Same API on both: classified EcoQoS on Win11, LowQoS on Win10.
                label = "EcoQoS" if caps.ecoqos else "LowQoS"
                (res.actions if self._set_qos(h, True) else res.failures).append(label)
                (res.actions if kernel32.SetPriorityClass(h, BELOW_NORMAL_PRIORITY_CLASS) else res.failures).append("priority=BELOW_NORMAL")
            if mode in (Mode.HALF, Mode.ECO_HALF):
                try:
                    res.actions.append(self._set_cpus(h, self.half_core_logicals(caps), caps))
                except OSError as e:
                    res.failures.append(f"half-cores: {e}")
            if mode == Mode.ECORES:
                lp = self.efficient_logicals(caps)
                try:
                    res.actions.append(self._set_cpus(h, lp, caps))
                except OSError as e:
                    res.failures.append(f"e-cores: {e}")
        finally:
            kernel32.CloseHandle(h)
        return res

    def cleanup(self, pid: int) -> None:
        job = self._jobs.pop(pid, None)
        if job:
            kernel32.CloseHandle(job)


def probe_controls(backend: WindowsBackend, caps: Capabilities) -> dict:  # pragma: no cover
    """Launch a throwaway sleeper and verify every control actually succeeds."""
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"])
    try:
        time.sleep(0.2)
        out = {}
        for m in caps.supported_modes() + [Mode.BOOST]:
            r = backend.apply(proc.pid, m, caps)
            out[m.value] = {"ok": r.ok, "actions": r.actions, "failures": r.failures}
        out["cpu_rate_cap"] = backend.cap_cpu_rate(proc.pid, 50)
        return out
    finally:
        proc.kill()
        backend.cleanup(proc.pid)
