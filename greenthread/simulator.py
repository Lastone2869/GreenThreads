"""Trace-driven simulator for large, repeatable policy evaluation.

It runs the *same* policy code (policy.RBACIPolicy etc.) against a simulated
laptop over days of real carbon-intensity data. Real-machine runs (engine.py)
cannot cover weeks of grid variation, many seeds, outages and forecast errors;
the simulator can. Its machine parameters are explicit assumptions that can be
re-calibrated from data collected on the target laptop (`calibrate_from_csv`).

Energy model while a job runs (Wh):
    E = runtime_h * (P_active + busy_cores(m) * P_core(m))
P_active captures platform power that is only spent because the job keeps the
machine busy (package/uncore/memory), which is what makes "slow but efficient"
modes a genuine trade-off rather than a free win.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .accounting import attributable_carbon_g
from .carbon import CarbonSignal, CSVReplaySource
from .config import Config
from .jobs import Job, Prediction
from .model import Ctx, Predictor
from .policy import PolicyContext, build_policy

HOUR = 3600.0


@dataclass
class SimMachine:
    logical_cpus: int = 8
    physical_cores: int = 4           # e.g. i7-10th gen U-series: 4C/8T
    p_active_w: float = 3.0           # extra platform power while busy
    p_core_w: float = 4.5             # per fully-busy logical CPU, NORMAL
    eco_power_ratio: float = 0.58     # P_core(ECO)/P_core(NORMAL)   [assumption]
    eco_slowdown: float = 1.35        # runtime(ECO)/runtime(NORMAL) [assumption]
    runtime_noise: float = 0.10       # lognormal sigma of true runtime
    modes: list[str] = field(default_factory=lambda: ["NORMAL", "ECO", "HALF", "ECO_HALF"])

    def busy(self, threads: int, mode: str) -> float:
        cap = self.physical_cores / 2 if mode in ("HALF", "ECO_HALF") else self.logical_cpus
        return float(min(threads, cap))

    def slowdown(self, threads: int, mode: str) -> float:
        s = self.busy(threads, "NORMAL") / self.busy(threads, mode)
        if mode in ("ECO", "ECO_HALF"):
            s *= self.eco_slowdown
        return s

    def power_w(self, threads: int, mode: str) -> float:
        pc = self.p_core_w * (self.eco_power_ratio if mode in ("ECO", "ECO_HALF") else 1.0)
        return self.p_active_w + self.busy(threads, mode) * pc

    def energy_wh(self, runtime_s: float, threads: int, mode: str) -> float:
        return runtime_s / HOUR * self.power_w(threads, mode)


@dataclass
class SimJob:
    job: Job
    base_s: float              # NORMAL-mode median runtime
    noise: float               # realised lognormal factor


class SimPredictor(Predictor):
    """Prediction with controlled error: log-error ~ N(bias, sigma).

    sigma is set from the *measured* held-out error of the real ML / table
    predictors (see experiments), so simulated decisions inherit realistic
    prediction quality. qhi = q50 * exp(z * sigma_total) (calibrated quantile).
    """

    def __init__(self, machine: SimMachine, jobs: dict[str, SimJob], sigma: float, bias: float = 0.0,
                 quantile: float = 0.9, seed: int = 0, name: str = "sim"):
        self.m, self.jobs, self.sigma, self.bias, self.quantile = machine, jobs, sigma, bias, quantile
        self.rng = np.random.default_rng(seed)
        self.name = name
        self._err: dict = {}
        from statistics import NormalDist
        self.z = NormalDist().inv_cdf(quantile)

    def predict(self, job, mode, ctx):
        sj = self.jobs[job.id]
        key = (job.id, mode)
        if key not in self._err:  # one error draw per (job, mode), stable across replans
            self._err[key] = self.rng.normal(self.bias, self.sigma)
        true_med = sj.base_s * self.m.slowdown(job.threads, mode)
        q50 = true_med * math.exp(self._err[key])
        total_sigma = math.sqrt(self.sigma ** 2 + self.m.runtime_noise ** 2)
        qhi = q50 * math.exp(self.z * total_sigma)
        return Prediction(q50, qhi, q50 * self.m.busy(job.threads, mode),
                          self.m.energy_wh(q50, job.threads, mode), self.name)


# ----------------------------------------------------------------- workloads
TEMPLATES = [
    # name, base runtime (min), threads, relative deadline (h), deferrable, weight
    ("backup", 25, 2, 10.0, True, 2),
    ("build", 8, 8, 1.0, False, 3),
    ("ml_train", 90, 8, 14.0, True, 1),
    ("video_export", 20, 8, 3.0, True, 2),
    ("indexing", 15, 1, 8.0, True, 2),
    ("tests", 5, 4, 0.5, False, 3),
]


def generate_jobs(days: int, jobs_per_day: int, machine: SimMachine, seed: int = 0) -> dict[str, SimJob]:
    rng = np.random.default_rng(seed)
    weights = np.array([t[5] for t in TEMPLATES], dtype=float)
    weights /= weights.sum()
    out = {}
    for d in range(days):
        for k in range(jobs_per_day):
            name, base_min, thr, dl_h, defer, _ = TEMPLATES[rng.choice(len(TEMPLATES), p=weights)]
            arrival = d * 24 * HOUR + rng.uniform(8, 22) * HOUR      # submitted during waking hours
            base = base_min * 60 * rng.lognormal(0, 0.3)
            thr = min(thr, machine.logical_cpus)
            j = Job(f"d{d}-{k}-{name}", "hash", units=1.0, threads=thr, arrival_s=arrival,
                    deadline_s=arrival + max(dl_h * HOUR, base * 1.6), deferrable=defer)
            out[j.id] = SimJob(j, base, float(rng.lognormal(0, machine.runtime_noise)))
    return out


# ----------------------------------------------------------------- simulation
@dataclass
class SimScenario:
    trace: str
    days: int = 14
    jobs_per_day: int = 10
    on_ac: bool = True
    soc: float | None = None             # battery scenario state of charge (0..1)
    ledger_intensity: float | None = None
    forecast_noise: float = 0.0
    outage_frac: float = 0.0             # fraction of time with no fresh signal
    pred_sigma: dict = field(default_factory=lambda: {"ml": 0.08, "table": 0.25})
    pred_bias: dict = field(default_factory=lambda: {"ml": 0.0, "table": 0.0})
    start: str = "2020-01-08T00:00:00"
    seed: int = 0


def _outages(days: int, frac: float, rng) -> list[tuple[float, float]]:
    if frac <= 0:
        return []
    out, total = [], days * 24 * HOUR
    n = max(1, int(days * 2))
    length = frac * total / n
    for s in rng.uniform(0, total - length, n):
        out.append((float(s), float(s + length)))
    return out


def simulate(policy_name: str, cfg: Config, sc: SimScenario, machine: SimMachine | None = None) -> pd.DataFrame:
    m = machine or SimMachine()
    rng = np.random.default_rng(sc.seed + 1000)
    src = CSVReplaySource(cfg.resolve(sc.trace), sc.start, 3600.0, sc.forecast_noise,
                          _outages(sc.days, sc.outage_frac, rng), seed=sc.seed)
    jobs = generate_jobs(sc.days, sc.jobs_per_day, m, sc.seed)
    ml = SimPredictor(m, jobs, sc.pred_sigma["ml"], sc.pred_bias["ml"], cfg.scheduler.deadline_quantile, sc.seed, "ml")
    tb = SimPredictor(m, jobs, sc.pred_sigma["table"], sc.pred_bias["table"], cfg.scheduler.deadline_quantile,
                      sc.seed + 1, "table")
    pol = build_policy(policy_name, cfg, ml, tb)
    aware = getattr(getattr(pol, "spec", None), "uncertainty", True)
    sig = CarbonSignal(src, cfg.carbon.prior_intensity or src.mean, cfg.carbon.freshness_half_life_s,
                       cfg.carbon.forecast_half_life_h, uncertainty_aware=aware)
    ledger_int = sc.ledger_intensity or src.mean / cfg.energy.charge_efficiency
    mctx = Ctx(m.logical_cpus, m.physical_cores, 0.0, sc.on_ac)
    modes = [x for x in m.modes if x in cfg.scheduler.allow_modes]
    rows = []
    margin = cfg.scheduler.safety_margin_s
    for sj in sorted(jobs.values(), key=lambda s: s.job.arrival_s):
        j = sj.job
        ctx = PolicyContext(j.arrival_s, sc.on_ac, sc.soc, ledger_int, sig, mctx, modes)
        dec = pol.decide(j, ctx)
        # receding-horizon replanning every hour while deferred
        t = j.arrival_s
        while dec.start_at > t + 1e-6:
            t_next = min(dec.start_at, t + HOUR)
            if t_next >= dec.start_at:
                break
            t = t_next
            nd = pol.decide(j, PolicyContext(t, sc.on_ac, sc.soc, ledger_int, sig, mctx, modes))
            if nd.start_at <= dec.start_at:      # commitment rule (same as engine)
                dec = nd
        start, mode = max(dec.start_at, j.arrival_s), dec.mode
        true_rt = sj.base_s * m.slowdown(j.threads, mode) * sj.noise
        # runtime escalation (same rule as engine): checked every 60 s
        escalated, end, e_wh = False, start + true_rt, None
        p = dec.prediction
        if cfg.scheduler.escalate and mode != "NORMAL" and p is not None and pol.predictor is not None:
            tt = start
            while tt < end:
                elapsed = tt - start
                frac_pred = min(0.95, elapsed / max(p.q50, 1e-6))
                if tt + (1 - frac_pred) * p.qhi > j.deadline_s - margin:
                    done = elapsed / true_rt
                    rest = (1 - done) * sj.base_s * m.slowdown(j.threads, "NORMAL") * sj.noise
                    e_wh = m.energy_wh(elapsed, j.threads, mode) + m.energy_wh(rest, j.threads, "NORMAL")
                    end, escalated = tt + rest, True
                    break
                tt += 60.0
        if e_wh is None:
            e_wh = m.energy_wh(end - start, j.threads, mode)
        if sc.on_ac:
            inten = src.mean_true(start, end, n=max(4, int((end - start) / 900)))
        else:
            inten = ledger_int
        carbon = attributable_carbon_g(e_wh, sc.on_ac, inten, ledger_int, cfg.energy.adapter_efficiency)
        rows.append({"policy": policy_name, "job": j.id, "deferrable": j.deferrable, "threads": j.threads,
                     "mode": mode, "start_s": start, "end_s": end, "arrival_s": j.arrival_s,
                     "deadline_s": j.deadline_s, "deadline_met": end <= j.deadline_s + 1e-6,
                     "delay_s": start - j.arrival_s, "runtime_s": end - start,
                     "slowdown": (end - j.arrival_s) / (sj.base_s * sj.noise), "energy_wh": e_wh,
                     "carbon_g": carbon, "escalated": escalated, "decide_ms": dec.decide_ms,
                     "reason": dec.reason, "seed": sc.seed, "trace": sc.trace, "on_ac": sc.on_ac, "soc": sc.soc})
    return pd.DataFrame(rows)


def summarise(df: pd.DataFrame, baseline: str = "default") -> pd.DataFrame:
    g = df.groupby(["policy", "seed"]).agg(carbon_g=("carbon_g", "sum"), energy_wh=("energy_wh", "sum"),
                                           miss_rate=("deadline_met", lambda s: 1 - s.mean()),
                                           mean_delay_h=("delay_s", lambda s: s.mean() / HOUR),
                                           p95_slowdown=("slowdown", lambda s: s.quantile(0.95)),
                                           escalations=("escalated", "sum"),
                                           decide_ms=("decide_ms", "mean")).reset_index()
    base = g[g.policy == baseline].set_index("seed")
    g["carbon_saving_pct"] = g.apply(lambda r: 100 * (1 - r.carbon_g / base.loc[r.seed, "carbon_g"]), axis=1)
    g["energy_saving_pct"] = g.apply(lambda r: 100 * (1 - r.energy_wh / base.loc[r.seed, "energy_wh"]), axis=1)
    return g


def ci95(x: pd.Series) -> tuple[float, float, float]:
    x = np.asarray(x, dtype=float)
    mean = float(x.mean())
    if len(x) < 2:
        return mean, mean, mean
    from scipy import stats
    h = stats.t.ppf(0.975, len(x) - 1) * x.std(ddof=1) / math.sqrt(len(x))
    return mean, mean - h, mean + h


def calibrate_from_csv(df: pd.DataFrame, machine: SimMachine) -> SimMachine:
    """Set ECO slowdown from measured runtime ratios (and power ratio if measured)."""
    d = df[df.bg_threads == 0] if "bg_threads" in df else df
    piv = d.groupby(["kind", "units", "threads", "mode"]).runtime_s.median().unstack("mode")
    if {"NORMAL", "ECO"} <= set(piv.columns):
        r = (piv["ECO"] / piv["NORMAL"]).dropna()
        if len(r):
            machine.eco_slowdown = float(r.median())
    if "energy_wh_measured" in d and d.energy_wh_measured.notna().sum() > 10:
        e = d.dropna(subset=["energy_wh_measured"]).groupby(["kind", "units", "threads", "mode"]).energy_wh_measured.median().unstack("mode")
        if {"NORMAL", "ECO"} <= set(e.columns):
            er = (e["ECO"] / e["NORMAL"]).dropna().median()
            machine.eco_power_ratio = float(er / machine.eco_slowdown)
    return machine
