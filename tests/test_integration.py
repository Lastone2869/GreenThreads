"""Integration tests: real child processes, real OS controls (POSIX backend here,
Windows backend on Windows), full engine loop, simulator and CLI."""
import json
import subprocess
import sys

import pandas as pd
import pytest

from greenthread.accounting import BatteryCarbonLedger, PowerModel
from greenthread.backends import NullBackend, get_backend
from greenthread.backends.base import Mode
from greenthread.carbon import CarbonSignal, CSVReplaySource
from greenthread.config import Config, PACKAGE_ROOT
from greenthread.engine import Engine
from greenthread.jobs import Decision, Job, Prediction
from greenthread.policy import Policy, build_policy
from greenthread.simulator import SimScenario, simulate, summarise
from tests.test_policy_model import FakePred


class _ACBackend:
    def __init__(self, target):
        self._target = target
    def __getattr__(self, item):
        return getattr(self._target, item)
    def power_status(self):
        ps = self._target.power_status()
        from greenthread.backends.base import PowerStatus
        return PowerStatus(True, ps.percent, ps.remaining_mwh, ps.full_mwh, 0.0, ps.source)


def make_engine(policy, trace_csv, backend=None, seconds_per_hour=2.0):
    cfg = Config()
    cfg.scheduler.tick_s = 0.05
    cfg.scheduler.defer_step_s = 1.0
    src = CSVReplaySource(trace_csv, "2020-01-01T08:00:00", seconds_per_hour)
    sig = CarbonSignal(src, prior=src.mean, freshness_half_life_s=3600)
    led = BatteryCarbonLedger(None, 0.85, 500)
    b = backend or get_backend()
    return Engine(cfg, _ACBackend(b), policy, sig, src, led, PowerModel(), None, verbose=False)


class ShortPred(FakePred):
    table = {"NORMAL": (0.4, 0.001), "ECO": (0.5, 0.0008), "HALF": (0.6, 0.00105), "ECO_HALF": (0.8, 0.0009)}


def test_engine_runs_real_jobs_and_defers_into_clean_window(trace_csv):
    pol = build_policy("greenthread", Config(), ShortPred(), ShortPred())
    pol.cfg.scheduler.defer_step_s = 1.0
    eng = make_engine(pol, trace_csv)
    jobs = [Job("now", "hash", 50, 1, 0.0, 30.0, deferrable=False),
            Job("later", "hash", 50, 1, 0.0, 12.0, deferrable=True),   # clean window at 4-8 s
            Job("ui", "probe", 1.0, 1, 0.0, 30.0, interactive=True)]
    res = {r.job_id: r for r in eng.run(jobs, timeout_s=60)}
    assert set(res) == {"now", "later", "ui"}
    assert all(r.deadline_met for r in res.values())
    assert res["now"].deferred_s < 1.0
    assert 3.0 <= res["later"].start_s <= 9.0
    assert res["later"].intensity_g_per_kwh == pytest.approx(100.0)
    assert res["now"].intensity_g_per_kwh == pytest.approx(500.0)
    assert res["ui"].mode == "NORMAL" and res["ui"].latency["p95_ms"] > 0
    assert res["later"].carbon_g < res["now"].carbon_g


class ForceEco(Policy):
    name = "force-eco"
    predictor = object()

    def decide(self, job, ctx):
        return Decision(job.id, ctx.t, "ECO", "test", Prediction(10.0, 20.0, 10.0, 0.001))


def test_engine_escalates_when_deadline_at_risk(trace_csv):
    eng = make_engine(ForceEco(), trace_csv)
    r = eng.run([Job("risky", "hash", 1500, 1, 0.0, 6.0)], timeout_s=60)[0]
    assert r.escalated and r.mode_final == "BOOST"
    assert any("nice" in c or "cpus" in c for c in r.controls)


@pytest.mark.skipif(sys.platform == "win32", reason="posix-specific check")
def test_posix_backend_applies_real_controls():
    b = get_backend()
    caps = b.capabilities()
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(3)"])
    try:
        import psutil
        res = b.apply(p.pid, Mode.ECO_HALF if Mode.ECO_HALF in caps.supported_modes() else Mode.ECO, caps)
        assert res.ok, res.failures
        assert psutil.Process(p.pid).nice() == 10
    finally:
        p.kill()


def test_null_backend_dry_run(trace_csv):
    eng = make_engine(build_policy("default", Config(), None, None), trace_csv, backend=NullBackend())
    r = eng.run([Job("x", "compress", 10, 1, 0, 30)], timeout_s=30)[0]
    assert r.deadline_met and r.controls == ["(dry-run) NORMAL"]


def test_simulator_deterministic_and_safe():
    cfg = Config()
    sc = SimScenario(trace="data/traces/gb_2020_hourly.csv", days=3, seed=4)
    a = simulate("greenthread", cfg, sc)
    b = simulate("greenthread", cfg, sc)
    pd.testing.assert_frame_equal(a.drop(columns='decide_ms'), b.drop(columns='decide_ms'))
    d = simulate("default", cfg, sc)
    assert (d.delay_s == 0).all() and (d["mode"] == "NORMAL").all()
    s = summarise(pd.concat([a, d]))
    assert s.set_index("policy").loc["greenthread", "miss_rate"] <= 0.05


def test_cli_detect_and_carbon():
    out = subprocess.run([sys.executable, "-m", "greenthread", "detect"], capture_output=True, text=True,
                         cwd=PACKAGE_ROOT, timeout=60)
    assert out.returncode == 0, out.stderr
    info = json.loads(out.stdout)
    assert "NORMAL" in info["supported_modes"]
    out = subprocess.run([sys.executable, "-m", "greenthread", "carbon", "--hours", "3"], capture_output=True,
                         text=True, cwd=PACKAGE_ROOT, timeout=60)
    assert out.returncode == 0 and "effective" in out.stdout
