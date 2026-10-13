"""Configuration loading (TOML, standard library only)."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, asdict, fields
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Weights:
    carbon: float = 1.0      # lambda_c
    battery: float = 1.0     # lambda_b (scaled further by state of charge)
    latency: float = 0.25    # lambda_l
    risk: float = 0.5        # lambda_r: penalty on carbon-signal uncertainty
    defer_overhead: float = 0.02  # lambda_o: small cost per deferral (wake-ups, user surprise)


@dataclass
class CarbonConfig:
    source: str = "csv"                      # csv | electricitymaps | static
    csv_path: str = "data/traces/de_2020_hourly.csv"
    csv_start: str = "2020-06-01T00:00:00"   # trace time that maps to t=0
    seconds_per_trace_hour: float = 3600.0   # <3600 compresses the trace for demos
    zone: str = "IN-SO"
    token_env: str = "ELECTRICITYMAPS_TOKEN"
    token_file: str = "secrets/electricitymaps.token"   # git-ignored; written by the setup scripts
    static_value: float = 700.0
    prior_intensity: float | None = None     # None -> long-run mean of trace / static value
    freshness_half_life_s: float = 3600.0    # confidence halves every half-life of staleness
    forecast_half_life_h: float = 12.0       # forecast confidence halves per 12 h of horizon
    forecast_noise_frac: float = 0.0         # inject forecast error in CSV mode (experiments)
    forecast_kind: str = "oracle"            # CSV mode: oracle | seasonal | persistence
    forecast_shift_h: float = 0.0            # CSV mode: phase error of the forecast (experiments)
    adaptive_confidence: bool = True         # learn forecast trust from observed errors
    spread: float | None = None              # prior std of intensity (None -> from trace / 15 % of prior)
    signal_state_path: str = "results/carbon_signal_state.json"
    log_path: str = "results/carbon_log.csv" # `carbon-log` output (live data becomes a replay trace)
    cache_path: str = "results/carbon_cache.json"


@dataclass
class EnergyConfig:
    adapter_efficiency: float = 0.88   # wall -> DC on AC (assumption, document it)
    charge_efficiency: float = 0.85    # wall -> stored battery energy (assumption)
    ledger_path: str = "results/battery_ledger.json"
    sample_interval_s: float = 5.0
    # Fallback utilisation power model (W). Replace with `greenthread calibrate` output.
    idle_w: float = 6.0
    per_core_w_normal: float = 4.5
    per_core_w_eco: float = 2.6


@dataclass
class SchedulerConfig:
    policy: str = "greenthread"
    deadline_quantile: float = 0.9
    safety_margin_s: float = 2.0
    defer_step_s: float = 60.0
    max_defer_candidates: int = 96
    max_concurrent: int = 1
    tick_s: float = 0.25
    escalate: bool = True              # drop eco restrictions if deadline at risk
    mode_gate_kappa: float = 1.0       # non-NORMAL modes judged at exp(kappa*rel_sigma) x predicted energy
    model_path: str = "results/model.pkl"
    allow_modes: list[str] = field(default_factory=lambda: ["NORMAL", "ECO", "HALF", "ECO_HALF", "ECORES"])


@dataclass
class Config:
    carbon: CarbonConfig = field(default_factory=CarbonConfig)
    energy: EnergyConfig = field(default_factory=EnergyConfig)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    weights: Weights = field(default_factory=Weights)
    results_dir: str = "results"

    def resolve(self, p: str) -> Path:
        path = Path(p)
        return path if path.is_absolute() else PACKAGE_ROOT / path

    def to_dict(self) -> dict:
        return asdict(self)


def _merge(dc, values: dict):
    known = {f.name for f in fields(dc)}
    for k, v in values.items():
        if k not in known:
            raise ValueError(f"Unknown config key '{k}' in section {type(dc).__name__}")
        cur = getattr(dc, k)
        if hasattr(cur, "__dataclass_fields__") and isinstance(v, dict):
            _merge(cur, v)
        else:
            setattr(dc, k, v)
    return dc


def load_config(path: str | os.PathLike | None = None) -> Config:
    cfg = Config()
    if path:
        with open(path, "rb") as fh:
            _merge(cfg, tomllib.load(fh))
    return cfg
