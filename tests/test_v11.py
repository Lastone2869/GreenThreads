"""Tests for version 1.1 features: Linux backend, live API client, robust mode
selection, tight-deadline safety, battery ledger tools."""
import io
import json
import math
import os
import subprocess
import sys
import urllib.error
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from greenthread.backends.base import Mode
from greenthread.backends import posix
from greenthread.carbon import CarbonSignal, ElectricityMapsSource
from greenthread.config import Config
from greenthread.jobs import Job, Prediction
from greenthread.ledger_study import PROFILES, report, simulate_profile
from greenthread.carbon import CSVReplaySource
from greenthread.model import Ctx, Predictor
from greenthread.policy import PolicyContext, build_policy
from greenthread.simulator import SimScenario, simulate, summarise


# ---------------------------------------------------------------- fake sysfs
def _w(p: Path, v):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(str(v))


def fake_sysfs(tmp_path, hybrid=False, battery="energy", discharging=True):
    r = tmp_path / "sys"
    for c in range(8):
        base = r / f"devices/system/cpu/cpu{c}"
        _w(base / "topology/physical_package_id", 0)
        _w(base / "topology/core_id", c // 2 if c < 4 else c)   # 2 P-cores x2 threads, 4 E-cores
    if hybrid:
        _w(r / "devices/cpu_core/cpus", "0-3")
        _w(r / "devices/cpu_atom/cpus", "4-7")
    b = r / "class/power_supply/BAT0"
    _w(b / "type", "Battery")
    _w(b / "status", "Discharging" if discharging else "Charging")
    _w(b / "capacity", 55)
    if battery == "energy":
        _w(b / "energy_now", 30_000_000)     # uWh = 30 Wh
        _w(b / "energy_full", 50_000_000)
        _w(b / "power_now", 9_500_000)       # uW = 9.5 W
    else:
        _w(b / "charge_now", 2_000_000)      # uAh
        _w(b / "charge_full", 4_000_000)
        _w(b / "voltage_now", 12_000_000)    # uV
        _w(b / "current_now", 800_000)       # uA
    ac = r / "class/power_supply/AC"
    _w(ac / "type", "Mains")
    _w(ac / "online", 0 if discharging else 1)
    return r


def test_linux_battery_energy_counters(tmp_path):
    ps = posix.read_battery(fake_sysfs(tmp_path))
    assert not ps.on_ac and ps.remaining_mwh == pytest.approx(30000) and ps.discharge_mw == pytest.approx(9500)
    assert ps.percent == pytest.approx(60.0)


def test_linux_battery_charge_counters_and_ac(tmp_path):
    ps = posix.read_battery(fake_sysfs(tmp_path, battery="charge", discharging=False))
    assert ps.on_ac and ps.remaining_mwh == pytest.approx(24000) and ps.discharge_mw == 0.0


def test_linux_no_battery(tmp_path):
    ps = posix.read_battery(tmp_path / "empty")
    assert ps.percent is None and ps.on_ac


def test_linux_topology_and_hybrid(tmp_path):
    topo = posix.read_topology(fake_sysfs(tmp_path, hybrid=True))
    assert len(topo) == 8 and len({t.core_index for t in topo}) == 6
    assert {t.logical_index for t in topo if t.efficiency_class == 0} == {4, 5, 6, 7}


@pytest.mark.skipif(sys.platform.startswith("win"), reason="':' is not allowed in Windows paths (fake Linux sysfs)")
def test_rapl_wraparound(tmp_path):
    z = tmp_path / "sys/class/powercap/intel-rapl:0"
    _w(z / "energy_uj", 999_000_000)
    _w(z / "max_energy_range_uj", 1_000_000_000)
    _w(tmp_path / "sys/class/powercap/intel-rapl:0:0/energy_uj", 5)   # sub-zone ignored
    m = posix.RaplMeter(tmp_path / "sys")
    assert m.available and len(m.paths) == 1
    m.read_j()
    _w(z / "energy_uj", 3_000_000)          # wrapped
    assert m.read_j() == pytest.approx(4.0)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux scheduler syscall")
def test_linux_eco_sets_sched_batch_on_all_threads():
    b = posix.PosixBackend()
    caps = b.capabilities()
    p = subprocess.Popen([sys.executable, "-c",
                          "import threading,time\n[threading.Thread(target=time.sleep,args=(3,)).start() for _ in range(3)]\ntime.sleep(3)"])
    try:
        import time
        time.sleep(0.3)
        res = b.apply(p.pid, Mode.ECO, caps)
        assert res.ok, res.failures
        for tid in os.listdir(f"/proc/{p.pid}/task"):
            assert os.sched_getscheduler(int(tid)) == os.SCHED_BATCH
        res = b.apply(p.pid, Mode.BOOST, caps)
        assert os.sched_getscheduler(p.pid) == os.SCHED_OTHER
    finally:
        p.kill()


# ---------------------------------------------------------------- live API client
class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_electricitymaps_client_parses_and_degrades(tmp_path, monkeypatch):
    calls = []
    hist = [{"datetime": f"2026-10-12T{h:02d}:00:00.000Z", "carbonIntensity": 600 + h} for h in range(24)]

    def fake(req, timeout=0):
        url = req.full_url
        calls.append((url, req.headers.get("Auth-token")))
        if "forecast" in url:
            raise urllib.error.HTTPError(url, 403, "not in plan", {}, None)
        if "history" in url:
            return _Resp(json.dumps({"zone": "IN-SO", "history": hist}).encode())
        return _Resp(json.dumps({"zone": "IN-SO", "carbonIntensity": 640,
                                 "datetime": "2026-10-13T06:00:00.000Z", "isEstimated": True}).encode())
    monkeypatch.setattr("urllib.request.urlopen", fake)
    src = ElectricityMapsSource("IN-SO", "TOKEN", tmp_path / "cache.json", prior=700)
    lat = src.latest(0)
    assert lat.value == 640 and lat.source == "electricitymaps"
    assert all(t == "TOKEN" for _, t in calls) and "zone=IN-SO" in calls[0][0]
    assert len(src.observed_history()) == 25
    fc = src.forecast(0, [3600.0, 7200.0])          # no forecast in plan -> seasonal naive
    assert len(fc) == 2 and all(500 < v < 800 for v in fc)
    sig = CarbonSignal(src, prior=700)
    sig.tick(0.0)
    assert sig.obs_n >= 24                           # history back-filled into climatology


def test_electricitymaps_bad_token_falls_back_to_prior(tmp_path, monkeypatch):
    def fake(req, timeout=0):
        raise urllib.error.HTTPError(req.full_url, 401, "bad token", {}, None)
    monkeypatch.setattr("urllib.request.urlopen", fake)
    src = ElectricityMapsSource("IN-SO", "WRONG", tmp_path / "c.json", prior=700)
    lat = src.latest(0)
    assert not lat.available and math.isinf(lat.observed_age_s) and "401" in src.last_error
    eff, sd = CarbonSignal(src, prior=700).plan_curve(0, [0.0, 3600.0])
    assert list(eff) == [700.0, 700.0]


# ---------------------------------------------------------------- robust mode selection
class NoisyPred(Predictor):
    """ECO truly saves 17 %, but this job's prediction says it costs 5 % (error 0.3)."""
    mode_prior = {"ECO": (math.log(0.83), 0.03)}

    def __init__(self, rel_sigma):
        self.rs = rel_sigma

    def predict(self, job, mode, ctx):
        if mode == "ECO":
            return Prediction(135, 160, 135, 1.05, "x", rel_sigma=self.rs)
        return Prediction(100, 120, 100, 1.0, "x")


def _battery_ctx():
    return PolicyContext(0.0, False, 0.3, 500.0, None, Ctx(), ["NORMAL", "ECO"])


def test_shrinkage_recovers_a_good_mode_from_a_noisy_prediction():
    cfg = Config()
    job = Job("j", "hash", 1, 8, 0, 10_000)
    assert build_policy("gt_no_gate", cfg, NoisyPred(0.3), NoisyPred(0.3)).decide(job, _battery_ctx()).mode == "NORMAL"
    assert build_policy("greenthread", cfg, NoisyPred(0.3), NoisyPred(0.3)).decide(job, _battery_ctx()).mode == "ECO"


def test_confident_prediction_is_not_overridden():
    cfg = Config()
    job = Job("j", "hash", 1, 8, 0, 10_000)
    # tiny uncertainty: the job-specific prediction (ECO worse) must win over the prior
    assert build_policy("greenthread", cfg, NoisyPred(0.005), NoisyPred(0.005)).decide(job, _battery_ctx()).mode == "NORMAL"


def test_tight_deadlines_quantile_check_prevents_misses():
    cfg = Config()
    frames = []
    for seed in range(2):
        sc = SimScenario(trace="data/traces/de_2020_hourly.csv", days=5, seed=seed, workload="tight",
                         on_ac=False, soc=0.3, escalate=False)
        frames += [simulate(p, cfg, sc) for p in ("default", "heuristic", "gt_no_quantile", "greenthread")]
    s = summarise(pd.concat(frames)).groupby("policy").miss_rate.mean()
    assert s["default"] == 0.0
    assert s["greenthread"] < s["gt_no_quantile"] <= s["heuristic"]


# ---------------------------------------------------------------- ledger tools
def test_ledger_report_on_synthetic_log(tmp_path):
    rows, t = [], 0.0
    cap = 30000.0
    for h in range(10):                      # charge for 10 h at 100 g/kWh (plugged in)
        rows.append({"unix_time": t, "on_ac": 1, "remaining_mwh": cap, "grid_intensity": 100})
        cap += 2000; t += 3600
    for h in range(5):                       # discharge at a dirty 800 g/kWh
        rows.append({"unix_time": t, "on_ac": 0, "remaining_mwh": cap, "grid_intensity": 800})
        cap -= 4000; t += 3600
    p = tmp_path / "log.csv"
    pd.DataFrame(rows).to_csv(p, index=False)
    r = report(p, adapter_eff=1.0, charge_eff=1.0)
    assert r["battery_energy_used_wh"] == pytest.approx(16.0)
    assert r["carbon_g"]["time_of_use"] == pytest.approx(16.0 * 0.8)
    # ledger: 30 Wh initial @100 + 18 Wh @100 -> 100 g/kWh
    assert r["carbon_g"]["ledger"] == pytest.approx(16.0 * 0.1)
    assert r["time_of_use_error_pct"] == pytest.approx(700.0)


def test_ledger_profile_simulation_is_consistent():
    src = CSVReplaySource("data/traces/us-cal_2020_hourly.csv", "2020-01-01T00:00:00", 3600)
    solar = simulate_profile(src, PROFILES[2], days=30)
    night = simulate_profile(src, PROFILES[0], days=30)
    assert solar["mean_ledger_intensity"] < night["mean_ledger_intensity"]   # CAISO: clean at noon
    assert solar["battery_wh"] > 0 and night["battery_wh"] > 0


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="uses setsid to create a non-child process")
def test_background_load_ignores_own_children_but_sees_other_processes():
    import time
    from greenthread.engine import BackgroundLoadMeter
    burn = "import time\nt=time.time()+2.5\nwhile time.time()<t: pass"
    n = os.cpu_count() or 1

    def measure(start=None):
        m = BackgroundLoadMeter(n, window_s=1.5)
        p = start() if start else None
        t_end = time.time() + 2.0
        while time.time() < t_end:
            m.sample(); time.sleep(0.1)
        v = m.value()
        if p is not None and hasattr(p, "wait"):
            p.wait()
        return v
    base = measure()
    own = measure(lambda: subprocess.Popen([sys.executable, "-c", burn]))          # our child
    time.sleep(0.5)
    other = measure(lambda: subprocess.run(["setsid", "bash", "-c", f"{sys.executable} -c '{burn}' &"]))
    time.sleep(0.6)
    # relative to whatever else is running: our own child adds ~nothing, a foreign process adds load
    assert other - base > (own - base) + 0.3 / n


def test_stress_table_keeps_rows_with_missing_keys():
    from greenthread.experiments import _stress_table
    rows = []
    for soc in (None, 0.3):
        for pol in ("default", "greenthread"):
            for seed in range(2):
                rows.append({"policy": pol, "seed": seed, "trace": "t", "carbon_g": 1.0 if pol == "default" else 0.9,
                             "energy_wh": 1.0, "deadline_met": True, "delay_s": 0.0, "slowdown": 1.0,
                             "escalated": False, "decide_ms": 0.0, "soc": soc})
    t = _stress_table(pd.DataFrame(rows), ["soc"])
    assert set(t.soc) == {"None", "0.3"} and len(t) == 4
