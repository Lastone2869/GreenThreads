"""Exercise the Windows backend's control logic against a fake kernel32, so the
call sequence and struct contents are verified even off-Windows."""
import ctypes

import pytest

import greenthread.backends.windows as w
from greenthread.backends.base import Capabilities, CpuSetInfo, Mode


class FakeK32:
    def __init__(self, fail=()):
        self.calls, self.fail = [], set(fail)

    def _rec(self, name, *args):
        self.calls.append((name, args))
        return 0 if name in self.fail else 1

    def OpenProcess(self, access, inherit, pid):
        self.calls.append(("OpenProcess", (access, pid)))
        return 0 if "OpenProcess" in self.fail else 4242

    def CloseHandle(self, h):
        return self._rec("CloseHandle", h)

    def SetProcessInformation(self, h, cls, ref, size):
        st = ref._obj
        return self._rec("SetProcessInformation", cls, st.Version, st.ControlMask, st.StateMask, size)

    def SetPriorityClass(self, h, cls):
        return self._rec("SetPriorityClass", cls)

    def SetProcessDefaultCpuSets(self, h, ptr, n):
        ids = [] if ptr is None else list(ctypes.cast(ptr, ctypes.POINTER(ctypes.c_uint32))[:n])
        return self._rec("SetProcessDefaultCpuSets", ids)

    def SetProcessAffinityMask(self, h, mask):
        return self._rec("SetProcessAffinityMask", mask.value if hasattr(mask, "value") else mask)

    def GetProcessAffinityMask(self, h, p, s):
        s._obj.value = 0xFF
        return 1

    def CreateJobObjectW(self, a, b):
        return 777

    def SetInformationJobObject(self, job, cls, ref, size):
        info = ref._obj
        return self._rec("SetInformationJobObject", cls, info.ControlFlags, info.CpuRate, size)

    def AssignProcessToJobObject(self, job, h):
        return self._rec("AssignProcessToJobObject", job, h)


@pytest.fixture
def backend(monkeypatch):
    fake = FakeK32()
    monkeypatch.setattr(w, "kernel32", fake)
    b = w.WindowsBackend()
    return b, fake


def caps(hetero=False):
    sets = [CpuSetInfo(256 + i, 0, i, i // 2, (1 if i < 4 else 0) if hetero else 0) for i in range(8)]
    return Capabilities("Windows 11", "22631", 8, 4, cpu_sets=sets, heterogeneous=hetero,
                        ecoqos=True, priority=True, affinity=True)


def test_normal_touches_nothing(backend):
    b, k = backend
    r = b.apply(1, Mode.NORMAL, caps())
    assert r.ok and k.calls == []


def test_eco_sets_ecoqos_and_below_normal(backend):
    b, k = backend
    r = b.apply(1, Mode.ECO, caps())
    assert r.ok and "EcoQoS" in r.actions
    spi = [c for c in k.calls if c[0] == "SetProcessInformation"][0][1]
    assert spi[0] == w.ProcessPowerThrottling and spi[2] == 1 and spi[3] == 1 and spi[4] == 12
    assert ("SetPriorityClass", (w.BELOW_NORMAL_PRIORITY_CLASS,)) in k.calls
    assert k.calls[-1][0] == "CloseHandle"


def test_eco_half_uses_cpu_sets_one_thread_per_core(backend):
    b, k = backend
    r = b.apply(1, Mode.ECO_HALF, caps())
    assert r.ok
    assert ("SetProcessDefaultCpuSets", ([256, 258],)) in k.calls


def test_ecores_selects_lowest_efficiency_class(backend):
    b, k = backend
    r = b.apply(1, Mode.ECORES, caps(hetero=True))
    ids = [c[1][0] for c in k.calls if c[0] == "SetProcessDefaultCpuSets"][0]
    assert ids == [260, 261, 262, 263] and r.ok


def test_boost_sets_highqos_and_clears_cpu_sets(backend):
    b, k = backend
    r = b.apply(1, Mode.BOOST, caps())
    spi = [c for c in k.calls if c[0] == "SetProcessInformation"][0][1]
    assert spi[2] == 1 and spi[3] == 0            # ControlMask set, StateMask clear = HighQoS
    assert ("SetProcessDefaultCpuSets", ([],)) in k.calls
    assert r.ok


def test_cpu_set_failure_falls_back_to_affinity(monkeypatch):
    fake = FakeK32(fail={"SetProcessDefaultCpuSets"})
    monkeypatch.setattr(w, "kernel32", fake)
    r = w.WindowsBackend().apply(1, Mode.HALF, caps())
    assert r.ok and any(a.startswith("affinity=0x5") for a in r.actions)


def test_open_failure_reported_not_raised(monkeypatch):
    monkeypatch.setattr(w, "kernel32", FakeK32(fail={"OpenProcess"}))
    monkeypatch.setattr(w.ctypes, "get_last_error", lambda: 5, raising=False)
    r = w.WindowsBackend().apply(1, Mode.ECO, caps())
    assert not r.ok and r.failures


def test_cpu_rate_cap_job_object(backend):
    b, k = backend
    assert b.cap_cpu_rate(1, 30)
    sij = [c for c in k.calls if c[0] == "SetInformationJobObject"][0][1]
    assert sij[0] == 15 and sij[1] == 0x5 and sij[2] == 3000 and sij[3] == 8
