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
import urllib.error
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
                 seed: int = 0, forecast_kind: str = "oracle", forecast_shift_h: float = 0.0):
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
        # forecast_kind: oracle    - recorded future + optional noise (a good forecast service)
        #                seasonal  - "same hour yesterday", re-levelled to the latest value
        #                            (what the free Electricity Maps tier allows: history only)
        #                persistence - latest value held flat
        # forecast_shift_h: phase error (e.g. wrong time zone / wrong grid zone): the
        #                forecast for T is the recorded value at T + shift
        self.forecast_kind = forecast_kind
        self.shift_s = forecast_shift_h * 3600.0
        self._noise_paths: dict[int, np.ndarray] = {}
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

    def _noise_path(self, issue_hour: int) -> np.ndarray:
        """Deterministic multiplicative error path (lead 0..96 h) for one issue hour.

        Error grows with lead time (AR(1) increments), and is independent of how the
        caller samples the horizon, so every query sees the same forecast."""
        p = self._noise_paths.get(issue_hour)
        if p is None:
            rng = np.random.default_rng((issue_hour * 7919 + self.seed * 104729) % (2 ** 32))
            eps = np.zeros(97)
            for k in range(1, 97):
                eps[k] = 0.85 * eps[k - 1] + rng.normal(0, self.noise)
            p = eps * np.sqrt(np.minimum(np.arange(97), 48) / 24.0 + 0.05)
            if len(self._noise_paths) > 4096:
                self._noise_paths.clear()
            self._noise_paths[issue_hour] = p
        return p

    def forecast(self, t: float, offsets_s: list[float]) -> list[float]:
        """Forecast issued at engine time t (or at the start of an outage) for t+offset."""
        base_t = self._in_outage(t)
        issue = t if base_t is None else base_t
        tt_issue = self.trace_time(issue)
        latest = self.true_value(issue)
        path = self._noise_path(int(tt_issue // 3600)) if self.noise > 0 else None
        out = []
        for off in offsets_s:
            tt = self.trace_time(t + off)
            if self.forecast_kind == "persistence":
                v = latest
            elif self.forecast_kind == "seasonal":
                v = latest + self.value_at_trace(tt - 86400) - self.value_at_trace(tt_issue - 86400)
            else:
                v = self.value_at_trace(tt + self.shift_s)
            if path is not None:
                lead_h = max(0.0, (tt - tt_issue) / 3600.0)
                v *= max(0.05, 1.0 + float(np.interp(lead_h, np.arange(97), path)))
            out.append(max(0.0, v))
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

    def trace_time(self, t):
        return t


class ElectricityMapsSource:
    """Live Electricity Maps v3 source with local cache and graceful degradation."""
    BASES = ("https://api.electricitymaps.com/v3", "https://api.electricitymap.org/v3")

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
        errors = []
        for base in self.BASES:
            req = urllib.request.Request(f"{base}/{endpoint}?zone={self.zone}",
                                         headers={"auth-token": self.token, "User-Agent": "GreenThread/1.1"})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
                    self.last_error = None if endpoint.endswith("latest") else self.last_error
                    return json.loads(r.read().decode())
            except urllib.error.HTTPError as e:   # 401 bad token, 403 not in plan, 429 rate limit
                errors.append(f"{endpoint}: HTTP {e.code}")
                if e.code in (401, 403, 429):
                    break
            except Exception as e:                # DNS, timeout, proxy ...
                errors.append(f"{endpoint}: {e}")
        if endpoint.endswith("latest"):
            self.last_error = "; ".join(errors)
        else:
            self.optional_errors = getattr(self, "optional_errors", []) + errors
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

    def trace_time(self, t: float) -> float:
        return self.t_start + t          # live: engine time is wall-clock seconds since start

    def observed_history(self) -> list[tuple[float, float]]:
        """(unix time, gCO2e/kWh) points the API has reported (last 24 h + latest)."""
        pts = [(_parse_ts(d), float(v)) for d, v in self.cache.get("history", []) if v is not None]
        lat = self.cache.get("latest")
        if lat and lat.get("datetime"):
            pts.append((_parse_ts(lat["datetime"]), float(lat["value"])))
        return pts

    def true_value(self, t: float) -> float:
        return self.latest(t).value

    def mean_true(self, t0, t1, n=16):
        return self.latest(t0).value


class CarbonSignal:
    """Carbon intensity the scheduler should *believe*, with its uncertainty.

    Three modes:
      uncertainty_aware=False          raw forecast, sigma = 0
      aware, adaptive=False            fixed decay: c = 0.5^(age/h_f) * 0.5^(horizon/h_h)
      aware, adaptive=True (default)   measured: every tick the signal stores the
          forecast it was given and later scores it against what the grid actually
          did, per lead-time bin. Lead = data age + horizon, so stale data and far
          forecasts are judged by the same evidence. The blend weight is the
          Bayes-optimal shrinkage of a forecast with error variance e^2 toward a prior
          with variance s^2:   c = s^2 / (s^2 + e^2),  sigma = sqrt(s^2 e^2 / (s^2 + e^2)).
          Before any evidence exists, e^2 is set so c equals the fixed-decay value,
          so the adaptive signal starts as cautious as the fixed one and becomes
          exactly as trusting as the forecast deserves.
    The shrinkage target is the observed hour-of-day climatology (typical intensity for
    that hour over the last ~4 weeks), so falling back keeps the predictable day/night
    pattern; s is the spread of intensity around that climatology.
    """
    LEAD_BINS_H = np.array([0.5, 1, 2, 3, 6, 9, 12, 18, 24, 36, 48, 72, 1e9])

    def __init__(self, source, prior: float, freshness_half_life_s: float = 3600.0,
                 forecast_half_life_h: float = 12.0, spread: float | None = None,
                 uncertainty_aware: bool = True, adaptive: bool = True,
                 pseudo_count: float = 5.0, max_count: float = 300.0, state_path: str | Path | None = None):
        self.source = source
        self.prior0 = float(prior)
        self.hl_fresh = freshness_half_life_s
        self.hl_fc = forecast_half_life_h
        self.spread = spread if spread is not None else max(getattr(source, "std", 0.0), 0.15 * prior)
        self.aware = uncertainty_aware
        self.adaptive = adaptive and uncertainty_aware
        self.pseudo, self.max_count = pseudo_count, max_count
        nb = len(self.LEAD_BINS_H)
        self.n = np.zeros(nb)
        self.mse = np.zeros(nb)
        self.realized: dict[int, float] = {}          # trace hour -> observed intensity
        self.pending: dict[tuple[int, int], tuple[float, float]] = {}  # (issue h, target h) -> (lead h, value)
        self.obs_sum, self.obs_n = 0.0, 0
        # hour-of-day climatology of observed intensity (the shrinkage target)
        self.clim_sum, self.clim_n = np.zeros(24), np.zeros(24)
        self.resid_ss, self.resid_n = 0.0, 0
        self.state_path = Path(state_path) if state_path else None
        self._load()

    # ---------------------------------------------------------------- state
    @property
    def prior(self) -> float:
        """Global mean intensity (scalar normaliser)."""
        return self.obs_sum / self.obs_n if self.obs_n >= 24 else self.prior0

    def prior_at(self, trace_times) -> np.ndarray:
        """Shrinkage target: typical intensity for that hour of day (needs >= 3 days of
        observations for the hour), else the global mean."""
        h = (np.asarray(trace_times, dtype=float) // 3600 % 24).astype(int)
        n = self.clim_n[h]
        return np.where(n >= 3, self.clim_sum[h] / np.maximum(n, 1), self.prior)

    @property
    def prior_spread(self) -> float:
        """Sd of intensity around the climatology (how much the prior alone misses)."""
        if self.resid_n >= 48:
            return max(math.sqrt(self.resid_ss / self.resid_n), 0.02 * self.prior)
        return self.spread

    def _record(self, h: int, v: float):
        hod = h % 24
        if self.clim_n[hod] >= 3:
            r = v - self.clim_sum[hod] / self.clim_n[hod]
            self.resid_ss += r * r
            self.resid_n += 1
        self.realized[h] = v
        self.obs_sum += v
        self.obs_n += 1
        # sliding climatology: cap the count so it tracks seasonal change (~4 weeks)
        if self.clim_n[hod] >= 28:
            self.clim_sum[hod] *= 27 / 28
            self.clim_n[hod] = 27
        self.clim_sum[hod] += v
        self.clim_n[hod] += 1
        if self.resid_n > 24 * 28:
            self.resid_ss *= (24 * 28 - 1) / (24 * 28)
            self.resid_n = 24 * 28 - 1

    def _bin(self, lead_h):
        return np.searchsorted(self.LEAD_BINS_H, lead_h, side="left").clip(0, len(self.LEAD_BINS_H) - 1)

    def _fixed_conf(self, lead_h):
        return 0.5 ** (np.asarray(lead_h) / self.hl_fc) if self.hl_fc > 0 else np.ones_like(lead_h)

    def error_var(self, lead_h) -> np.ndarray:
        """Posterior forecast-error variance per lead (pseudo-count prior + evidence)."""
        lead_h = np.atleast_1d(np.asarray(lead_h, dtype=float))
        s2 = self.prior_spread ** 2
        c0 = np.clip(self._fixed_conf(lead_h), 1e-3, 1 - 1e-6)
        e0 = s2 * (1.0 / c0 - 1.0)
        b = self._bin(lead_h)
        n, mse = self.n[b], self.mse[b]
        return (self.pseudo * e0 + n * mse) / (self.pseudo + n)

    def tick(self, t: float, horizon_h: float = 48.0, step_h: float = 1.0):
        """Record the current observation and forecast; score forecasts now realised."""
        if not self.adaptive:
            return
        scale = getattr(self.source, "scale", 1.0)
        tt_now = (getattr(self.source, "trace_time", None) or (lambda x: x * scale))(t)
        lat = self.source.latest(t)
        h_now = int(tt_now // 3600)
        if lat.available and lat.observed_age_s <= 3600 and h_now not in self.realized:
            self._record(h_now, lat.value)
        hist = getattr(self.source, "observed_history", None)
        if hist:                                   # live API: back-fill from reported history
            for ts, v in sorted(hist()):
                h = int(ts // 3600)
                if h not in self.realized and h not in getattr(self, "_seen", set()):
                    self._record(h, v)
                    self.__dict__.setdefault("_seen", set()).add(h)
        # store one forecast per (issue hour, target hour)
        if math.isinf(lat.observed_age_s):
            return
        issue_h = int((tt_now - lat.observed_age_s) // 3600)
        offs_h = np.arange(step_h, horizon_h + 1e-9, step_h)
        vals = self.source.forecast(t, list(offs_h * 3600.0 / scale))
        for oh, v in zip(offs_h, vals):
            target_h = int((tt_now + oh * 3600) // 3600)
            key = (issue_h, target_h)
            if key not in self.pending:
                self.pending[key] = (lat.observed_age_s / 3600.0 + oh, float(v))
        # score
        done = []
        for (ih, th), (lead, v) in self.pending.items():
            if th in self.realized:
                b = int(self._bin(lead))
                self.n[b] = min(self.n[b] + 1, self.max_count)
                self.mse[b] += ((v - self.realized[th]) ** 2 - self.mse[b]) / self.n[b]
                done.append((ih, th))
            elif th < h_now - 2:          # target passed without an observation: drop
                done.append((ih, th))
        for k in done:
            del self.pending[k]
        if len(self.realized) > 24 * 60:
            for h in sorted(self.realized)[: len(self.realized) - 24 * 30]:
                del self.realized[h]

    def save(self):
        if not self.state_path:
            return
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps({"n": self.n.tolist(), "mse": self.mse.tolist(),
                                                   "obs_sum": self.obs_sum, "obs_n": self.obs_n,
                                                   "clim_sum": self.clim_sum.tolist(), "clim_n": self.clim_n.tolist(),
                                                   "resid_ss": self.resid_ss, "resid_n": self.resid_n,
                                                   "spread": self.spread}))
        except OSError:
            pass

    def _load(self):
        if self.state_path and self.state_path.exists():
            try:
                d = json.loads(self.state_path.read_text())
                if len(d["n"]) == len(self.n):
                    self.n, self.mse = np.array(d["n"]), np.array(d["mse"])
                    self.obs_sum, self.obs_n = d["obs_sum"], d["obs_n"]
                    if "clim_sum" in d:
                        self.clim_sum, self.clim_n = np.array(d["clim_sum"]), np.array(d["clim_n"])
                        self.resid_ss, self.resid_n = d["resid_ss"], d["resid_n"]
            except Exception:
                pass

    def calibration_table(self) -> list[dict]:
        rows = []
        for i, edge in enumerate(self.LEAD_BINS_H):
            lead = edge if edge < 1e8 else 96
            e2 = float(self.error_var([lead])[0])
            c = self.prior_spread ** 2 / (self.prior_spread ** 2 + e2)
            rows.append({"lead_le_h": float(edge), "n": int(self.n[i]), "rmse": float(math.sqrt(self.mse[i])) if self.n[i] else None,
                         "confidence": round(c, 3)})
        return rows

    # -------------------------------------------------------------- queries
    def confidence(self, age_s: float, horizon_s: float) -> float:
        if not self.aware:
            return 1.0
        if math.isinf(age_s):
            return 0.0
        if not self.adaptive:
            return 0.5 ** (age_s / self.hl_fresh) * 0.5 ** ((horizon_s / 3600.0) / self.hl_fc)
        lead = (age_s + horizon_s) / 3600.0
        e2 = float(self.error_var([lead])[0])
        s2 = self.prior_spread ** 2
        return s2 / (s2 + e2)

    def plan_curve(self, t: float, offsets_s: list[float]) -> tuple[np.ndarray, np.ndarray]:
        """Effective intensity and sigma for engine offsets from now."""
        latest = self.source.latest(t)
        raw = np.asarray(self.source.forecast(t, offsets_s), dtype=float)
        if not self.aware:
            return raw, np.zeros_like(raw)
        scale = getattr(self.source, "scale", 1.0)
        hor = np.asarray(offsets_s, dtype=float) * scale
        if self.adaptive:
            tt = (getattr(self.source, "trace_time", None) or (lambda x: x * scale))(t) + hor
            prior = self.prior_at(tt)
            sp = self.prior_spread
        else:
            prior, sp = np.full_like(raw, self.prior), self.spread
        s2 = sp ** 2
        if math.isinf(latest.observed_age_s):
            return np.asarray(prior, dtype=float) * np.ones_like(raw), np.full_like(raw, sp)
        if self.adaptive:
            e2 = self.error_var((latest.observed_age_s + hor) / 3600.0)
            c = s2 / (s2 + e2)
            sig = np.sqrt(s2 * e2 / (s2 + e2))
        else:
            c = 0.5 ** (latest.observed_age_s / self.hl_fresh) * 0.5 ** ((hor / 3600.0) / self.hl_fc)
            sig = (1 - c) * self.spread
        return c * raw + (1 - c) * prior, sig

    def window_mean(self, t: float, start_off: float, dur: float, n: int = 6) -> tuple[float, float]:
        offs = [start_off + dur * (k + 0.5) / n for k in range(n)]
        eff, sig = self.plan_curve(t, offs)
        return float(eff.mean()), float(sig.mean())


def read_token(cfg: Config) -> str | None:
    """API token from the environment variable, else from the (git-ignored) token file."""
    tok = os.environ.get(cfg.carbon.token_env)
    if tok:
        return tok.strip()
    p = cfg.resolve(cfg.carbon.token_file)
    try:
        t = p.read_text().strip()
        return t or None
    except OSError:
        return None


def build_source(cfg: Config, outages=None, seed: int = 0):
    c: CarbonConfig = cfg.carbon
    if c.source == "csv":
        return CSVReplaySource(cfg.resolve(c.csv_path), c.csv_start, c.seconds_per_trace_hour,
                               c.forecast_noise_frac, outages, seed, c.forecast_kind, c.forecast_shift_h)
    if c.source == "static":
        return StaticSource(c.static_value, c.zone)
    if c.source == "electricitymaps":
        prior = c.prior_intensity or c.static_value
        return ElectricityMapsSource(c.zone, read_token(cfg), cfg.resolve(c.cache_path), prior)
    raise ValueError(f"unknown carbon source {c.source}")


def build_signal(cfg: Config, source, uncertainty_aware: bool = True, adaptive: bool | None = None) -> CarbonSignal:
    c = cfg.carbon
    prior = c.prior_intensity or getattr(source, "mean", c.static_value)
    spread = c.spread if c.spread else None
    state = cfg.resolve(c.signal_state_path) if c.source == "electricitymaps" else None
    return CarbonSignal(source, prior, c.freshness_half_life_s, c.forecast_half_life_h, spread=spread,
                        uncertainty_aware=uncertainty_aware,
                        adaptive=c.adaptive_confidence if adaptive is None else adaptive, state_path=state)
