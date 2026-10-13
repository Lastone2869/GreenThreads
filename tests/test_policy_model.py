import numpy as np
import pandas as pd
import pytest

from greenthread.carbon import CarbonSignal, CSVReplaySource
from greenthread.config import Config
from greenthread.jobs import Job, Prediction
from greenthread.model import Ctx, MLPredictor, Predictor, TablePredictor, evaluate_predictors
from greenthread.policy import PolicyContext, build_policy, make_specs, RBACIPolicy

MODES = ["NORMAL", "ECO", "HALF", "ECO_HALF"]


class FakePred(Predictor):
    """NORMAL: 100 s / 1.0 Wh; ECO: 130 s / 0.8 Wh; HALF: 150 s / 1.05 Wh; ECO_HALF 190 s / 0.9 Wh."""
    name = "fake"
    table = {"NORMAL": (100, 1.0), "ECO": (130, 0.8), "HALF": (150, 1.05), "ECO_HALF": (190, 0.9)}

    def __init__(self, hi=1.2):
        self.hi = hi

    def predict(self, job, mode, ctx):
        q, e = self.table[mode]
        return Prediction(q, q * self.hi, q, e, "fake")


def ctx_for(signal, t=0.0, on_ac=True, soc=None):
    return PolicyContext(t, on_ac, soc, 400.0, signal, Ctx(), MODES)


@pytest.fixture
def cfg():
    c = Config()
    c.scheduler.defer_step_s = 60
    return c


@pytest.fixture
def signal(trace_csv):
    # trace starts 08:00 -> clean window (100 g) begins 2 h later
    src = CSVReplaySource(trace_csv, "2020-01-01T08:00:00", 3600)
    return CarbonSignal(src, prior=float(src.mean), freshness_half_life_s=3600, forecast_half_life_h=48)


def gt(cfg, name="greenthread", pred=None):
    return build_policy(name, cfg, pred or FakePred(), pred or FakePred())


def test_interactive_never_deferred_or_throttled(cfg, signal):
    d = gt(cfg).decide(Job("i", "probe", 10, 1, 0, 1e6, deferrable=True, interactive=True), ctx_for(signal))
    assert d.start_at == 0 and d.mode == "NORMAL"


def test_non_deferrable_starts_now(cfg, signal):
    d = gt(cfg).decide(Job("a", "hash", 1, 1, 0, 6 * 3600, deferrable=False), ctx_for(signal))
    assert d.start_at == 0


def test_deferrable_moves_into_clean_window_and_respects_deadline(cfg, signal):
    job = Job("b", "hash", 1, 1, 0, 6 * 3600, deferrable=True)
    d = gt(cfg).decide(job, ctx_for(signal))
    assert 2 * 3600 - 300 <= d.start_at <= 6 * 3600
    assert d.start_at + d.prediction.qhi + cfg.scheduler.safety_margin_s <= job.deadline_s


def test_quantile_constraint_excludes_slow_modes(cfg, signal):
    # slack 200 s: NORMAL qhi=120 ok, ECO qhi=156 ok, HALF 180 ok, ECO_HALF 228 infeasible
    job = Job("c", "hash", 1, 1, 0, 200, deferrable=True)
    for _ in range(3):
        d = gt(cfg).decide(job, ctx_for(signal))
        assert d.mode != "ECO_HALF"
        assert d.start_at + d.prediction.qhi + 2 <= 200


def test_infeasible_falls_back_to_normal_now(cfg, signal):
    d = gt(cfg).decide(Job("d", "hash", 1, 1, 0, 50, deferrable=True), ctx_for(signal))
    assert d.mode == "NORMAL" and d.start_at == 0 and "fallback" in d.reason


def test_no_deferral_on_battery_and_low_soc_prefers_efficient_mode(cfg, signal):
    job = Job("e", "hash", 1, 1, 0, 6 * 3600, deferrable=True)
    d = gt(cfg).decide(job, ctx_for(signal, on_ac=False, soc=0.1))
    assert d.start_at == 0 and d.mode == "ECO"


def test_vectorised_window_matches_bruteforce(cfg, signal):
    pol = gt(cfg)
    job = Job("f", "hash", 1, 1, 0, 8 * 3600, deferrable=True)
    d = pol.decide(job, ctx_for(signal))
    # brute force the same objective on the same grid of offsets for mode NORMAL/ECO
    w = cfg.weights
    best = None
    for m in MODES:
        q, e = FakePred.table[m]
        for o in np.arange(0, 8 * 3600, 60.0):
            if o + q * 1.2 + 2 > 8 * 3600:
                continue
            xs = np.linspace(o, o + q, 400)
            eff, sig = signal.plan_curve(0.0, list(xs))
            i_now = np.mean(signal.plan_curve(0.0, list(np.linspace(0, 100, 400)))[0])
            c = w.carbon * e * eff.mean() / (1.0 * i_now) * (1 + w.risk * sig.mean() / signal.prior)
            tot = c + w.latency * (o + q) / (8 * 3600) + (w.defer_overhead if o > 0 else 0)
            if best is None or tot < best[0]:
                best = (tot, o, m)
    assert d.mode == best[2]
    assert abs(d.start_at - best[1]) <= 15 * 60    # grid resolution differences only


def test_stale_signal_reduces_deferral(cfg, trace_csv):
    # outage from t=0: last value seen is the dirty 500 g, forecast frozen and stale
    src = CSVReplaySource(trace_csv, "2020-01-01T08:00:00", 3600, outages=[(-1, 1e9)])
    aware = CarbonSignal(src, prior=src.mean, freshness_half_life_s=600, forecast_half_life_h=4)
    job = Job("g", "hash", 1, 1, 3 * 3600, 3 * 3600 + 6 * 3600, deferrable=True)
    d_aware = gt(cfg).decide(job, ctx_for(aware, t=3 * 3600))
    unaware = CarbonSignal(src, prior=src.mean, uncertainty_aware=False)
    d_unaware = gt(cfg, "gt_no_uncertainty").decide(job, ctx_for(unaware, t=3 * 3600))
    assert d_aware.start_at - job.arrival_s <= d_unaware.start_at - job.arrival_s
    assert d_aware.start_at == job.arrival_s       # nothing trustworthy to wait for


def test_all_named_policies_build(cfg):
    for n in ["default", "heuristic", *make_specs(cfg)]:
        assert build_policy(n, cfg, FakePred(), FakePred()).name == n


# ---------------------------------------------------------------- models
def test_ml_predictor_learns_and_is_calibrated(synthetic_runs):
    df = synthetic_runs
    tr, ca, te = df.iloc[:240], df.iloc[240:320], df.iloc[320:]
    m = evaluate_predictors(tr, te, ca, 0.9)
    assert m["ml"]["mape_pct"] < m["table"]["mape_pct"]     # bg_load effect is invisible to the table
    assert 0.75 <= m["ml"]["coverage_q90"] <= 1.0


def test_predictor_save_load_and_mode_ordering(synthetic_runs, tmp_path):
    ml = MLPredictor().fit(synthetic_runs.iloc[:300], synthetic_runs.iloc[300:])
    ml.save(tmp_path / "m.pkl")
    ml2 = MLPredictor.load(tmp_path / "m.pkl")
    j = Job("x", "matmul", 400, 4)
    pn, pe = ml2.predict(j, "NORMAL", Ctx()), ml2.predict(j, "ECO", Ctx())
    assert pe.q50 > pn.q50 and pn.qhi >= pn.q50 and pn.energy_wh > 0


def test_table_predictor_fallbacks(synthetic_runs):
    tb = TablePredictor().fit(synthetic_runs)
    p = tb.predict(Job("x", "hash", 100, 3), "ECO", Ctx())   # threads=3 never seen
    assert p.q50 > 0 and p.qhi >= p.q50
