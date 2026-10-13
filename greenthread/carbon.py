"""Carbon-intensity acquisition with explicit freshness and uncertainty.

Units: gCO2e/kWh throughout. Two sources are provided:
  * CSVReplaySource  - deterministic replay of a recorded trace (optionally
                       time-compressed for demos, with injectable forecast error
                       and signal outages for experiments).
  * ElectricityMapsSource - live API (v3 `carbon-intensity/latest` + `history`;
                       uses `forecast` when the token's plan includes it, else a
                       seasonal-naive forecast built from the last 24 h).
The CarbonSignal wrapper turns raw values into *effective* intensities by
discounting stale or far-ahead values toward a prior (the long-run mean), and
reports a standard deviation that the scheduler penalises (risk term).
"""
from __future__ import annotations

import csv
import json
import math
import os
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from .config import CarbonConfig, Config


@dataclass
class CarbonSample:
    value: float            # gCO2e/kWh
    observed_age_s: float   # age of the measurement in real (trace) seconds
    source: str
    zone: str
    available: bool = True


def _parse_ts(s: str) -> float:
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


class CSVReplaySource:
    """Replays an hourly (or finer) trace. Engine time t (s) maps to trace time
    start + t * (3600 / seconds_per_trace_hour)."""

    def __init__(self, path: str | Path, start: str | None = None, seconds_per_trace_hour: float = 3600.0,
                 forecast_noise_frac: float = 0.0, outages: list[tuple[float, float]] | None = None,
                 seed: int = 0):
        self.path = Path(path)
        ts, vals, zone = [], [], ""
        with open(self.path, newline="") as fh:
            for row in csv.DictReader(fh):
                ts.append(_parse_ts(row["timestamp"]))
                vals.append(float(row["carbon_intensity"]))
                zone = row.get("zone", zone)
        if not ts:
            raise ValueError(f"empty carbon trace {path}")
        order = np.argsort(ts)
        self.ts = np.asarray(ts)[order]
        self.vals = np.asarray(vals)[order]
        self.zone = zone or self.path.stem
        self.t0 = _parse_ts(start) if start else float(self.ts[0])
        self.scale = 3600.0 / seconds_per_trace_hour  # trace seconds per engine second
        self.noise = forecast_noise_frac
        self.outages = outages or []                   # engine-time windows with no signal
        self.seed = seed
        self.mean = float(self.vals.mean())
        self.std = float(self.vals.std())

    # trace helpers ----------------------------------------------------------
    def trace_time(self, t: float) -> float:
        return self.t0 + t * self.scale

    def value_at_trace(self, tt: float) -> float:
        i = int(np.searchsorted(self.ts, tt, side="right") - 1)
        i = min(max(i, 0), len(self.vals) - 1)
        return float(self.vals[i])

    def true_value(self, t: float) -> float:
        """Ground truth used for *accounting* (what the grid actually was)."""
        return self.value_at_trace(self.trace_time(t))

    def mean_true(self, t0: float, t1: float, n: int = 16) -> float:
        if t1 <= t0:
            return self.true_value(t0)
        return float(np.mean([self.true_value(t0 + (t1 - t0) * (k + 0.5) / n) for k in range(n)]))

    def _in_outage(self, t: float) -> float | None:
        for a, b in self.outages:
            if a <= t < b:
                return a
        return None

    # source interface -------------------------------------------------------
    def latest(self, t: float) -> CarbonSample:
        out_start = self._in_outage(t)
        if out_start is not None:  # serve last value seen before the outage, ageing
            age = (t - out_start) * self.scale
            return CarbonSample(self.true_value(out_start), age, "csv(stale)", self.zone, available=False)
        return CarbonSample(self.true_value(t), 0.0, "csv", self.zone)

    def forecast(self, t: float, offsets_s: list[float]) -> list[float]:
        """Forecast issued at engine time t for t+offset (engine seconds)."""
        base_t = self._in_outage(t)
        issue = t if base_t is None else base_t
        tt_issue = self.trace_time(issue)
        rng = np.random.default_rng(int(tt_issue // 3600) * 7919 + self.seed)
        out, eps = [], 0.0
        for off in offsets_s:
            v = self.value_at_trace(self.trace_time(t + off))
            if self.noise > 0:
                h = max(0.0, (t + off - issue) * self.scale / 3600.0)
                # random-walk forecast error growing with horizon (AR(1)-like)
                eps = 0.9 * eps + rng.normal(0, self.noise)
                v *= max(0.05, 1.0 + eps * math.sqrt(min(h, 48) / 24.0 + 0.05))
            out.append(v)
        return out


class StaticSource:
    def __init__(self, value: float, zone: str = "static"):
        self.mean, self.std, self.zone, self.scale = value, 0.0, zone, 1.0
        self.v = value

    def latest(self, t):
        return CarbonSample(self.v, 0.0, "static", self.zone)

    def forecast(self, t, offsets_s):
        return [self.v for _ in offsets_s]

    def true_value(self, t):
        return self.v

    def mean_true(self, t0, t1, n=16):
        return self.v


class ElectricityMapsSource:
    """Live Electricity Maps v3 source with local cache and graceful degradation."""
    BASE = "https://api.electricitymap.org/v3"

    def __init__(self, zone: str, token: str | None, cache_path: Path, prior: float,
                 min_refresh_s: float = 300.0, timeout_s: float = 8.0):
        self.zone, self.token, self.cache_path = zone, token, Path(cache_path)
        self.mean, self.std, self.scale = prior, 0.25 * prior, 1.0
        self.min_refresh_s, self.timeout_s = min_refresh_s, timeout_s
        self.t_start = time.time()
        self.cache = self._load_cache()
        self.last_error: str | None = None

    def _load_cache(self) -> dict:
        try:
            return json.loads(self.cache_path.read_text())
        except Exception:
            return {}

    def _save_cache(self):
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(json.dumps(self.cache))
        except OSError:
            pass

    def _get(self, endpoint: str) -> dict | None:
        if not self.token:
            self.last_error = "no API token (set ELECTRICITYMAPS_TOKEN)"
            return None
        req = urllib.request.Request(f"{self.BASE}/{endpoint}?zone={self.zone}",
                                     headers={"auth-token": self.token})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
                return json.loads(r.read().decode())
        except Exception as e:  # network, 401/403 (plan), 429, ...
            self.last_error = f"{endpoint}: {e}"
            return None

    def refresh(self, force: bool = False):
        now = time.time()
        if not force and now - self.cache.get("fetched_at", 0) < self.min_refresh_s:
            return
        latest = self._get("carbon-intensity/latest")
        if latest and "carbonIntensity" in latest:
            self.cache["latest"] = {"value": float(latest["carbonIntensity"]),
                                    "datetime": latest.get("datetime")}
            self.cache["fetched_at"] = now
            hist = self._get("carbon-intensity/history")
            if hist and hist.get("history"):
                self.cache["history"] = [(h["datetime"], h["carbonIntensity"]) for h in hist["history"]
                                         if h.get("carbonIntensity") is not None]
            fc = self._get("carbon-intensity/forecast")
            if fc and fc.get("forecast"):
                self.cache["forecast"] = [(f["datetime"], f["carbonIntensity"]) for f in fc["forecast"]]
            self._save_cache()

    def latest(self, t: float) -> CarbonSample:
        self.refresh()
        lat = self.cache.get("latest")
        if not lat:
            return CarbonSample(self.mean, float("inf"), "prior", self.zone, available=False)
        obs = _parse_ts(lat["datetime"]) if lat.get("datetime") else self.cache.get("fetched_at", 0)
        return CarbonSample(lat["value"], max(0.0, time.time() - obs), "electricitymaps", self.zone,
                            available=self.last_error is None)

    def forecast(self, t: float, offsets_s: list[float]) -> list[float]:
        now = time.time()
        fc = self.cache.get("forecast")
        if fc:
            pts = sorted((_parse_ts(d), v) for d, v in fc)
            xs = np.array([p[0] for p in pts]); ys = np.array([p[1] for p in pts])
            return [float(np.interp(now + o, xs, ys)) for o in offsets_s]
        lat = self.latest(t).value
        hist = self.cache.get("history")
        if not hist:
            return [lat for _ in offsets_s]           # persistence
        pts = sorted((_parse_ts(d), v) for d, v in hist)
        xs = np.array([p[0] for p in pts]); ys = np.array([p[1] for p in pts])
        ref = float(np.interp(now - 86400, xs, ys))
        # seasonal-naive: yesterday's shape, re-levelled to today's latest value
        return [lat + float(np.interp(now + o - 86400, xs, ys)) - ref for o in offsets_s]

    def true_value(self, t: float) -> float:
        return self.latest(t).value

    def mean_true(self, t0, t1, n=16):
        return self.latest(t0).value


class CarbonSignal:
    """Freshness- and horizon-discounted intensity with an uncertainty estimate."""

    def __init__(self, source, prior: float, freshness_half_life_s: float = 3600.0,
                 forecast_half_life_h: float = 12.0, spread: float | None = None,
                 uncertainty_aware: bool = True):
        self.source = source
        self.prior = prior
        self.hl_fresh = freshness_half_life_s
        self.hl_fc = forecast_half_life_h
        self.spread = spread if spread is not None else max(getattr(source, "std", 0.0), 0.15 * prior)
        self.aware = uncertainty_aware

    def confidence(self, age_s: float, horizon_s: float) -> float:
        if not self.aware:
            return 1.0
        if math.isinf(age_s):
            return 0.0
        return 0.5 ** (age_s / self.hl_fresh) * 0.5 ** ((horizon_s / 3600.0) / self.hl_fc)

    def plan_curve(self, t: float, offsets_s: list[float]) -> tuple[np.ndarray, np.ndarray]:
        """Effective intensity and sigma for engine offsets from now."""
        latest = self.source.latest(t)
        raw = np.asarray(self.source.forecast(t, offsets_s), dtype=float)
        scale = getattr(self.source, "scale", 1.0)
        eff, sig = np.empty_like(raw), np.empty_like(raw)
        for i, off in enumerate(offsets_s):
            c = self.confidence(latest.observed_age_s, off * scale)
            eff[i] = c * raw[i] + (1 - c) * self.prior
            sig[i] = (1 - c) * self.spread
        return eff, sig

    def window_mean(self, t: float, start_off: float, dur: float, n: int = 6) -> tuple[float, float]:
        offs = [start_off + dur * (k + 0.5) / n for k in range(n)]
        eff, sig = self.plan_curve(t, offs)
        return float(eff.mean()), float(sig.mean())


def build_source(cfg: Config, outages=None, seed: int = 0):
    c: CarbonConfig = cfg.carbon
    if c.source == "csv":
        return CSVReplaySource(cfg.resolve(c.csv_path), c.csv_start, c.seconds_per_trace_hour,
                               c.forecast_noise_frac, outages, seed)
    if c.source == "static":
        return StaticSource(c.static_value, c.zone)
    if c.source == "electricitymaps":
        prior = c.prior_intensity or c.static_value
        return ElectricityMapsSource(c.zone, os.environ.get(c.token_env), cfg.resolve(c.cache_path), prior)
    raise ValueError(f"unknown carbon source {c.source}")


def build_signal(cfg: Config, source, uncertainty_aware: bool = True) -> CarbonSignal:
    prior = cfg.carbon.prior_intensity or getattr(source, "mean", cfg.carbon.static_value)
    return CarbonSignal(source, prior, cfg.carbon.freshness_half_life_s, cfg.carbon.forecast_half_life_h,
                        uncertainty_aware=uncertainty_aware)
