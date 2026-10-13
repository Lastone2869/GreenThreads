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
    jobs = load_jobs(a.jobs)
    policies = [p.strip() for p in a.policies.split(",") if p.strip()]
    ml, tb = _predictors(cfg, required=any(p not in ("default", "heuristic") for p in policies))
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
    lines = [f"Energy source(s): {src}", "",
             "| policy | carbon (g) | carbon saving % | energy (mWh) | misses | mean delay (s) | escalations |",
             "|---|---|---|---|---|---|---|"]
    for r in s.itertuples():
        cs = getattr(r, "carbon_saving_pct", float("nan"))
        csc = getattr(r, "carbon_saving_pct_ci95", float("nan"))
        lines.append(f"| {r.policy} | {r.carbon_g:.4f} ± {r.carbon_g_ci95:.4f} | {cs:.1f} ± {csc:.1f} | "
                     f"{r.energy_wh*1000:.2f} | {r.misses:.2f} | {r.mean_delay_s:.1f} | {r.escalations:.2f} |")
    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------- simulation
def _pred_sigma_from_metrics(path: str | None) -> tuple[dict, dict, dict]:
    if not path or not Path(path).exists():
        return {"ml": 0.08, "table": 0.25}, {"ml": 0.0, "table": 0.0}, {"source": "default assumption"}
    m = json.loads(Path(path).read_text())["temporal_split"]
    sig = {"ml": m["ml"]["log_err_sd"], "table": m["table"]["log_err_sd"]}
    bias = {"ml": m["ml"]["log_err_bias"], "table": m["table"]["log_err_bias"]}
    return sig, bias, {"source": str(path), **sig}


def run_simulation_suite(cfg: Config, a) -> dict:
    out = cfg.resolve(cfg.results_dir) / "simulation"
    out.mkdir(parents=True, exist_ok=True)
    traces = [f"data/traces/{t}.csv" for t in a.traces.split(",")]
    seeds = list(range(a.seeds))
    sig, bias, sig_src = _pred_sigma_from_metrics(a.metrics)
    machine = SimMachine()
    calib_csv = cfg.resolve(cfg.results_dir) / "training_data.csv"
    if getattr(a, "calibrate", False) and calib_csv.exists():
        # replace the EcoQoS slowdown/power assumptions with values measured on this laptop
        machine = calibrate_from_csv(pd.read_csv(calib_csv), machine)
    meta = {"machine": machine.__dict__, "pred_sigma": sig_src, "days": a.days, "seeds": a.seeds,
            "note": "Simulation: energy from SimMachine model (assumptions), carbon from real 2020 traces "
                    "(DE/GB/FR/US-CAL; ENTSO-E/CAISO via Let's Wait Awhile) and one SYNTHETIC India trace."}

    def run(policies, scen_kw, tag):
        frames = []
        for tr in traces:
            for s in seeds:
                kw = {"pred_sigma": sig, "pred_bias": bias, **scen_kw}
                sc = SimScenario(trace=tr, days=a.days, seed=s, **kw)
                for p in policies:
                    frames.append(simulate(p, cfg, sc, machine))
        df = pd.concat(frames, ignore_index=True)
        df.to_csv(out / f"{tag}_jobs.csv.gz", index=False)
        return df

    results = {}
    t_start = time.time()
    # E1: baselines on AC, perfect-ish forecasts
    df = run(BASELINES, {}, "e1_baselines_ac")
    results["e1"] = _table(df, by_trace=True)
    # E2: ablations on AC with realistic forecast error
    df = run(ABLATIONS + ["default"], {"forecast_noise": 0.15}, "e2_ablations_ac")
    results["e2"] = _table(df, by_trace=False)
    # E3: uncertainty stress - forecast error and signal outages
    frames = []
    for fn in ([0.0, 0.3] if a.quick else [0.0, 0.15, 0.3, 0.5]):
        for of in ([0.0, 0.3] if a.quick else [0.0, 0.15, 0.3]):
            d = run(["default", "combined_noai", "gt_no_uncertainty", "greenthread"],
                    {"forecast_noise": fn, "outage_frac": of}, f"e3_fn{fn}_of{of}")
            d["forecast_noise"], d["outage_frac"] = fn, of
            frames.append(d)
    e3 = pd.concat(frames)
    results["e3"] = _stress_table(e3, ["forecast_noise", "outage_frac"])
    # E4: battery scenarios (state of charge)
    frames = []
    for soc in [0.8, 0.3, 0.1]:
        d = run(["default", "heuristic", "battery_only", "combined_noai", "greenthread"],
                {"on_ac": False, "soc": soc}, f"e4_soc{soc}")
        d["soc_level"] = soc
        frames.append(d)
    results["e4"] = _stress_table(pd.concat(frames), ["soc_level"])
    # E5: prediction-error sensitivity (ML sigma sweep, table fixed)
    frames = []
    for s_ml in [0.05, 0.15, 0.3, 0.5]:
        d = run(["default", "greenthread", "gt_no_quantile"], {"pred_sigma": {"ml": s_ml, "table": sig["table"]},
                                                              "forecast_noise": 0.15}, f"e5_sig{s_ml}")
        d["ml_sigma"] = s_ml
        frames.append(d)
    results["e5"] = _stress_table(pd.concat(frames), ["ml_sigma"])
    meta["runtime_s"] = time.time() - t_start
    (out / "meta.json").write_text(json.dumps(meta, indent=2, default=str))
    for k, v in results.items():
        v.to_csv(out / f"{k}_summary.csv", index=False)
    _plots(out, results)
    (out / "SIMULATION_RESULTS.md").write_text(_sim_markdown(results, meta))
    print((out / "SIMULATION_RESULTS.md").read_text())
    return results


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
    for kv, d in df.groupby(keys):
        t = _table(d, by_trace=False)
        kv = kv if isinstance(kv, tuple) else (kv,)
        for k, v in zip(keys, kv):
            t[k] = v
        rows.append(t)
    return pd.concat(rows, ignore_index=True)


def _fmt(r, c):
    return f"{r[c]:.1f} ± {r[c + '_ci95']:.1f}"


def _sim_markdown(res: dict, meta: dict) -> str:
    L = ["# Simulation results (generated)", "",
         f"_{meta['note']}_", "",
         f"Predictor error levels: {meta['pred_sigma']}. Seeds per trace: {meta['seeds']}, days: {meta['days']}.",
         "Savings are relative to `default` (start now, OS scheduling) for the same seed and trace; ± is a 95% CI.", ""]
    L += ["## E1 Baselines on AC", "", "| trace | policy | carbon saving % | energy saving % | miss rate % | mean delay h |",
          "|---|---|---|---|---|---|"]
    for _, r in res["e1"].iterrows():
        L.append(f"| {r.trace} | {r.policy} | {_fmt(r,'carbon_saving_pct')} | {_fmt(r,'energy_saving_pct')} | "
                 f"{100*r.miss_rate:.2f} | {r.mean_delay_h:.2f} |")
    L += ["", "## E2 Ablations (all traces, forecast noise 0.15)", "",
          "| policy | carbon saving % | energy saving % | miss rate % | p95 slowdown | escalations/run |",
          "|---|---|---|---|---|---|"]
    for _, r in res["e2"].iterrows():
        L.append(f"| {r.policy} | {_fmt(r,'carbon_saving_pct')} | {_fmt(r,'energy_saving_pct')} | "
                 f"{100*r.miss_rate:.2f} | {r.p95_slowdown:.2f} | {r.escalations:.1f} |")
    L += ["", "## E3 Uncertainty stress (forecast error x signal outage)", "",
          "| forecast noise | outage frac | policy | carbon saving % | miss rate % |", "|---|---|---|---|---|"]
    for _, r in res["e3"].iterrows():
        L.append(f"| {r.forecast_noise} | {r.outage_frac} | {r.policy} | {_fmt(r,'carbon_saving_pct')} | {100*r.miss_rate:.2f} |")
    L += ["", "## E4 Battery state of charge (carbon via battery ledger)", "",
          "| SoC | policy | energy saving % | miss rate % | p95 slowdown |", "|---|---|---|---|---|"]
    for _, r in res["e4"].iterrows():
        L.append(f"| {r.soc_level} | {r.policy} | {_fmt(r,'energy_saving_pct')} | {100*r.miss_rate:.2f} | {r.p95_slowdown:.2f} |")
    L += ["", "## E5 Prediction-error sensitivity", "", "| ML log-error sd | policy | carbon saving % | miss rate % |",
          "|---|---|---|---|"]
    for _, r in res["e5"].iterrows():
        L.append(f"| {r.ml_sigma} | {r.policy} | {_fmt(r,'carbon_saving_pct')} | {100*r.miss_rate:.2f} |")
    return "\n".join(L) + "\n"


def _plots(out: Path, res: dict):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    e1 = res["e1"]
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
    ax.set_title("E1: carbon saving by policy and grid trace (simulation)")
    fig.tight_layout(); fig.savefig(out / "e1_carbon_saving.png", dpi=150); plt.close(fig)

    e3 = res["e3"]
    fig, axs = plt.subplots(1, 2, figsize=(10, 3.6))
    for p in e3.policy.unique():
        if p == "default":
            continue
        d = e3[(e3.policy == p) & (e3.outage_frac == e3.outage_frac.max())].sort_values("forecast_noise")
        axs[0].errorbar(d.forecast_noise, d.carbon_saving_pct, d.carbon_saving_pct_ci95, marker="o", label=p, capsize=3)
        axs[1].plot(d.forecast_noise, 100 * d.miss_rate, marker="o", label=p)
    axs[0].set_xlabel("forecast noise"); axs[0].set_ylabel("carbon saving (%)")
    axs[1].set_xlabel("forecast noise"); axs[1].set_ylabel("deadline miss rate (%)")
    axs[0].legend(fontsize=7); axs[0].set_title("E3 with max outage fraction", fontsize=9)
    fig.tight_layout(); fig.savefig(out / "e3_uncertainty.png", dpi=150); plt.close(fig)

    e5 = res["e5"]
    fig, ax = plt.subplots(figsize=(5, 3.6))
    for p in ("greenthread", "gt_no_quantile"):
        d = e5[e5.policy == p].sort_values("ml_sigma")
        ax.plot(d.ml_sigma, 100 * d.miss_rate, marker="o", label=p)
    ax.set_xlabel("ML runtime log-error sd"); ax.set_ylabel("deadline miss rate (%)")
    ax.legend(); ax.set_title("E5: quantile constraint vs prediction error", fontsize=9)
    fig.tight_layout(); fig.savefig(out / "e5_pred_error.png", dpi=150); plt.close(fig)
