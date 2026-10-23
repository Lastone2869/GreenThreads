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
from datetime import datetime, timedelta

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
    """Prediction with controlled, *structured* error (log scale).

    structure="shared" (how the real ML/table predictors behave, measured on real
    runs): one error per job shared by all its modes, eps_job ~ N(bias, sigma_abs),
    plus a smaller per-(job, mode) error on the mode effect, eps_jm ~ N(0, sigma_rel).
    structure="independent": every (job, mode) gets its own N(bias, sigma_abs) error
    (what version 1 of the simulator assumed).
    qhi = q50 * exp(z_tau * total sigma)  (a calibrated quantile).
    """

    def __init__(self, machine: SimMachine, jobs: dict[str, SimJob], sigma: float, bias: float = 0.0,
                 quantile: float = 0.9, seed: int = 0, name: str = "sim", sigma_rel: float | None = None,
                 structure: str = "shared"):
        self.m, self.jobs, self.sigma, self.bias, self.quantile = machine, jobs, sigma, bias, quantile
        self.sigma_rel = sigma if sigma_rel is None else sigma_rel
        self.structure = structure
        self.rng = np.random.default_rng(seed)
        self.name = name
        self._err: dict = {}
        from statistics import NormalDist
        self.z = NormalDist().inv_cdf(quantile)
        # what a model trained on this machine would know about each mode on average:
        # mean / sd of the true log energy ratio over the jobs' thread counts
        thr = [sj.job.threads for sj in jobs.values()] or [1]
        self.mode_prior = {}
        for mo in machine.modes:
            if mo == "NORMAL":
                continue
            lr = np.log([machine.energy_wh(machine.slowdown(t, mo), t, mo) / machine.energy_wh(1.0, t, "NORMAL")
                         for t in thr])
            self.mode_prior[mo] = (float(lr.mean()), float(lr.std()))

    def _eps(self, key, mu, sd):
        if key not in self._err:   # one draw per key, stable across replans
            self._err[key] = self.rng.normal(mu, sd)
        return self._err[key]

    def predict(self, job, mode, ctx):
        sj = self.jobs[job.id]
        if self.structure == "independent":
            eps = self._eps((job.id, mode), self.bias, self.sigma)
            rel = self.sigma * math.sqrt(2) if mode != "NORMAL" else 0.0
        else:
            eps = self._eps((job.id,), self.bias, self.sigma)
            if mode != "NORMAL":
                eps += self._eps((job.id, mode), 0.0, self.sigma_rel)
            rel = self.sigma_rel if mode != "NORMAL" else 0.0
        true_med = sj.base_s * self.m.slowdown(job.threads, mode)
        q50 = true_med * math.exp(eps)
        tot = math.sqrt(self.sigma ** 2 + (self.sigma_rel ** 2 if mode != "NORMAL" else 0) + self.m.runtime_noise ** 2)
        qhi = q50 * math.exp(self.z * tot)
        return Prediction(q50, qhi, q50 * self.m.busy(job.threads, mode),
                          self.m.energy_wh(q50, job.threads, mode), self.name, rel_sigma=rel)


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

# Tight deadlines: relative deadline = factor x NORMAL runtime, factor ~ U(lo, hi).
# Slowing down (ECO is ~1.35x slower) is tempting but often infeasible, which is
# exactly where a median-based feasibility check gets it wrong.
TIGHT_TEMPLATES = [
    # name, base runtime (min), threads, (factor lo, hi), deferrable, weight
    ("render", 12, 8, (1.25, 2.2), False, 3),
    ("compile", 6, 8, (1.25, 2.0), False, 3),
    ("export", 20, 4, (1.3, 2.4), False, 2),
    ("sync", 10, 2, (1.4, 3.0), True, 1),
]


def generate_jobs(days: int, jobs_per_day: int, machine: SimMachine, seed: int = 0,
                  workload: str = "mixed") -> dict[str, SimJob]:
    rng = np.random.default_rng(seed)
    T = TEMPLATES if workload == "mixed" else TIGHT_TEMPLATES
    weights = np.array([t[5] for t in T], dtype=float)
    weights /= weights.sum()
    out = {}
    for d in range(days):
        for k in range(jobs_per_day):
            name, base_min, thr, dl, defer, _ = T[rng.choice(len(T), p=weights)]
            arrival = d * 24 * HOUR + rng.uniform(8, 22) * HOUR      # submitted during waking hours
            base = base_min * 60 * rng.lognormal(0, 0.3)
            thr = min(thr, machine.logical_cpus)
            rel = max(dl * HOUR, base * 1.6) if workload == "mixed" else base * rng.uniform(*dl)
            j = Job(f"d{d}-{k}-{name}", "hash", units=1.0, threads=thr, arrival_s=arrival,
                    deadline_s=arrival + rel, deferrable=defer)
            out[j.id] = SimJob(j, base, float(rng.lognormal(0, machine.runtime_noise)))
    return out


# ----------------------------------------------------------------- simulation
WINDOW_START = {"test": "2020-07-01T00:00:00", "tune": "2020-01-15T00:00:00"}


@dataclass
class SimScenario:
    trace: str
    days: int = 14
    jobs_per_day: int = 10
    on_ac: bool = True
    soc: float | None = None             # battery scenario state of charge (0..1)
    ledger_intensity: float | None = None
    forecast_noise: float = 0.0
    forecast_kind: str = "oracle"        # oracle | seasonal | persistence
    forecast_shift_h: float = 0.0        # misleading forecast: phase error in hours
    outage_frac: float = 0.0             # fraction of time with no fresh signal
    pred_sigma: dict = field(default_factory=lambda: {"ml": 0.145, "table": 0.257})
    pred_sigma_rel: dict = field(default_factory=lambda: {"ml": 0.133, "table": 0.164})
    pred_bias: dict = field(default_factory=lambda: {"ml": 0.0, "table": 0.0})
    pred_structure: str = "shared"
    workload: str = "mixed"              # mixed | tight
    escalate: bool | None = None         # None -> config value
    split: str = "test"                  # test: windows Jul-Dec, tune: Jan-Jun
    start: str | None = None             # explicit window start (overrides split/seed)
    warmup_days: int = 14
    seed: int = 0

    def window_start(self) -> str:
        if self.start:
            return self.start
        base = datetime.fromisoformat(WINDOW_START[self.split])
        return (base + timedelta(days=14 * (self.seed % 10))).isoformat()


def _outages(days: int, frac: float, rng) -> list[tuple[float, float]]:
    if frac <= 0:
        return []
    out, total = [], days * 24 * HOUR
    n = max(1, int(days * 2))
    length = frac * total / n
    for s in rng.uniform(0, total - length, n):
        out.append((float(s), float(s + length)))
    return out


def _execute(sj: SimJob, dec, pol, m: SimMachine, cfg: Config, sc: SimScenario, src, ledger_int: float) -> dict:
    j = sj.job
    start, mode = max(dec.start_at, j.arrival_s), dec.mode
    true_rt = sj.base_s * m.slowdown(j.threads, mode) * sj.noise
    escalate = cfg.scheduler.escalate if sc.escalate is None else sc.escalate
    margin = cfg.scheduler.safety_margin_s
    escalated, end, e_wh = False, start + true_rt, None
    p = dec.prediction
    if escalate and mode != "NORMAL" and p is not None and pol.predictor is not None:
        tt = start
        while tt < end:          # same rule as the engine, checked every 60 s
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
    inten = src.mean_true(start, end, n=max(4, int((end - start) / 900))) if sc.on_ac else ledger_int
    carbon = attributable_carbon_g(e_wh, sc.on_ac, inten, ledger_int, cfg.energy.adapter_efficiency)
    return {"policy": pol.name, "job": j.id, "deferrable": j.deferrable, "threads": j.threads,
            "mode": mode, "start_s": start, "end_s": end, "arrival_s": j.arrival_s,
            "deadline_s": j.deadline_s, "deadline_met": end <= j.deadline_s + 1e-6,
            "delay_s": start - j.arrival_s, "runtime_s": end - start,
            "slowdown": (end - j.arrival_s) / (sj.base_s * sj.noise), "energy_wh": e_wh,
            "carbon_g": carbon, "escalated": escalated, "decide_ms": dec.decide_ms,
            "reason": dec.reason, "seed": sc.seed, "trace": sc.trace, "on_ac": sc.on_ac, "soc": sc.soc,
            "window_start": sc.window_start()}


def simulate(policy_name: str, cfg: Config, sc: SimScenario, machine: SimMachine | None = None) -> pd.DataFrame:
    m = machine or SimMachine()
    rng = np.random.default_rng(sc.seed + 1000)
    src = CSVReplaySource(cfg.resolve(sc.trace), sc.window_start(), 3600.0, sc.forecast_noise,
                          _outages(sc.days, sc.outage_frac, rng), seed=sc.seed,
                          forecast_kind=sc.forecast_kind, forecast_shift_h=sc.forecast_shift_h)
    jobs = generate_jobs(sc.days, sc.jobs_per_day, m, sc.seed, sc.workload)
    q = cfg.scheduler.deadline_quantile
    ml = SimPredictor(m, jobs, sc.pred_sigma["ml"], sc.pred_bias["ml"], q, sc.seed, "ml",
                      sc.pred_sigma_rel["ml"], sc.pred_structure)
    tb = SimPredictor(m, jobs, sc.pred_sigma["table"], sc.pred_bias["table"], q, sc.seed + 1, "table",
                      sc.pred_sigma_rel["table"], sc.pred_structure)
    pol = build_policy(policy_name, cfg, ml, tb)
    spec = getattr(pol, "spec", None)
    # prior mean / spread from the 60 days *before* the window (no look-ahead)
    hist = [src.true_value(-k * HOUR) for k in range(1, 60 * 24)]
    prior0, spread = float(np.mean(hist)), float(max(np.std(hist), 0.05 * np.mean(hist)))
    sig = CarbonSignal(src, prior0, cfg.carbon.freshness_half_life_s, cfg.carbon.forecast_half_life_h,
                       spread=spread, uncertainty_aware=getattr(spec, "uncertainty", True),
                       adaptive=getattr(spec, "adaptive", True))
    for k in range(sc.warmup_days * 24, 0, -1):     # warm-up: evidence from the week before
        sig.tick(-k * HOUR)
    ledger_int = sc.ledger_intensity or prior0 / cfg.energy.charge_efficiency
    mctx = Ctx(m.logical_cpus, m.physical_cores, 0.0, sc.on_ac)
    modes = [x for x in m.modes if x in cfg.scheduler.allow_modes]
    ctx = lambda t: PolicyContext(t, sc.on_ac, sc.soc, ledger_int, sig, mctx, modes)

    arrivals = sorted(jobs.values(), key=lambda s: s.job.arrival_s)
    planned: dict[str, tuple[SimJob, object]] = {}
    rows, i = [], 0
    horizon = sc.days * 24 + 72
    for h in range(horizon):
        H = h * HOUR
        sig.tick(H)
        # replan deferred jobs at the hour boundary (commitment rule: never later)
        for jid, (sj, dec) in list(planned.items()):
            if dec.start_at > H:
                nd = pol.decide(sj.job, ctx(H))
                if nd.start_at <= dec.start_at:
                    planned[jid] = (sj, nd)
        # admit arrivals during this hour
        while i < len(arrivals) and arrivals[i].job.arrival_s < H + HOUR:
            sj = arrivals[i]; i += 1
            planned[sj.job.id] = (sj, pol.decide(sj.job, ctx(sj.job.arrival_s)))
        # execute jobs that start before the next boundary
        for jid, (sj, dec) in list(planned.items()):
            if dec.start_at < H + HOUR:
                rows.append(_execute(sj, dec, pol, m, cfg, sc, src, ledger_int))
                del planned[jid]
        if i >= len(arrivals) and not planned:
            break
    df = pd.DataFrame(rows)
    df["policy"] = policy_name
    return df


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
    """Set core counts, ECO slowdown, and ECO power ratio from measured laptop data."""
    from pathlib import Path
    import json

    # 1. Hardware topology: prefer capabilities.json or latest rows in df
    caps_path = Path("results/capabilities.json")
    if caps_path.exists():
        try:
            caps = json.loads(caps_path.read_text()).get("capabilities", {})
            if "logical_cpus" in caps:
                machine.logical_cpus = int(caps["logical_cpus"])
            if "physical_cores" in caps:
                machine.physical_cores = int(caps["physical_cores"])
        except Exception:
            pass
    elif "logical_cpus" in df and df.logical_cpus.notna().any():
        machine.logical_cpus = int(df.logical_cpus.dropna().iloc[-1])
        if "physical_cores" in df and df.physical_cores.notna().any():
            machine.physical_cores = int(df.physical_cores.dropna().iloc[-1])

    # Filter to this machine's rows if mixed
    d = df[df.logical_cpus == machine.logical_cpus] if "logical_cpus" in df else df
    d = d[d.bg_threads == 0] if "bg_threads" in d else d

    # 2. Runtime slowdown
    piv = d.groupby(["kind", "units", "threads", "mode"]).runtime_s.median().unstack("mode")
    if {"NORMAL", "ECO"} <= set(piv.columns):
        r = (piv["ECO"] / piv["NORMAL"]).replace([np.inf, -np.inf], np.nan).dropna()
        if len(r):
            machine.eco_slowdown = float(r.median())

    # 3. Core power ratio: prefer calibrated multi-step power model if available
    pm_path = Path("results/power_model.json")
    if pm_path.exists():
        try:
            pm = json.loads(pm_path.read_text())
            if "per_core_w_normal" in pm and "per_core_w_eco" in pm and pm["per_core_w_normal"] > 0:
                machine.p_core_w = float(pm["per_core_w_normal"])
                machine.eco_power_ratio = float(pm["per_core_w_eco"] / pm["per_core_w_normal"])
                return machine
        except Exception:
            pass

    # Fallback to training data measured energy if power_model.json is absent
    if "energy_wh_measured" in d and d.energy_wh_measured.notna().sum() > 10:
        valid_e = d[d.energy_wh_measured > 0.0005]
        e = valid_e.groupby(["kind", "units", "threads", "mode"]).energy_wh_measured.median().unstack("mode")
        if {"NORMAL", "ECO"} <= set(e.columns):
            ratios = (e["ECO"] / e["NORMAL"]).replace([np.inf, -np.inf], np.nan).dropna()
            ratios = ratios[(ratios > 0.2) & (ratios < 5.0)]
            if len(ratios):
                er = float(ratios.median())
                machine.eco_power_ratio = float(np.clip(er / max(machine.eco_slowdown, 0.1), 0.5, 2.0))
    return machine

