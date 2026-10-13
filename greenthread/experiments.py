"""Experiment drivers: real-machine comparisons and the simulation suite."""
from __future__ import annotations

import copy
import json
import random
import time
from pathlib import Path

import numpy as np
import pandas as pd

from .config import Config
from .simulator import SimMachine, SimScenario, calibrate_from_csv, ci95, simulate, summarise
from .policy import ABLATIONS, BASELINES


# ----------------------------------------------------------------- real runs
def run_real_experiment(cfg: Config, a) -> pd.DataFrame:
    from .cli import _predictors, _runtime
    from .jobs import load_jobs
    from .jobs import resolve_relative_deadlines
    jobs = load_jobs(a.jobs)
    policies = [p.strip() for p in a.policies.split(",") if p.strip()]
    if getattr(a, "no_escalate", False):
        cfg.scheduler.escalate = False
    ml, tb = _predictors(cfg, required=any(p not in ("default", "heuristic") for p in policies)
                         or any(j.deadline_factor for j in jobs))
    from .model import Ctx
    caps0 = __import__("greenthread.backends", fromlist=["get_backend"]).get_backend(a.backend).capabilities()
    jobs = resolve_relative_deadlines(jobs, ml, Ctx(caps0.logical_cpus, caps0.physical_cores, 0.0, True))
    for j in jobs:
        if j.deadline_factor:
            print(f"  {j.id}: deadline {j.deadline_s - j.arrival_s:.2f} s after arrival ({j.deadline_factor} x predicted)")
    out_dir = cfg.resolve(cfg.results_dir) / f"experiment_{a.tag}"
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    rng = random.Random(0)
    for rep in range(a.reps):
        order = policies[:]
        rng.shuffle(order)            # randomise policy order per repetition (thermal drift)
        for pol in order:
            print(f"\n=== rep {rep+1}/{a.reps} policy={pol} ===", flush=True)
            eng, sampler = _runtime(cfg, a, pol, ml, tb, log_name=f"experiment_{a.tag}/decisions_{pol}_r{rep}.jsonl")
            t0 = time.perf_counter()
            res = eng.run(copy.deepcopy(jobs))
            if sampler:
                sampler.stop()
            for r in res:
                d = r.to_dict()
                d.update(rep=rep, wall_total_s=time.perf_counter() - t0)
                rows.append(d)
            pd.DataFrame(rows).to_csv(out_dir / "results.csv", index=False)
            time.sleep(a.cooldown)
    df = pd.DataFrame(rows)
    summary = summarise_real(df)
    summary.to_csv(out_dir / "summary.csv", index=False)
    (out_dir / "summary.md").write_text(summary_markdown(summary, df))
    print("\n" + summary.to_string(index=False))
    return df


def summarise_real(df: pd.DataFrame) -> pd.DataFrame:
    batch = df[~df.interactive]
    per = batch.groupby(["policy", "rep"]).agg(carbon_g=("carbon_g", "sum"), energy_wh=("energy_wh", "sum"),
                                                misses=("deadline_met", lambda s: int((~s).sum())),
                                                miss_rate=("deadline_met", lambda s: float((~s).mean())),
                                                jobs=("job_id", "count"), mean_delay_s=("deferred_s", "mean"),
                                                mean_runtime_s=("runtime_s", "mean"),
                                                escalations=("escalated", "sum"),
                                                decide_ms=("decide_ms", "mean")).reset_index()
    probe = df[df.interactive & df.latency.astype(str).ne("{}")]
    if len(probe):
        lat = probe.assign(p95=probe.latency.apply(lambda d: (d if isinstance(d, dict) else eval(d)).get("p95_ms")))
        per = per.merge(lat.groupby(["policy", "rep"]).p95.mean().rename("probe_p95_ms").reset_index(),
                        on=["policy", "rep"], how="left")
    rows = []
    base = per[per.policy == "default"].set_index("rep")
    for pol, g in per.groupby("policy"):
        r = {"policy": pol, "reps": len(g)}
        for col in [c for c in g.columns if c not in ("policy", "rep")]:
            m, lo, hi = ci95(g[col])
            r[col] = m
            r[col + "_ci95"] = (hi - lo) / 2
        if len(base):
            sv = [100 * (1 - row.carbon_g / base.loc[row.rep, "carbon_g"]) for row in g.itertuples() if row.rep in base.index]
            ev = [100 * (1 - row.energy_wh / base.loc[row.rep, "energy_wh"]) for row in g.itertuples() if row.rep in base.index]
            if sv:
                r["carbon_saving_pct"], lo, hi = ci95(pd.Series(sv)); r["carbon_saving_pct_ci95"] = (hi - lo) / 2
                r["energy_saving_pct"], lo, hi = ci95(pd.Series(ev)); r["energy_saving_pct_ci95"] = (hi - lo) / 2
        rows.append(r)
    return pd.DataFrame(rows)


def summary_markdown(s: pd.DataFrame, df: pd.DataFrame) -> str:
    src = ", ".join(sorted(df.energy_source.unique()))
    lines = [f"Energy source(s): {src}. Reps per policy: {int(s.reps.max())}.", "",
             "| policy | carbon (g) | carbon saving % | energy (mWh) | energy saving % | deadline misses per run | "
             "mean delay (s) | escalations | interactive p95 (ms) |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in s.itertuples():
        g = lambda k: getattr(r, k, float("nan"))
        lines.append(f"| {r.policy} | {r.carbon_g:.4f} ± {r.carbon_g_ci95:.4f} | {g('carbon_saving_pct'):.1f} ± "
                     f"{g('carbon_saving_pct_ci95'):.1f} | {r.energy_wh*1000:.2f} | {g('energy_saving_pct'):.1f} ± "
                     f"{g('energy_saving_pct_ci95'):.1f} | {r.misses:.2f} ± {r.misses_ci95:.2f} | {r.mean_delay_s:.1f} | "
                     f"{r.escalations:.2f} | {g('probe_p95_ms'):.2f} |")
    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------- simulation
def _pred_sigma_from_metrics(path: str | None) -> dict:
    """Predictor error levels for the simulator, from measured held-out metrics.

    sigma      : model error of absolute runtime, run-to-run noise removed
    sigma_rel  : model error of the predicted mode effect (cpu ratio), noise removed
    noise      : run-to-run runtime noise (becomes SimMachine.runtime_noise)
    """
    d = {"sigma": {"ml": 0.097, "table": 0.233}, "sigma_rel": {"ml": 0.048, "table": 0.108},
         "bias": {"ml": 0.0, "table": 0.0}, "noise": 0.108, "source": "defaults (dev-machine metrics)"}
    if not path or not Path(path).exists():
        return d
    m = json.loads(Path(path).read_text())["temporal_split"]
    mc = m.get("mode_comparison", {}).get("cpu_ratio_model_error_noise_removed", {})
    d["sigma"] = {"ml": m["ml"].get("model_log_err_sd", m["ml"]["log_err_sd"]),
                  "table": m["table"].get("model_log_err_sd", m["table"]["log_err_sd"])}
    d["sigma_rel"] = {"ml": max(0.02, mc.get("ml_direct_difference", 0.05)), "table": max(0.02, mc.get("table", 0.11))}
    d["bias"] = {"ml": m["ml"]["log_err_bias"], "table": m["table"]["log_err_bias"]}
    d["noise"] = m.get("label_noise", {}).get("runtime_log_sd", 0.108)
    d["source"] = str(path)
    return d


def _sim_job(args):
    policy, cfg, sc, machine = args
    return simulate(policy, cfg, sc, machine)


def run_simulation_suite(cfg: Config, a) -> dict:
    from multiprocessing import get_context
    out = cfg.resolve(cfg.results_dir) / "simulation"
    out.mkdir(parents=True, exist_ok=True)
    traces = [f"data/traces/{t}.csv" for t in a.traces.split(",")]
    seeds = list(range(a.seeds))
    P = _pred_sigma_from_metrics(a.metrics)
    machine = SimMachine(runtime_noise=P["noise"])
    calib_csv = cfg.resolve(cfg.results_dir) / "training_data.csv"
    if getattr(a, "calibrate", False) and calib_csv.exists():
        machine = calibrate_from_csv(pd.read_csv(calib_csv), machine)
    meta = {"machine": dict(machine.__dict__), "predictor_errors": P, "days": a.days, "seeds": a.seeds,
            "windows": "test split: 14-day windows starting 2020-07-01 + 14*seed days (Jul-Dec); "
                       "tuning split (kappa sweep only): windows from 2020-01-15 (Jan-Jun)",
            "note": "Simulation: energy from SimMachine model (assumptions unless --calibrate with "
                    "measured data), carbon from real 2020 traces (DE/GB/FR/US-CAL; ENTSO-E/CAISO via "
                    "Let's Wait Awhile) and one SYNTHETIC India trace."}
    base_kw = {"pred_sigma": P["sigma"], "pred_sigma_rel": P["sigma_rel"], "pred_bias": P["bias"]}
    workers = max(1, min(4, (__import__("os").cpu_count() or 1)))
    pool = get_context("spawn").Pool(workers) if workers > 1 else None

    def run(policies, scen_kw, tag, trace_list=None, seed_list=None, mach=None):
        tasks = []
        for tr in (trace_list or traces):
            for sd in (seed_list if seed_list is not None else seeds):
                kw = {**base_kw, **scen_kw}
                sc = SimScenario(trace=tr, days=a.days, seed=sd, **kw)
                tasks += [(p, cfg, sc, mach or machine) for p in policies]
        frames = pool.map(_sim_job, tasks, chunksize=2) if pool else [_sim_job(t) for t in tasks]
        df = pd.concat(frames, ignore_index=True)
        df.to_csv(out / f"{tag}_jobs.csv.gz", index=False)
        print(f"  {tag}: {len(tasks)} runs", flush=True)
        return df

    def stress(policies, grid, tag, keys, **kw):
        frames = []
        for vals in grid:
            sk = dict(zip(keys, vals))
            scen = {**kw.get("fixed", {}), **{k: v for k, v in sk.items() if not k.startswith("_")}}
            if "_sigma_rel_ml" in sk:
                scen["pred_sigma_rel"] = {"ml": sk["_sigma_rel_ml"], "table": P["sigma_rel"]["table"]}
            d = run(policies, scen,
                    f"{tag}_" + "_".join(f"{k}{v}" for k, v in sk.items()), kw.get("trace_list"),
                    kw.get("seed_list"))
            for k, v in sk.items():
                d[k.lstrip("_")] = v
            frames.append(d)
        return _stress_table(pd.concat(frames), [k.lstrip("_") for k in keys])

    t_start = time.time()
    R = {}
    print("E1 baselines (AC)", flush=True)
    R["e1"] = _table(run(BASELINES, {}, "e1_baselines_ac"), by_trace=True)
    print("E2 ablations (AC, forecast noise 0.15)", flush=True)
    R["e2"] = _table(run(ABLATIONS + ["default"], {"forecast_noise": 0.15}, "e2_ablations_ac"), by_trace=False)
    print("E3 carbon-signal quality", flush=True)
    sig_pols = ["default", "combined_noai", "gt_no_uncertainty", "gt_fixed_conf", "greenthread"]
    cases = [("oracle", 0.0, 0.0, 0.0), ("oracle", 0.15, 0.0, 0.0), ("oracle", 0.3, 0.0, 0.0),
             ("oracle", 0.5, 0.0, 0.0), ("oracle", 0.15, 0.3, 0.0), ("seasonal", 0.0, 0.0, 0.0),
             ("persistence", 0.0, 0.0, 0.0), ("oracle", 0.0, 0.0, 12.0), ("oracle", 0.15, 0.0, 6.0)]
    if a.quick:
        cases = [cases[0], cases[2], cases[5], cases[7]]
    R["e3"] = stress(sig_pols, cases, "e3", ["forecast_kind", "forecast_noise", "outage_frac", "forecast_shift_h"])
    print("E4 battery state of charge", flush=True)
    bat_pols = ["default", "heuristic", "battery_only", "combined_noai", "greenthread_v1", "gt_no_gate", "greenthread"]
    R["e4"] = stress(bat_pols, [(0.8,), (0.3,), (0.1,)], "e4", ["soc"], fixed={"on_ac": False})
    print("E5 tight deadlines", flush=True)
    tight_pols = ["default", "heuristic", "gt_no_quantile", "greenthread"]
    R["e5"] = stress(tight_pols, [(True, True, None), (True, False, None), (False, True, 0.3), (False, False, 0.3)],
                     "e5", ["on_ac", "escalate", "soc"], fixed={"workload": "tight"})
    print("E6 mode-gate sensitivity (battery, SoC 0.3)", flush=True)
    R["e6"] = stress(["default", "heuristic", "gt_no_gate", "greenthread"],
                     [(v,) for v in ([0.02, 0.05, 0.1, 0.2, 0.3] if not a.quick else [0.05, 0.3])], "e6",
                     ["_sigma_rel_ml"], fixed={"on_ac": False, "soc": 0.3})
    print("E7 version-1 error structure (independent per-mode errors), battery", flush=True)
    R["e7"] = stress(["default", "heuristic", "greenthread_v1", "greenthread"],
                     [("independent",), ("shared",)], "e7", ["pred_structure"],
                     fixed={"on_ac": False, "soc": 0.3, "pred_sigma": {"ml": 0.145, "table": 0.257}})
    print("Tuning: kappa sweep on Jan-Jun windows", flush=True)
    R["tune"] = _kappa_sweep(cfg, a, machine, base_kw, traces, seeds, out)
    if pool:
        pool.close()
    meta["runtime_s"] = time.time() - t_start
    (out / "meta.json").write_text(json.dumps(meta, indent=2, default=str))
    for k, v in R.items():
        v.to_csv(out / f"{k}_summary.csv", index=False)
    _plots(out, R)
    (out / "SIMULATION_RESULTS.md").write_text(_sim_markdown(R, meta))
    print((out / "SIMULATION_RESULTS.md").read_text())
    return R


def _kappa_sweep(cfg, a, machine, base_kw, traces, seeds, out) -> pd.DataFrame:
    import copy
    rows = []
    for kappa in ([0.0, 0.5, 1.0, 2.0] if not a.quick else [0.0, 1.0]):
        c2 = copy.deepcopy(cfg)
        c2.scheduler.mode_gate_kappa = kappa
        frames = []
        for tr in traces[:2]:
            for sd in seeds[: max(2, len(seeds) // 2)]:
                for on_ac, soc in ((True, None), (False, 0.3)):
                    sc = SimScenario(trace=tr, days=a.days, seed=sd, split="tune", on_ac=on_ac, soc=soc,
                                     forecast_noise=0.15, **base_kw)
                    for p in ("default", "greenthread"):
                        d = simulate(p, c2, sc, machine)
                        d["seed"] = f"{sd}|{tr}|{on_ac}"
                        frames.append(d)
        t = summarise(pd.concat(frames))
        g = t[t.policy == "greenthread"]
        rows.append({"kappa": kappa, "carbon_saving_pct": g.carbon_saving_pct.mean(),
                     "energy_saving_pct": g.energy_saving_pct.mean(), "miss_rate": g.miss_rate.mean()})
    return pd.DataFrame(rows)


def _table(df: pd.DataFrame, by_trace: bool) -> pd.DataFrame:
    rows = []
    groups = df.groupby("trace") if by_trace else [("all", df)]
    for tr, d in groups:
        s = summarise(d.assign(seed=d.seed.astype(str) + "|" + d.trace))
        for pol, g in s.groupby("policy"):
            r = {"trace": Path(tr).stem if tr != "all" else "all", "policy": pol, "n_runs": len(g)}
            for col in ("carbon_saving_pct", "energy_saving_pct", "miss_rate", "mean_delay_h", "p95_slowdown",
                        "escalations", "decide_ms"):
                m, lo, hi = ci95(g[col])
                r[col], r[col + "_ci95"] = m, (hi - lo) / 2
            rows.append(r)
    return pd.DataFrame(rows)


def _stress_table(df: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    rows = []
    df = df.copy()
    for k in keys:   # pandas >= 3 keeps None as NA under astype(str); make it a real label
        df[k] = df[k].map(lambda v: "None" if v is None or (isinstance(v, float) and v != v) else str(v))
    for kv, d in df.groupby(keys, dropna=False):
        t = _table(d, by_trace=False)
        kv = kv if isinstance(kv, tuple) else (kv,)
        for k, v in zip(keys, kv):
            t[k] = v
        rows.append(t)
    return pd.concat(rows, ignore_index=True)


def _fmt(r, c):
    return f"{r[c]:.1f} ± {r[c + '_ci95']:.1f}"


def _sim_markdown(R: dict, meta: dict) -> str:
    P = meta["predictor_errors"]
    L = ["# Simulation results (generated)", "", f"_{meta['note']}_", "",
         f"Predictor errors (noise removed): absolute {P['sigma']}, mode effect {P['sigma_rel']}; "
         f"run-to-run noise {P['noise']:.3f} (source: {P['source']}).",
         f"{meta['windows']}. Seeds per trace: {meta['seeds']}, days per window: {meta['days']}.",
         "Savings are relative to `default` (start now, OS scheduling) for the same window and jobs; ± is a 95% CI.", ""]
    L += ["## E1 Baselines on AC", "", "| trace | policy | carbon saving % | energy saving % | miss rate % | mean delay h |",
          "|---|---|---|---|---|---|"]
    for _, r in R["e1"].iterrows():
        L.append(f"| {r.trace} | {r.policy} | {_fmt(r,'carbon_saving_pct')} | {_fmt(r,'energy_saving_pct')} | "
                 f"{100*r.miss_rate:.2f} | {r.mean_delay_h:.2f} |")
    L += ["", "## E2 Ablations (all traces, AC, forecast noise 0.15)", "",
          "| policy | carbon saving % | energy saving % | miss rate % | p95 slowdown |", "|---|---|---|---|---|"]
    for _, r in R["e2"].iterrows():
        L.append(f"| {r.policy} | {_fmt(r,'carbon_saving_pct')} | {_fmt(r,'energy_saving_pct')} | "
                 f"{100*r.miss_rate:.2f} | {r.p95_slowdown:.2f} |")
    L += ["", "## E3 Carbon-signal quality (AC)", "",
          "Forecast kinds: oracle = recorded future (+noise); seasonal = same hour yesterday (what a free "
          "Electricity Maps plan supports); persistence = latest value; shift = forecast phase error in hours.", "",
          "| forecast | noise | outage | shift h | policy | carbon saving % | miss rate % |", "|---|---|---|---|---|---|---|"]
    for _, r in R["e3"].iterrows():
        L.append(f"| {r.forecast_kind} | {r.forecast_noise} | {r.outage_frac} | {r.forecast_shift_h} | {r.policy} | "
                 f"{_fmt(r,'carbon_saving_pct')} | {100*r.miss_rate:.2f} |")
    L += ["", "## E4 Battery state of charge", "", "| SoC | policy | energy saving % | miss rate % | p95 slowdown |",
          "|---|---|---|---|---|"]
    for _, r in R["e4"].iterrows():
        L.append(f"| {r.soc} | {r.policy} | {_fmt(r,'energy_saving_pct')} | {100*r.miss_rate:.2f} | {r.p95_slowdown:.2f} |")
    L += ["", "## E5 Tight deadlines (deadline = 1.25-3.0 x normal runtime)", "",
          "| power | escalation | policy | miss rate % | carbon saving % | energy saving % |", "|---|---|---|---|---|---|"]
    for _, r in R["e5"].iterrows():
        pw = "AC" if r.on_ac == "True" else f"battery {r.soc}"
        L.append(f"| {pw} | {r.escalate} | {r.policy} | {_fmt(r,'miss_rate') if False else f'{100*r.miss_rate:.2f} ± {100*r.miss_rate_ci95:.2f}'} | "
                 f"{_fmt(r,'carbon_saving_pct')} | {_fmt(r,'energy_saving_pct')} |")
    L += ["", "## E6 Mode-gate sensitivity to mode-effect error (battery, SoC 0.3)", "",
          "| ML mode-effect error | policy | energy saving % | p95 slowdown |", "|---|---|---|---|"]
    for _, r in R["e6"].iterrows():
        L.append(f"| {r.sigma_rel_ml} | {r.policy} | {_fmt(r,'energy_saving_pct')} | {r.p95_slowdown:.2f} |")
    L += ["", "## E7 Version-1 simulator assumption (independent per-mode errors, sd 0.145) vs measured structure", "",
          "| error structure | policy | energy saving % |", "|---|---|---|"]
    for _, r in R["e7"].iterrows():
        L.append(f"| {r.pred_structure} | {r.policy} | {_fmt(r,'energy_saving_pct')} |")
    L += ["", "## Tuning split: gate strength kappa (Jan-Jun windows, AC and battery pooled)", "",
          "| kappa | carbon saving % | energy saving % | miss rate % |", "|---|---|---|---|"]
    for _, r in R["tune"].iterrows():
        L.append(f"| {r.kappa} | {r.carbon_saving_pct:.2f} | {r.energy_saving_pct:.2f} | {100*r.miss_rate:.2f} |")
    return "\n".join(L) + "\n"


def _plots(out: Path, R: dict):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    e1 = R["e1"]
    fig, ax = plt.subplots(figsize=(9, 4))
    traces = list(e1.trace.unique())
    pols = [p for p in BASELINES if p != "default"]
    w = 0.8 / len(pols)
    for i, p in enumerate(pols):
        d = e1[e1.policy == p].set_index("trace").reindex(traces)
        ax.bar(np.arange(len(traces)) + i * w, d.carbon_saving_pct, w, yerr=d.carbon_saving_pct_ci95, label=p)
    ax.set_xticks(np.arange(len(traces)) + 0.4 - w / 2)
    ax.set_xticklabels(traces, fontsize=8)
    ax.set_ylabel("carbon saving vs default (%)")
    ax.axhline(0, color="k", lw=0.5)
    ax.legend(fontsize=7, ncol=3)
    ax.set_title("E1: carbon saving by policy and grid trace (simulation, Jul-Dec windows)")
    fig.tight_layout(); fig.savefig(out / "e1_carbon_saving.png", dpi=150); plt.close(fig)

    e3 = R["e3"].copy()
    e3["case"] = (e3.forecast_kind + " n" + e3.forecast_noise.astype(str) + " o" + e3.outage_frac.astype(str)
                  + " s" + e3.forecast_shift_h.astype(str))
    cases = list(dict.fromkeys(e3.case))
    pols = [p for p in e3.policy.unique() if p != "default"]
    fig, ax = plt.subplots(figsize=(11, 4))
    w = 0.8 / len(pols)
    for i, p in enumerate(pols):
        d = e3[e3.policy == p].set_index("case").reindex(cases)
        ax.bar(np.arange(len(cases)) + i * w, d.carbon_saving_pct, w, yerr=d.carbon_saving_pct_ci95, label=p)
    ax.set_xticks(np.arange(len(cases)) + 0.4 - w / 2)
    ax.set_xticklabels(cases, fontsize=7, rotation=20, ha="right")
    ax.axhline(0, color="k", lw=0.5)
    ax.set_ylabel("carbon saving vs default (%)")
    ax.legend(fontsize=7, ncol=4)
    ax.set_title("E3: carbon-signal quality (n = noise, o = outage fraction, s = phase shift h)")
    fig.tight_layout(); fig.savefig(out / "e3_signal_quality.png", dpi=150); plt.close(fig)

    e5 = R["e5"]
    fig, ax = plt.subplots(figsize=(8, 3.8))
    conds = list(dict.fromkeys(zip(e5.on_ac, e5.escalate)))
    pols = [p for p in e5.policy.unique() if p != "default"]
    w = 0.8 / len(pols)
    for i, p in enumerate(pols):
        vals = [100 * e5[(e5.policy == p) & (e5.on_ac == a_) & (e5.escalate == e_)].miss_rate.mean() for a_, e_ in conds]
        ax.bar(np.arange(len(conds)) + i * w, vals, w, label=p)
    ax.set_xticks(np.arange(len(conds)) + 0.4 - w / 2)
    ax.set_xticklabels([("AC" if a_ == "True" else "battery") + (", escalation" if e_ == "True" else ", no escalation")
                        for a_, e_ in conds], fontsize=8)
    ax.set_ylabel("deadline miss rate (%)"); ax.legend(fontsize=7)
    ax.set_title("E5: tight deadlines - quantile vs median feasibility")
    fig.tight_layout(); fig.savefig(out / "e5_tight_deadlines.png", dpi=150); plt.close(fig)

    e6 = R["e6"]
    fig, ax = plt.subplots(figsize=(5.5, 3.6))
    for p in [p for p in e6.policy.unique() if p != "default"]:
        d = e6[e6.policy == p].assign(x=lambda z: z.sigma_rel_ml.astype(float)).sort_values("x")
        ax.errorbar(d.x, d.energy_saving_pct, d.energy_saving_pct_ci95, marker="o", capsize=3, label=p)
    ax.set_xlabel("ML mode-effect error (log sd)"); ax.set_ylabel("battery energy saving (%)")
    ax.legend(fontsize=7); ax.set_title("E6: mode gate vs prediction error", fontsize=9)
    fig.tight_layout(); fig.savefig(out / "e6_mode_gate.png", dpi=150); plt.close(fig)


def rebuild_summaries(out: Path) -> dict:
    """Recompute summary tables, plots and SIMULATION_RESULTS.md from saved job files."""
    meta = json.loads((out / "meta.json").read_text())
    R = {k: pd.read_csv(out / f"{k}_summary.csv") for k in ("e1", "e2", "e3", "e4", "e6", "e7", "tune")}
    frames = []
    for f in sorted(out.glob("e5_*_jobs.csv.gz")):
        d = pd.read_csv(f)
        tag = f.name[len("e5_"):-len("_jobs.csv.gz")]
        d["on_ac"] = "on_acTrue" in tag
        d["escalate"] = "escalateTrue" in tag
        d["soc"] = None if "socNone" in tag else float(tag.split("soc")[-1])
        frames.append(d)
    R["e5"] = _stress_table(pd.concat(frames), ["on_ac", "escalate", "soc"])
    R["e5"].to_csv(out / "e5_summary.csv", index=False)
    _plots(out, R)
    (out / "SIMULATION_RESULTS.md").write_text(_sim_markdown(R, meta))
    return R
