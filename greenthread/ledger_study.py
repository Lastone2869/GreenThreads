"""Battery carbon ledger: field tracking on a real laptop, and a year-long study on
real grid traces.

Three ways to attribute carbon to energy drawn from a laptop battery:
  zero        - battery energy is treated as free (what a naive meter on battery does)
  time-of-use - battery energy is charged at the grid intensity *when it is used*
                (what a grid-only carbon-aware scheduler implicitly assumes)
  ledger      - battery energy is charged at the intensity of the electricity that
                *charged* it, including charge losses (GreenThread)
Only the ledger follows the physical energy path: discharged joules were bought
from the grid at charge time.
"""
from __future__ import annotations

import csv
import json
import math
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .accounting import BatteryCarbonLedger
from .carbon import CSVReplaySource

LOG_FIELDS = ["unix_time", "iso_time", "on_ac", "percent", "remaining_mwh", "discharge_mw",
              "grid_intensity", "intensity_source", "ledger_intensity", "ledger_stored_wh"]


# ------------------------------------------------------------- field tracker
def wall_clock_source(cfg, trace_year: int = 2020):
    """CSV source whose t=0 is *now*, mapped to the same date/hour in the trace year
    (clearly labelled as replayed data when no live API is available)."""
    now = datetime.now(timezone.utc)
    try:
        start = now.replace(year=trace_year)
    except ValueError:              # 29 Feb
        start = now.replace(year=trace_year, day=28)
    return CSVReplaySource(cfg.resolve(cfg.carbon.csv_path), start.strftime("%Y-%m-%dT%H:%M:%S"), 3600.0)


def track(cfg, backend, source, interval_s: float = 60.0, hours: float = 24.0,
          out: str | Path | None = None, verbose: bool = True) -> Path:
    """Log battery state + grid intensity every `interval_s` and keep the ledger current.
    Run it for days across your normal charging habits; stop with Ctrl+C any time."""
    out = Path(out or cfg.resolve(cfg.results_dir) / "ledger_log.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    ledger = BatteryCarbonLedger(cfg.resolve(cfg.energy.ledger_path), cfg.energy.charge_efficiency,
                                 getattr(source, "mean", cfg.carbon.static_value) / cfg.energy.charge_efficiency)
    new = not out.exists()
    t0, prev = time.time(), None
    with open(out, "a", newline="") as fh:
        w = csv.DictWriter(fh, LOG_FIELDS)
        if new:
            w.writeheader()
        try:
            while time.time() - t0 < hours * 3600:
                ps = backend.power_status()
                tt = time.time() - t0
                lat = source.latest(tt)
                if ps.remaining_mwh is not None:
                    ledger.initialise(ps.remaining_mwh / 1000.0, lat.value / cfg.energy.charge_efficiency)
                    ledger.observe(prev, ps.remaining_mwh, ps.on_ac, lat.value)
                    prev = ps.remaining_mwh
                row = {"unix_time": round(time.time(), 1), "iso_time": datetime.now().isoformat(timespec="seconds"),
                       "on_ac": int(ps.on_ac), "percent": ps.percent, "remaining_mwh": ps.remaining_mwh,
                       "discharge_mw": ps.discharge_mw, "grid_intensity": round(lat.value, 2),
                       "intensity_source": lat.source, "ledger_intensity": round(ledger.intensity, 2),
                       "ledger_stored_wh": round(ledger.state.stored_wh, 3)}
                w.writerow(row)
                fh.flush()
                if verbose:
                    print(f"{row['iso_time']} ac={row['on_ac']} {ps.percent}% {ps.remaining_mwh} mWh "
                          f"grid={row['grid_intensity']} ledger={row['ledger_intensity']} g/kWh", flush=True)
                time.sleep(interval_s)
        except KeyboardInterrupt:
            pass
    return out


def report(log_path: str | Path, adapter_eff: float = 0.88, charge_eff: float = 0.85) -> dict:
    """Compare accounting methods on a tracker log (needs remaining_mwh readings)."""
    d = pd.read_csv(log_path).dropna(subset=["remaining_mwh"]).sort_values("unix_time")
    if len(d) < 3:
        raise ValueError("log has fewer than 3 battery readings")
    d["dE_wh"] = d.remaining_mwh.diff() / 1000.0
    d["dt_h"] = d.unix_time.diff() / 3600.0
    prev_ac = d.on_ac.shift().fillna(d.on_ac.iloc[0]).astype(bool)
    disch = d[(d.dE_wh < 0)]
    charge = d[(d.dE_wh > 0) & prev_ac]
    e_out = float(-disch.dE_wh.sum())
    # ledger, replayed from the log so the result does not depend on tracker state
    led = BatteryCarbonLedger(None, charge_eff, 0.0)
    first = d.iloc[0]
    led.initialise(first.remaining_mwh / 1000.0, float(first.grid_intensity) / charge_eff)
    c_ledger = 0.0
    for r in d.iloc[1:].itertuples():
        if r.dE_wh > 0 and r.on_ac:
            led.charge(r.dE_wh, r.grid_intensity)
        elif r.dE_wh < 0:
            c_ledger += -r.dE_wh / 1000.0 * led.intensity
            led.discharge(-r.dE_wh)
    c_tou = float((-disch.dE_wh * disch.grid_intensity / adapter_eff / 1000.0).sum())
    res = {
        "hours_logged": float(d.dt_h.sum()),
        "battery_energy_used_wh": e_out,
        "battery_energy_charged_wh": float(charge.dE_wh.sum()),
        "mean_grid_intensity_while_charging": float(np.average(charge.grid_intensity, weights=charge.dE_wh)) if len(charge) else None,
        "mean_grid_intensity_while_discharging": float(np.average(disch.grid_intensity, weights=-disch.dE_wh)) if len(disch) else None,
        "carbon_g": {"zero": 0.0, "time_of_use": c_tou, "ledger": c_ledger},
        "initial_charge_wh_assumed_at_first_intensity": float(first.remaining_mwh / 1000.0),
    }
    if c_ledger > 0:
        res["time_of_use_error_pct"] = 100 * (c_tou - c_ledger) / c_ledger
    return res


# ------------------------------------------------------------- year-long study
@dataclass
class Profile:
    name: str
    charge_hours: tuple[int, int]      # plugged in [start, end) local hour
    battery_hours: tuple[int, int]     # on battery and working [start, end)
    weekdays_only: bool = False
    load_w: float = 12.0               # average device draw while working on battery


PROFILES = [
    Profile("night-charger", (0, 7), (9, 18)),            # charge overnight, work unplugged all day
    Profile("office-day-charger", (9, 17), (18, 23), weekdays_only=True),
    Profile("midday-solar-charger", (11, 15), (16, 23)),  # deliberately charges at the solar peak
    Profile("evening-charger", (18, 23), (8, 13)),
]


def simulate_profile(src: CSVReplaySource, prof: Profile, days: int = 366, capacity_wh: float = 60.0,
                     charge_w: float = 45.0, charge_eff: float = 0.85, adapter_eff: float = 0.88,
                     tz_offset_h: float = 0.0) -> dict:
    """Hour-by-hour battery simulation over a recorded trace (trace hours are UTC;
    tz_offset_h shifts the user's local schedule)."""
    led = BatteryCarbonLedger(None, charge_eff, 0.0)
    led.initialise(capacity_wh * 0.6, src.true_value(0) / charge_eff)
    soc = capacity_wh * 0.6
    c = {"zero": 0.0, "time_of_use": 0.0, "ledger": 0.0}
    used = 0.0
    hourly = []
    for h in range(days * 24):
        t = h * 3600.0
        dt = datetime.fromtimestamp(src.trace_time(t), timezone.utc) + timedelta(hours=tz_offset_h)
        hr, wd = dt.hour, dt.weekday()
        workday = (wd < 5) or not prof.weekdays_only
        I = src.true_value(t)
        a, b = prof.charge_hours
        if workday and a <= hr < b and soc < capacity_wh:
            dE = min(charge_w * charge_eff, capacity_wh - soc)
            led.charge(dE, I)
            soc += dE
        a, b = prof.battery_hours
        if workday and a <= hr < b and soc > 0.1 * capacity_wh:
            dE = min(prof.load_w, soc - 0.1 * capacity_wh)
            c["ledger"] += dE / 1000.0 * led.intensity
            c["time_of_use"] += dE / adapter_eff / 1000.0 * I
            hourly.append((I / adapter_eff, led.intensity))
            led.discharge(dE)
            soc -= dE
            used += dE
    hourly = np.array(hourly) if hourly else np.zeros((0, 2))
    # decision relevance: in every battery hour, is running now on battery dirtier than
    # the grid now? ledger and time-of-use views disagree when the inequality flips
    # (time-of-use always says "same as grid", so it can never prefer the battery).
    flips = float(np.mean(hourly[:, 1] < hourly[:, 0])) if len(hourly) else 0.0
    return {"profile": prof.name, "battery_wh": used, **{f"carbon_{k}_g": v for k, v in c.items()},
            "tou_error_pct": 100 * (c["time_of_use"] - c["ledger"]) / c["ledger"] if c["ledger"] else float("nan"),
            "zero_error_pct": -100.0,
            "mean_ledger_intensity": c["ledger"] / used * 1000 if used else float("nan"),
            "hours_battery_cleaner_than_grid_pct": 100 * flips}


def run_study(cfg, traces: list[str], out_dir: str | Path) -> pd.DataFrame:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    # hours to add to trace timestamps to get the user's local clock. ENTSO-E traces
    # (DE/GB/FR) are UTC; the CAISO trace is already local (its daily minimum sits at
    # 11:00, local solar noon), and the synthetic India trace was generated in local time.
    tz = {"de": 1, "gb": 0, "fr": 1, "us-cal": 0, "in-so_synthetic": 0}
    for tr in traces:
        stem = Path(tr).stem.replace("_2020_hourly", "")
        src = CSVReplaySource(cfg.resolve(tr), "2020-01-01T00:00:00", 3600.0)
        for prof in PROFILES:
            r = simulate_profile(src, prof, days=365, charge_eff=cfg.energy.charge_efficiency,
                                 adapter_eff=cfg.energy.adapter_efficiency, tz_offset_h=tz.get(stem, 0))
            r["trace"] = stem
            rows.append(r)
    df = pd.DataFrame(rows)
    df.to_csv(out / "ledger_study.csv", index=False)
    L = ["# Battery carbon ledger study (generated)", "",
         "One simulated year per grid trace and charging habit (60 Wh battery, 12 W average draw on battery, "
         "45 W charger, charge efficiency %.2f, adapter efficiency %.2f). Real 2020 traces except the "
         "SYNTHETIC India trace. Local time: DE/FR = UTC+1, GB = UTC; CAISO and the synthetic India trace are already local time." % (cfg.energy.charge_efficiency,
                                                                             cfg.energy.adapter_efficiency), "",
         "| trace | charging habit | battery kWh/yr | ledger kgCO2e | time-of-use kgCO2e | time-of-use error | "
         "battery cleaner than grid (% of battery hours) |", "|---|---|---|---|---|---|---|"]
    for r in df.itertuples():
        L.append(f"| {r.trace} | {r.profile} | {r.battery_wh/1000:.1f} | {r.carbon_ledger_g/1000:.2f} | "
                 f"{r.carbon_time_of_use_g/1000:.2f} | {r.tou_error_pct:+.1f}% | {r.hours_battery_cleaner_than_grid_pct:.0f} |")
    L += ["", "Zero accounting reports 0 kg in every row (-100 %)."]
    (out / "LEDGER_STUDY.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    _plot(df, out)
    return df


def _plot(df: pd.DataFrame, out: Path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    traces = list(dict.fromkeys(df.trace))
    profs = list(dict.fromkeys(df.profile))
    fig, ax = plt.subplots(figsize=(9, 3.8))
    w = 0.8 / len(profs)
    for i, p in enumerate(profs):
        d = df[df.profile == p].set_index("trace").reindex(traces)
        ax.bar(np.arange(len(traces)) + i * w, d.tou_error_pct, w, label=p)
    ax.axhline(0, color="k", lw=0.5)
    ax.set_xticks(np.arange(len(traces)) + 0.4 - w / 2)
    ax.set_xticklabels(traces, fontsize=8)
    ax.set_ylabel("time-of-use error vs ledger (%)")
    ax.legend(fontsize=7)
    ax.set_title("How wrong battery carbon is when counted at time of use (zero accounting: -100 %)")
    fig.tight_layout(); fig.savefig(out / "ledger_study.png", dpi=150); plt.close(fig)
