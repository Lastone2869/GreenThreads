"""Layer 1: runtime / energy prediction.

GreenThread needs, for every candidate execution mode m of a job:
  * q50(m)  - median runtime      (latency term)
  * qhi(m)  - upper-quantile runtime, default 90th percentile (deadline constraint)
  * E(m)    - incremental energy  (carbon + battery terms)
The ML predictor uses gradient-boosted quantile regression (scikit-learn
HistGradientBoostingRegressor, loss="quantile") on log-runtime, plus a direct
energy regressor when measured energy labels exist (Windows on battery).
The non-AI baseline is a lookup table of seconds-per-unit medians.
"""
from __future__ import annotations

import math
import pickle
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .accounting import PowerModel
from .backends.base import Mode
from .jobs import Job, Prediction
from .workload import KINDS

MODES = [m.value for m in (Mode.NORMAL, Mode.ECO, Mode.HALF, Mode.ECO_HALF, Mode.ECORES)]
FEATURES = ([f"kind_{k}" for k in KINDS] + [f"mode_{m}" for m in MODES] +
            ["log_units", "threads", "threads_over_cores", "logical_cpus", "physical_cores",
             "bg_load", "on_ac"])


@dataclass
class Ctx:
    logical_cpus: int = 4
    physical_cores: int = 4
    bg_load: float = 0.0     # system CPU utilisation (0..1) before the job starts
    on_ac: bool = True


def featurize(kind: str, units: float, threads: int, mode: str, ctx: Ctx) -> list[float]:
    eff_cores = ctx.physical_cores / 2 if mode in ("HALF", "ECO_HALF") else ctx.logical_cpus
    return ([1.0 if kind == k else 0.0 for k in KINDS] + [1.0 if mode == m else 0.0 for m in MODES] +
            [math.log(max(units, 1e-6)), float(threads), threads / max(1.0, eff_cores),
             float(ctx.logical_cpus), float(ctx.physical_cores), float(ctx.bg_load), float(ctx.on_ac)])


def frame_features(df: pd.DataFrame) -> np.ndarray:
    return np.array([featurize(r.kind, r.units, int(r.threads), r.mode,
                               Ctx(int(r.logical_cpus), int(r.physical_cores), float(r.bg_load), bool(r.on_ac)))
                     for r in df.itertuples()])


def _energy_wh(cpu_s: float, mode: str, pm: PowerModel) -> float:
    return pm.incremental_w(1.0, mode in ("ECO", "ECO_HALF")) * cpu_s / 3600.0


class Predictor:
    name = "base"
    quantile = 0.9

    def predict(self, job: Job, mode: str, ctx: Ctx) -> Prediction:  # pragma: no cover
        raise NotImplementedError


class TablePredictor(Predictor):
    """Non-AI baseline: median seconds/unit and cpu/unit per (kind, threads, mode),
    falling back to (kind, mode) then (kind); qhi = q50 * empirical ratio."""
    name = "table"

    def __init__(self, power_model: PowerModel | None = None, quantile: float = 0.9):
        self.pm = power_model or PowerModel()
        self.quantile = quantile
        self.tables: dict = {}
        self.hi_ratio = 1.3

    def fit(self, df: pd.DataFrame):
        d = df.assign(spu=df.runtime_s / df.units, cpu_pu=df.cpu_s / df.units)
        for keys in (("kind", "threads", "mode"), ("kind", "mode"), ("kind",)):
            self.tables[keys] = d.groupby(list(keys))[["spu", "cpu_pu"]].median().to_dict("index")
        # fallback ratio used when a mode was never measured
        self.mode_slowdown = (d.groupby("mode").spu.median() / d[d["mode"] == "NORMAL"].spu.median()).to_dict()
        q50 = d.apply(lambda r: self._lookup(r.kind, r.threads, r["mode"])[0] * r.units, axis=1)
        ratio = d.runtime_s / q50
        self.hi_ratio = float(max(1.0, np.quantile(ratio, self.quantile)))
        return self

    def _lookup(self, kind, threads, mode):
        for keys, key in ((("kind", "threads", "mode"), (kind, threads, mode)),
                          (("kind", "mode"), (kind, mode)), (("kind",), kind)):
            t = self.tables.get(keys, {})
            if key in t:
                v = t[key]
                scale = 1.0 if keys != ("kind",) else self.mode_slowdown.get(mode, 1.5)
                return v["spu"] * scale, v["cpu_pu"] * (scale if mode in ("ECO", "ECO_HALF") else 1.0)
        return 1.0, 1.0

    def predict(self, job, mode, ctx):
        spu, cpu_pu = self._lookup(job.kind, job.threads, mode)
        q50 = spu * job.units
        cpu = cpu_pu * job.units
        return Prediction(q50, q50 * self.hi_ratio, cpu, _energy_wh(cpu, mode, self.pm), "table")


class MLPredictor(Predictor):
    """Gradient-boosted quantile regression on log runtime (+ CPU-s, energy)."""
    name = "ml"

    def __init__(self, power_model: PowerModel | None = None, quantile: float = 0.9, seed: int = 0):
        self.pm = power_model or PowerModel()
        self.quantile = quantile
        self.seed = seed
        self.models: dict = {}
        self.has_energy = False
        self.calib = 1.0   # conformal multiplier on qhi (fit on validation split)

    def _hgb(self, loss, q=None):
        from sklearn.ensemble import HistGradientBoostingRegressor
        kw = dict(max_iter=300, learning_rate=0.06, min_samples_leaf=4, max_leaf_nodes=15,
                  l2_regularization=1e-3, random_state=self.seed)
        return HistGradientBoostingRegressor(loss=loss, quantile=q, **kw) if q else \
            HistGradientBoostingRegressor(loss=loss, **kw)

    # Targets are log *per-unit* quantities (seconds per unit, CPU-s per unit):
    # tree ensembles cannot extrapolate absolute runtime to unseen job sizes, but
    # per-unit rates are nearly size-invariant, so predictions scale correctly.
    def fit(self, df: pd.DataFrame, calib_df: pd.DataFrame | None = None):
        X = frame_features(df)
        u = df.units.values
        y = np.log(df.runtime_s.values / u)
        self.models["q50"] = self._hgb("quantile", 0.5).fit(X, y)
        self.models["qhi"] = self._hgb("quantile", self.quantile).fit(X, y)
        self.models["cpu"] = self._hgb("squared_error").fit(X, np.log(np.maximum(df.cpu_s.values, 1e-3) / u))
        m = df.energy_wh_measured.notna() if "energy_wh_measured" in df else pd.Series(False, index=df.index)
        if m.sum() >= 20:
            self.models["energy"] = self._hgb("squared_error").fit(X[m.values], np.log(df.energy_wh_measured[m].values.clip(1e-7) / u[m.values]))
            self.has_energy = True
        if calib_df is not None and len(calib_df) >= 10:
            # split-conformal style correction so qhi actually covers `quantile` of cases
            Xc = frame_features(calib_df)
            r = np.log(calib_df.runtime_s.values / calib_df.units.values) - self.models["qhi"].predict(Xc)
            self.calib = float(math.exp(max(0.0, np.quantile(r, self.quantile))))
        return self

    def predict_frame(self, df: pd.DataFrame) -> pd.DataFrame:
        X = frame_features(df)
        u = df.units.values
        q50 = np.exp(self.models["q50"].predict(X)) * u
        qhi = np.maximum(np.exp(self.models["qhi"].predict(X)) * self.calib * u, q50)
        cpu = np.exp(self.models["cpu"].predict(X)) * u
        return pd.DataFrame({"q50": q50, "qhi": qhi, "cpu_s": cpu}, index=df.index)

    def predict_modes(self, job, modes: list[str], ctx) -> dict:
        """Batched prediction for all candidate modes (one model call each)."""
        ctx = ctx or Ctx()
        X = np.array([featurize(job.kind, job.units, job.threads, m, ctx) for m in modes])
        q50 = np.exp(self.models["q50"].predict(X)) * job.units
        qhi = np.maximum(np.exp(self.models["qhi"].predict(X)) * self.calib * job.units, q50)
        cpu = np.exp(self.models["cpu"].predict(X)) * job.units
        en = np.exp(self.models["energy"].predict(X)) * job.units if self.has_energy else None
        out = {}
        for i, m in enumerate(modes):
            e = float(en[i]) if en is not None else _energy_wh(float(cpu[i]), m, self.pm)
            out[m] = Prediction(float(q50[i]), float(qhi[i]), float(cpu[i]), e,
                                "ml+measured-energy" if self.has_energy else "ml")
        return out

    def predict(self, job, mode, ctx):
        return self.predict_modes(job, [mode], ctx)[mode]

    def save(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as fh:
            pickle.dump(self, fh)

    @staticmethod
    def load(path) -> "MLPredictor":
        with open(path, "rb") as fh:
            return pickle.load(fh)


# --- evaluation ----------------------------------------------------------------
def pinball(y, q, tau):
    d = y - q
    return float(np.mean(np.maximum(tau * d, (tau - 1) * d)))


def evaluate_predictors(train: pd.DataFrame, test: pd.DataFrame, calib: pd.DataFrame | None = None,
                        quantile: float = 0.9, power_model: PowerModel | None = None) -> dict:
    """Compare ML vs table baseline on held-out data. Returns metrics dict."""
    ml = MLPredictor(power_model, quantile).fit(train, calib)
    tb = TablePredictor(power_model, quantile).fit(pd.concat([train, calib]) if calib is not None else train)
    pm = ml.predict_frame(test)
    out = {"n_train": len(train), "n_test": len(test)}
    y = test.runtime_s.values
    for name, q50, qhi in (
        ("ml", pm.q50.values, pm.qhi.values),
        ("table", *zip(*[(p.q50, p.qhi) for p in
                         (tb.predict(_row_job(r), r.mode, None) for r in test.itertuples())])),
    ):
        q50, qhi = np.asarray(q50), np.asarray(qhi)
        out[name] = {
            "mae_s": float(np.mean(np.abs(y - q50))),
            "mape_pct": float(np.mean(np.abs(y - q50) / y) * 100),
            "pinball_q50": pinball(y, q50, 0.5),
            f"pinball_q{int(quantile*100)}": pinball(y, qhi, quantile),
            f"coverage_q{int(quantile*100)}": float(np.mean(y <= qhi)),
            "mean_upper_margin_pct": float(np.mean((qhi - y) / y) * 100),
            "log_err_bias": float(np.mean(np.log(q50 / y))),
            "log_err_sd": float(np.std(np.log(q50 / y))),
        }
    return out


def _row_job(r) -> Job:
    return Job(id="x", kind=r.kind, units=float(r.units), threads=int(r.threads))
