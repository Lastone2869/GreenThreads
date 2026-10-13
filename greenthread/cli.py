"""Command-line interface: python -m greenthread <command> ..."""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

from .accounting import BatteryCarbonLedger, PowerModel
from .backends import get_backend
from .carbon import build_signal, build_source
from .config import Config, load_config


def _power_model(cfg: Config) -> PowerModel:
    p = cfg.resolve(cfg.results_dir) / "power_model.json"
    if p.exists():
        return PowerModel(**json.loads(p.read_text()))
    e = cfg.energy
    return PowerModel(e.idle_w, e.per_core_w_normal, e.per_core_w_eco)


def _predictors(cfg: Config, required: bool = False):
    from .model import MLPredictor
    rd = cfg.resolve(cfg.results_dir)
    ml = tb = None
    if (rd / "model.pkl").exists():
        ml = MLPredictor.load(rd / "model.pkl")
        ml.pm = _power_model(cfg)
    if (rd / "table.pkl").exists():
        import pickle
        tb = pickle.loads((rd / "table.pkl").read_bytes())
        tb.pm = _power_model(cfg)
    if required and (ml is None or tb is None):
        sys.exit("No trained predictors found. Run: python -m greenthread collect && python -m greenthread train")
    return ml, tb


# ------------------------------------------------------------------ commands
def cmd_detect(cfg, a):
    b = get_backend(a.backend)
    caps = b.capabilities()
    ps = b.power_status()
    info = {"backend": b.name, "capabilities": asdict(caps),
            "supported_modes": [m.value for m in caps.supported_modes()], "power": asdict(ps)}
    if a.probe and b.name == "windows":
        from .backends.windows import probe_controls
        info["probe"] = probe_controls(b, caps)
    info["capabilities"]["cpu_sets"] = [asdict(s) for s in caps.cpu_sets][:64]
    print(json.dumps(info, indent=2, default=str))
    out = cfg.resolve(cfg.results_dir) / "capabilities.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(info, indent=2, default=str))


def cmd_carbon(cfg, a):
    src = build_source(cfg)
    sig = build_signal(cfg, src)
    lat = src.latest(0.0)
    hours = list(range(0, a.hours + 1))
    offs = [h * 3600.0 / getattr(src, "scale", 1.0) for h in hours]
    eff, sigma = sig.plan_curve(0.0, offs)
    raw = src.forecast(0.0, offs)
    print(f"source={lat.source} zone={lat.zone} latest={lat.value:.1f} g/kWh age={lat.observed_age_s:.0f}s "
          f"available={lat.available} prior={sig.prior:.1f}")
    if getattr(src, "last_error", None):
        print("last error:", src.last_error)
    for h, r, e, s in zip(hours, raw, eff, sigma):
        print(f"  +{h:3d}h raw={r:7.1f} effective={e:7.1f} sigma={s:6.1f}")


def cmd_collect(cfg, a):
    from .collect import collect
    b = get_backend(a.backend)
    idle_file = cfg.resolve(cfg.results_dir) / "idle_w.json"
    idle_w = json.loads(idle_file.read_text())["idle_w"] if idle_file.exists() else None
    out = collect(cfg, b, a.out or cfg.resolve(cfg.results_dir) / "training_data.csv", a.reps,
                  scale=a.scale, seed=a.seed, measure_idle_s=a.idle, limit=a.limit, idle_w=idle_w)
    print("wrote", out)


def split_temporal(df: pd.DataFrame, fr=(0.6, 0.2)):
    df = df.sort_values("sample_id", kind="stable").reset_index(drop=True)
    n = len(df)
    a, b = int(n * fr[0]), int(n * (fr[0] + fr[1]))
    return df.iloc[:a], df.iloc[a:b], df.iloc[b:]


def cmd_train(cfg, a):
    import pickle
    from .model import MLPredictor, TablePredictor, evaluate_predictors
    data = pd.read_csv(a.data or cfg.resolve(cfg.results_dir) / "training_data.csv")
    data = data[data.control_failures.isna() | (data.control_failures == "")]
    pm = _power_model(cfg)
    q = cfg.scheduler.deadline_quantile
    tr, ca, te = split_temporal(data)
    metrics = {"temporal_split": evaluate_predictors(tr, te, ca, q, pm)}
    # workload-leakage check: hold out the largest size of every kind (extrapolation)
    big = data.groupby("kind").units.transform("max") == data.units
    tr2 = data[~big]
    ca2 = tr2.sample(frac=0.25, random_state=0)
    metrics["unseen_size_split"] = evaluate_predictors(tr2.drop(ca2.index), data[big], ca2, q, pm)
    # final models on all data (calibration on a held-out 20 %)
    cal = data.sample(frac=0.2, random_state=1)
    ml = MLPredictor(pm, q).fit(data.drop(cal.index), cal)
    tb = TablePredictor(pm, q).fit(data)
    rd = cfg.resolve(cfg.results_dir)
    ml.save(rd / "model.pkl")
    (rd / "table.pkl").write_bytes(pickle.dumps(tb))
    t0 = time.perf_counter()
    from .jobs import Job
    from .model import Ctx
    for _ in range(200):
        ml.predict(Job("x", "hash", 100, 2), "ECO", Ctx())
    metrics["ml_inference_ms"] = (time.perf_counter() - t0) / 200 * 1000
    metrics["model_size_kb"] = (rd / "model.pkl").stat().st_size / 1024
    metrics["n_samples"] = len(data)
    (rd / "model_metrics.json").write_text(json.dumps(metrics, indent=2))
    print(json.dumps(metrics, indent=2))


def cmd_calibrate(cfg, a):
    """Fit the utilisation power model from battery discharge (unplug first)."""
    from .collect import BackgroundLoad
    from .telemetry import PowerSampler, calibrate_power_model
    import subprocess
    from .backends.base import Mode
    b = get_backend(a.backend)
    caps = b.capabilities()
    ps = b.power_status()
    if ps.on_ac or ps.discharge_mw is None:
        sys.exit("Calibration needs the laptop on battery with BatteryStatus available. Unplug and retry.")
    s = PowerSampler(b, a.interval)
    s.start()
    flags = []
    levels = sorted({0, 1, max(1, caps.logical_cpus // 2), caps.logical_cpus})
    for eco in (False, True):
        for lv in levels:
            print(f"load={lv} eco={eco} for {a.step:.0f}s")
            with BackgroundLoad(lv) as bg:
                if bg.proc and eco:
                    b.apply(bg.proc.pid, Mode.ECO, caps)
                n0 = len(s.samples)
                time.sleep(a.step)
                flags += [eco] * (len(s.samples) - n0)
    s.stop()
    samples = s.samples[-len(flags):] if flags else []
    pm = calibrate_power_model(samples, flags, caps.logical_cpus)
    if pm is None:
        sys.exit("Not enough samples; increase --step")
    out = cfg.resolve(cfg.results_dir) / "power_model.json"
    out.write_text(json.dumps(pm.to_dict(), indent=2))
    print("power model:", pm.to_dict(), "->", out)


def _runtime(cfg, a, policy_name, ml, tb, log_name=None):
    from .engine import Engine
    from .policy import build_policy
    from .telemetry import PowerSampler
    b = get_backend(a.backend)
    src = build_source(cfg)
    pol = build_policy(policy_name, cfg, ml, tb)
    aware = getattr(getattr(pol, "spec", None), "uncertainty", True)
    sig = build_signal(cfg, src, uncertainty_aware=aware)
    ledger = BatteryCarbonLedger(cfg.resolve(cfg.energy.ledger_path), cfg.energy.charge_efficiency,
                                 sig.prior / cfg.energy.charge_efficiency)
    sampler = None
    ps = b.power_status()
    if not ps.on_ac and ps.discharge_mw is not None:
        sampler = PowerSampler(b, cfg.energy.sample_interval_s)
        sampler.start()
        idle = cfg.resolve(cfg.results_dir) / "idle_w.json"
        if idle.exists():
            sampler.idle_w = json.loads(idle.read_text())["idle_w"]
        if ps.remaining_mwh:
            ledger.initialise(ps.remaining_mwh / 1000.0, sig.prior / cfg.energy.charge_efficiency)
    log = cfg.resolve(cfg.results_dir) / (log_name or f"decisions_{policy_name}.jsonl")
    return Engine(cfg, b, pol, sig, src, ledger, _power_model(cfg), sampler, log, verbose=not a.quiet), sampler


def cmd_run(cfg, a):
    from .jobs import load_jobs
    ml, tb = _predictors(cfg, required=a.policy not in ("default", "heuristic"))
    eng, sampler = _runtime(cfg, a, a.policy, ml, tb)
    res = eng.run(load_jobs(a.jobs))
    if sampler:
        sampler.stop()
    df = pd.DataFrame([r.to_dict() for r in res])
    out = cfg.resolve(cfg.results_dir) / f"run_{a.policy}.csv"
    df.to_csv(out, index=False)
    print(df[["job_id", "mode", "mode_final", "deferred_s", "runtime_s", "deadline_met", "energy_wh",
              "energy_source", "carbon_g"]].to_string(index=False))
    print(f"\ncarbon={df.carbon_g.sum():.4f} g energy={df.energy_wh.sum()*1000:.2f} mWh "
          f"misses={(~df.deadline_met).sum()} -> {out}")


def cmd_idle(cfg, a):
    from .telemetry import PowerSampler
    b = get_backend(a.backend)
    s = PowerSampler(b, a.interval)
    s.start()
    print(f"measuring idle power for {a.seconds}s - leave the machine untouched")
    w = s.measure_idle(a.seconds)
    s.stop()
    if w is None:
        sys.exit("No discharge readings: run on battery with BatteryStatus available")
    out = cfg.resolve(cfg.results_dir) / "idle_w.json"
    out.write_text(json.dumps({"idle_w": w, "samples": len(s.samples)}))
    print(f"idle = {w:.2f} W -> {out}")


def cmd_experiment(cfg, a):
    from .experiments import run_real_experiment
    run_real_experiment(cfg, a)


def cmd_simulate(cfg, a):
    from .experiments import run_simulation_suite
    run_simulation_suite(cfg, a)


def cmd_ledger(cfg, a):
    led = BatteryCarbonLedger(cfg.resolve(cfg.energy.ledger_path), cfg.energy.charge_efficiency)
    print(json.dumps(asdict(led.state), indent=2))


def main(argv=None):
    p = argparse.ArgumentParser(prog="greenthread", description="Carbon- and battery-aware job scheduler")
    p.add_argument("--config", help="TOML config file")
    p.add_argument("--backend", default="auto", choices=["auto", "windows", "posix", "null"])
    p.add_argument("--quiet", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("detect", help="detect hardware/OS capabilities"); s.add_argument("--probe", action="store_true")
    s.set_defaults(fn=cmd_detect)
    s = sub.add_parser("carbon", help="show carbon signal and forecast"); s.add_argument("--hours", type=int, default=12)
    s.set_defaults(fn=cmd_carbon)
    s = sub.add_parser("collect", help="collect training data on this machine")
    s.add_argument("--reps", type=int, default=2); s.add_argument("--scale", type=float, default=1.0)
    s.add_argument("--seed", type=int, default=0); s.add_argument("--idle", type=float, default=0.0)
    s.add_argument("--limit", type=int); s.add_argument("--out")
    s.set_defaults(fn=cmd_collect)
    s = sub.add_parser("train", help="train + evaluate predictors"); s.add_argument("--data")
    s.set_defaults(fn=cmd_train)
    s = sub.add_parser("idle", help="measure idle power on battery"); s.add_argument("--seconds", type=float, default=120)
    s.add_argument("--interval", type=float, default=5.0); s.set_defaults(fn=cmd_idle)
    s = sub.add_parser("calibrate", help="fit power model on battery"); s.add_argument("--step", type=float, default=60)
    s.add_argument("--interval", type=float, default=5.0); s.set_defaults(fn=cmd_calibrate)
    s = sub.add_parser("run", help="schedule a job file"); s.add_argument("--jobs", required=True)
    s.add_argument("--policy", default="greenthread"); s.set_defaults(fn=cmd_run)
    s = sub.add_parser("experiment", help="real-machine baseline comparison")
    s.add_argument("--jobs", required=True); s.add_argument("--reps", type=int, default=3)
    s.add_argument("--policies", default="default,heuristic,carbon_only,battery_only,combined_noai,greenthread")
    s.add_argument("--tag", default="real"); s.add_argument("--cooldown", type=float, default=5.0)
    s.set_defaults(fn=cmd_experiment)
    s = sub.add_parser("simulate", help="trace-driven simulation suite")
    s.add_argument("--seeds", type=int, default=10); s.add_argument("--days", type=int, default=14)
    s.add_argument("--traces", default="de_2020_hourly,gb_2020_hourly,us-cal_2020_hourly,fr_2020_hourly,in-so_synthetic_2020_hourly")
    s.add_argument("--metrics", help="model_metrics.json to set predictor error levels")
    s.add_argument("--calibrate", action="store_true", help="set ECO slowdown/power from results/training_data.csv")
    s.add_argument("--quick", action="store_true"); s.set_defaults(fn=cmd_simulate)
    s = sub.add_parser("ledger", help="show battery carbon ledger"); s.set_defaults(fn=cmd_ledger)
    a = p.parse_args(argv)
    cfg = load_config(a.config)
    a.fn(cfg, a)


if __name__ == "__main__":
    main()
