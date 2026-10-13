"""Layer 2: constrained scheduling policies.

R-BACI (Risk-bounded Battery- And Carbon-Intensity-aware scheduling)
--------------------------------------------------------------------
For a job j submitted at time t with absolute deadline d_j, choose a start
offset o in O_j and an execution mode m in M (OS-supported modes) to

    min  J(o,m) = lc * C~(o,m) * (1 + lr * sigma(o,m)/I_prior)
                + lb * u(SoC) * B~(m)
                + ll * (o + q50(m)) / (d_j - t)
                + lo * [o > 0]

    s.t. o + q_tau(m) + delta <= d_j - t          (risk-bounded deadline)
         o = 0 and m = NORMAL   if j is interactive
         o = 0                  unless j is explicitly deferrable

  C~ : attributable carbon of the job relative to running it now in NORMAL mode
       (AC: E(m)/eta_a * I_eff(t+o .. t+o+q50); battery: E(m) * I_ledger)
  I_eff, sigma : freshness/horizon-discounted intensity and its uncertainty
  B~ : E(m)/E(NORMAL) on battery, 0 on AC;  u(SoC) = clip(0.5/SoC, 1, 10)
  q50, q_tau, E : Layer-1 predictions per mode.
If no (o, m) is feasible the job runs now in NORMAL mode (safe fallback).
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

import numpy as np

from .config import Config, Weights
from .jobs import Decision, Job
from .model import Ctx, Predictor


@dataclass
class PolicyContext:
    t: float
    on_ac: bool
    soc: float | None              # 0..1, None if no battery
    ledger_intensity: float
    signal: object                 # CarbonSignal
    model_ctx: Ctx
    modes: list[str]


@dataclass
class PolicySpec:
    name: str
    use_carbon: bool = True
    use_battery: bool = True
    allow_defer: bool = True
    modes: list[str] | None = None   # None -> all supported
    quantile_constraint: bool = True # False -> use q50 for deadline check
    predictor: str = "ml"            # ml | table
    uncertainty: bool = True         # use carbon-signal confidence and risk
    adaptive: bool = True            # confidence learned from observed forecast errors
    mode_gate: bool = True           # leave NORMAL only if the gain beats prediction error
    weights: Weights = field(default_factory=Weights)


class Policy:
    name = "base"
    predictor: Predictor | None = None

    def decide(self, job: Job, ctx: PolicyContext) -> Decision:  # pragma: no cover
        raise NotImplementedError


class DefaultOSPolicy(Policy):
    """Baseline 1: unmodified Windows scheduling - start immediately, no controls."""
    name = "default"

    def decide(self, job, ctx):
        return Decision(job.id, ctx.t, "NORMAL", "os-default")


class HeuristicPolicy(Policy):
    """Baseline 2: conventional rule (like a power-saver): background jobs get ECO
    when on battery or SoC < 50 %; everything starts immediately."""
    name = "heuristic"

    def decide(self, job, ctx):
        low = (not ctx.on_ac) or (ctx.soc is not None and ctx.soc < 0.5)
        if not job.interactive and low and "ECO" in ctx.modes:
            return Decision(job.id, ctx.t, "ECO", "battery-rule")
        return Decision(job.id, ctx.t, "NORMAL", "rule-default")


class RBACIPolicy(Policy):
    """Baselines 3-5 and GreenThread are all instances of R-BACI with switches."""

    def __init__(self, spec: PolicySpec, predictor: Predictor, cfg: Config):
        self.spec, self.predictor, self.cfg = spec, predictor, cfg
        self.name = spec.name

    def _robust_factor(self, mode: str, ratio: float, sigma: float, kappa: float) -> float:
        """Multiplier turning the predicted energy ratio E_m/E_NORMAL into the value
        the decision uses (robust mode selection).

        1. Empirical-Bayes shrinkage: the job's predicted log-ratio (error sd sigma) is
           pulled toward the mode's population effect mu (spread tau across jobs):
              lr_post = (tau^2 * lr + sigma^2 * mu) / (tau^2 + sigma^2)
           An uncertain prediction therefore falls back to "what this mode usually
           does", not to "do nothing".
        2. Pessimism: add kappa * posterior sd, so a mode is chosen only when its
           advantage is larger than what is still unknown (winner's-curse guard).
        """
        lr = math.log(max(ratio, 1e-9))
        mu, tau = getattr(self.predictor, "mode_prior", {}).get(mode, (lr, 0.0))
        if sigma <= 0:
            return 1.0
        if tau <= 0:  # no population information: plain pessimism
            post, post_sd = lr, sigma
        else:
            w = tau ** 2 / (tau ** 2 + sigma ** 2)
            post = w * lr + (1 - w) * mu
            post_sd = math.sqrt(tau ** 2 * sigma ** 2 / (tau ** 2 + sigma ** 2))
        return math.exp(post + kappa * post_sd - lr)

    def _feasible(self, off, pred, slack):
        need = pred.qhi if self.spec.quantile_constraint else pred.q50
        return off + need + self.cfg.scheduler.safety_margin_s <= slack

    def decide(self, job: Job, ctx: PolicyContext) -> Decision:
        t0 = time.perf_counter()
        sc, w = self.cfg.scheduler, self.spec.weights
        if job.interactive:
            return Decision(job.id, ctx.t, "NORMAL", "interactive", decide_ms=0.0)
        modes = [m for m in (self.spec.modes or ctx.modes) if m in ctx.modes] or ["NORMAL"]
        if "NORMAL" not in modes:
            modes = ["NORMAL"] + modes
        if hasattr(self.predictor, "predict_modes"):
            preds = self.predictor.predict_modes(job, modes, ctx.model_ctx)
        else:
            preds = {m: self.predictor.predict(job, m, ctx.model_ctx) for m in modes}
        slack = job.deadline_s - ctx.t
        ref = preds["NORMAL"]
        e_ref = max(ref.energy_wh, 1e-9)

        # candidate start offsets
        defer_ok = self.spec.allow_defer and job.deferrable and self.spec.use_carbon and ctx.on_ac
        offsets = [0.0]
        if defer_ok:
            min_need = min(p.q50 for p in preds.values())
            horizon = max(0.0, slack - min_need - sc.safety_margin_s)
            step = max(sc.defer_step_s, horizon / max(1, sc.max_defer_candidates))
            offsets += list(np.arange(step, horizon + 1e-9, step))

        # carbon curve on a common grid (one forecast call)
        if ctx.on_ac and self.spec.use_carbon:
            max_dur = max(p.q50 for p in preds.values())
            n_grid = 64
            grid = np.linspace(0, offsets[-1] + max_dur, n_grid) if offsets[-1] + max_dur > 0 else np.array([0.0])
            eff, sig = ctx.signal.plan_curve(ctx.t, list(grid))
            if not self.spec.uncertainty:
                sig = np.zeros_like(sig)
            prior = ctx.signal.prior

            # cumulative integrals -> O(1) window means (vectorised over offsets)
            if len(grid) > 1:
                dg = np.diff(grid)
                F_eff = np.concatenate([[0.0], np.cumsum((eff[1:] + eff[:-1]) / 2 * dg)])
                F_sig = np.concatenate([[0.0], np.cumsum((sig[1:] + sig[:-1]) / 2 * dg)])

            def window(o: np.ndarray, d: float):
                if len(grid) == 1:
                    return np.full_like(o, eff[0]), np.full_like(o, sig[0])
                d = max(d, 1e-6)
                a, b = np.clip(o, grid[0], grid[-1]), np.clip(o + d, grid[0], grid[-1])
                span = np.maximum(b - a, 1e-9)
                e = (np.interp(b, grid, F_eff) - np.interp(a, grid, F_eff)) / span
                s = (np.interp(b, grid, F_sig) - np.interp(a, grid, F_sig)) / span
                deg = (b - a) < 1e-6        # zero-length window: point value
                e[deg] = np.interp(a[deg], grid, eff)
                s[deg] = np.interp(a[deg], grid, sig)
                return e, s
            i_now = float(window(np.array([0.0]), ref.q50)[0][0])
        else:
            prior = ctx.signal.prior if ctx.signal else 1.0
            window = None
            i_now = prior

        u = 1.0
        if not ctx.on_ac and ctx.soc is not None:
            u = float(np.clip(0.5 / max(ctx.soc, 0.05), 1.0, 10.0))

        O = np.asarray(offsets, dtype=float)
        best, n_eval, any_feasible = None, 0, False
        for m in modes:
            p = preds[m]
            n_eval += len(O)
            need = p.qhi if self.spec.quantile_constraint else p.q50
            feas = O + need + sc.safety_margin_s <= slack
            if not feas.any():
                continue
            any_feasible = True
            if window is not None:
                i_m, s_m = window(O, p.q50)
                c_rel = (p.energy_wh * i_m) / (e_ref * max(i_now, 1e-9))
                risk = s_m / max(prior, 1e-9)
            else:
                c_rel = np.full_like(O, p.energy_wh / e_ref)   # battery path: ledger intensity cancels
                risk = np.zeros_like(O)
            # mode gate: judge a non-NORMAL mode by its pessimistic energy ratio
            # r * exp(kappa * rel_sigma), so a mode is only picked when its predicted
            # saving is larger than the uncertainty of that prediction (counters the
            # winner's curse of choosing the minimum of several noisy estimates)
            gate = 1.0
            if self.spec.mode_gate and m != "NORMAL":
                gate = self._robust_factor(m, p.energy_wh / e_ref, getattr(p, "rel_sigma", 0.0), sc.mode_gate_kappa)
                c_rel = c_rel * gate
            b_rel = 0.0 if ctx.on_ac else p.energy_wh / e_ref * gate
            c_carbon = (w.carbon * c_rel * (1 + w.risk * risk)) if self.spec.use_carbon else np.zeros_like(O)
            c_batt = (w.battery * u * b_rel) if self.spec.use_battery else 0.0
            c_lat = w.latency * (O + p.q50) / max(slack, 1e-6)
            c_ovh = np.where(O > 0, w.defer_overhead, 0.0)
            total = c_carbon + c_batt + c_lat + c_ovh
            total = np.where(feas, total, np.inf)
            k = int(np.argmin(total))
            if best is None or total[k] < best[0] - 1e-12:
                cost = {"carbon": float(c_carbon[k]), "battery": float(c_batt), "latency": float(c_lat[k]),
                        "overhead": float(c_ovh[k])}
                best = (float(total[k]), float(O[k]), m, cost, p)
        dt = (time.perf_counter() - t0) * 1000
        if best is None:
            return Decision(job.id, ctx.t, "NORMAL", "infeasible->fallback", ref, {}, n_eval, dt)
        total, o, m, cost, p = best
        reason = "optimal" if any_feasible else "fallback"
        if o > 0:
            reason += f":defer {o:.0f}s"
        return Decision(job.id, ctx.t + float(o), m, reason, p, dict(cost, total=total), n_eval, dt)


def make_specs(cfg: Config) -> dict[str, PolicySpec]:
    W = cfg.weights
    nai = dict(uncertainty=False, adaptive=False, mode_gate=False)
    return {
        "carbon_only": PolicySpec("carbon_only", use_battery=False, modes=["NORMAL"], predictor="table", weights=W, **nai),
        "battery_only": PolicySpec("battery_only", use_carbon=False, allow_defer=False, predictor="table", weights=W, **nai),
        "combined_noai": PolicySpec("combined_noai", predictor="table", weights=W, **nai),
        "greenthread": PolicySpec("greenthread", predictor="ml", weights=W),
        # ablations of GreenThread (one component removed or replaced each)
        "gt_no_uncertainty": PolicySpec("gt_no_uncertainty", predictor="ml", uncertainty=False, adaptive=False, weights=W),
        "gt_fixed_conf": PolicySpec("gt_fixed_conf", predictor="ml", adaptive=False, weights=W),
        "gt_no_gate": PolicySpec("gt_no_gate", predictor="ml", mode_gate=False, weights=W),
        "gt_no_quantile": PolicySpec("gt_no_quantile", predictor="ml", quantile_constraint=False, weights=W),
        "gt_no_ml": PolicySpec("gt_no_ml", predictor="table", weights=W),
        "gt_no_defer": PolicySpec("gt_no_defer", predictor="ml", allow_defer=False, weights=W),
        "gt_no_modes": PolicySpec("gt_no_modes", predictor="ml", modes=["NORMAL"], weights=W),
        "gt_no_carbon": PolicySpec("gt_no_carbon", predictor="ml", use_carbon=False, weights=W),
        "gt_no_battery": PolicySpec("gt_no_battery", predictor="ml", use_battery=False, weights=W),
        # version 1 of GreenThread (fixed-decay confidence, no gate) for before/after comparison
        "greenthread_v1": PolicySpec("greenthread_v1", predictor="ml", adaptive=False, mode_gate=False, weights=W),
    }


BASELINES = ["default", "heuristic", "carbon_only", "battery_only", "combined_noai", "greenthread"]
ABLATIONS = ["greenthread", "greenthread_v1", "gt_no_uncertainty", "gt_fixed_conf", "gt_no_gate", "gt_no_quantile",
             "gt_no_ml", "gt_no_defer", "gt_no_modes", "gt_no_carbon", "gt_no_battery"]


def build_policy(name: str, cfg: Config, ml: Predictor | None, table: Predictor | None) -> Policy:
    if name == "default":
        return DefaultOSPolicy()
    if name == "heuristic":
        return HeuristicPolicy()
    specs = make_specs(cfg)
    if name not in specs:
        raise ValueError(f"unknown policy {name}; choose from {['default', 'heuristic', *specs]}")
    spec = specs[name]
    pred = ml if spec.predictor == "ml" else table
    if pred is None:
        raise RuntimeError(f"policy {name} needs a '{spec.predictor}' predictor (run `greenthread train`)")
    return RBACIPolicy(spec, pred, cfg)
