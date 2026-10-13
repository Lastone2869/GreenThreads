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
        self.rel_sigma: dict[str, float] = {}
        self.mode_prior: dict[str, tuple[float, float]] = {}

    def fit(self, df: pd.DataFrame):
        d = df.assign(spu=df.runtime_s / df.units, cpu_pu=df.cpu_s / df.units)
        for keys in (("kind", "threads", "mode"), ("kind", "mode"), ("kind",)):
            self.tables[keys] = d.groupby(list(keys))[["spu", "cpu_pu"]].median().to_dict("index")
        # fallback ratio used when a mode was never measured
        self.mode_slowdown = (d.groupby("mode").spu.median() / d[d["mode"] == "NORMAL"].spu.median()).to_dict()
        q50 = d.apply(lambda r: self._lookup(r.kind, r.threads, r["mode"])[0] * r.units, axis=1)
        ratio = d.runtime_s / q50
        self.hi_ratio = float(max(1.0, np.quantile(ratio, self.quantile)))
        # mode-ratio error (in-sample, inflated): table ratio = median(mode)/median(NORMAL)
        ref = _paired_reference(df, df)
        pr = df.join(ref).query("mode != 'NORMAL'").dropna(subset=["ref_runtime"])
        noise, _ = replicate_noise(df, "cpu_s")
        keys = [k for k in CONFIG_KEYS if k in df.columns]
        n_ref = df[df["mode"] == "NORMAL"].groupby(keys).size().mean()
        label_var = noise ** 2 * (1.0 + 1.0 / max(1.0, float(n_ref)))
        self.rel_sigma = {"NORMAL": 0.0}
        for mo, g in pr.groupby("mode"):
            pred = np.array([self._lookup(r.kind, r.threads, mo)[1] / self._lookup(r.kind, r.threads, "NORMAL")[1]
                             for r in g.itertuples()])
            res = np.log(np.maximum(g.cpu_s.values, 1e-3) / np.maximum(g.ref_cpu.values, 1e-3)) - np.log(pred)
            # in-sample, so inflate by 1.2; remove irreducible run-to-run noise
            self.rel_sigma[mo] = float(max(0.02, math.sqrt(max(0.0, np.mean(res ** 2) - label_var))) * 1.2)
        self._prior_df = df[["kind", "threads", "mode"]].copy()
        self._table_prior()
        return self

    def set_power_model(self, pm: PowerModel):
        self.pm = pm
        if getattr(self, "_prior_df", None) is not None:
            self._table_prior()

    def _table_prior(self):
        df = self._prior_df
        self.mode_prior = {}
        cfgs = df.drop_duplicates(["kind", "threads"])
        for mo in set(df["mode"]) - {"NORMAL"}:
            lr = np.log([_energy_wh(self._lookup(r.kind, r.threads, mo)[1], mo, self.pm) /
                         _energy_wh(self._lookup(r.kind, r.threads, "NORMAL")[1], "NORMAL", self.pm)
                         for r in cfgs.itertuples()])
            self.mode_prior[mo] = (float(np.mean(lr)), float(np.std(lr)))

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
        return Prediction(q50, q50 * self.hi_ratio, cpu, _energy_wh(cpu, mode, self.pm), "table",
                          rel_sigma=self.rel_sigma.get(mode, 0.3 if mode != "NORMAL" else 0.0))


CONFIG_KEYS = ["kind", "units", "threads", "bg_threads"]


def replicate_noise(df: pd.DataFrame, col: str = "runtime_s") -> tuple[float, int]:
    """Run-to-run noise: pooled within-group sd of log(col) over identical repeated runs.
    Returns (sd, number of replicate groups). This is irreducible label noise, which
    must not be counted as model error."""
    keys = [k for k in CONFIG_KEYS + ["mode"] if k in df.columns]
    l = np.log(df[col].clip(lower=1e-3))
    grp = l.groupby([df[k] for k in keys])
    n = grp.transform("count")
    dev = (l - grp.transform("mean"))[n >= 2]
    groups = int((grp.count() >= 2).sum())
    dof = int((grp.count()[grp.count() >= 2] - 1).sum())
    return (float(np.sqrt((dev ** 2).sum() / dof)) if dof > 0 else 0.0), groups


def _paired_reference(df: pd.DataFrame, ref_pool: pd.DataFrame) -> pd.Series:
    """Median NORMAL-mode runtime of the same job configuration (from ref_pool labels)."""
    keys = [k for k in CONFIG_KEYS if k in df.columns and k in ref_pool.columns]
    ref = ref_pool[ref_pool["mode"] == "NORMAL"].groupby(keys)[["runtime_s", "cpu_s"]].median()
    ref.columns = ["ref_runtime", "ref_cpu"]
    return df.join(ref, on=keys)[["ref_runtime", "ref_cpu"]]


class MLPredictor(Predictor):
    """Learned runtime / CPU / energy predictor.

    Level models: gradient-boosted quantile regression (q50, q_tau) of log seconds per
    unit, plus log CPU-seconds per unit, with the execution mode as a feature. Because
    all modes of one job share every other feature, the model's errors move together
    across modes, so mode *differences* are predicted more accurately than absolute
    runtimes. `rel_sigma[m]` is the held-out error of the predicted mode effect; the
    policy refuses a mode whose predicted advantage is inside that error.

    Paired ratio models (log(runtime_m/runtime_NORMAL) trained on paired runs) are also
    fitted and reported in evaluation, but are NOT used for decisions: on our data they
    compared modes less accurately than the level model (documented negative result).
    """
    name = "ml"

    def __init__(self, power_model: PowerModel | None = None, quantile: float = 0.9, seed: int = 0):
        self.pm = power_model or PowerModel()
        self.quantile = quantile
        self.seed = seed
        self.models: dict = {}
        self.has_energy = False
        self.calib = 1.0          # conformal multiplier on qhi (fit on validation split)
        self.rel_sigma: dict[str, float] = {}
        self.mode_prior: dict[str, tuple[float, float]] = {}
        self.label_noise = 0.0

    def _hgb(self, loss, q=None, max_iter=300):
        from sklearn.ensemble import HistGradientBoostingRegressor
        kw = dict(max_iter=max_iter, learning_rate=0.06, min_samples_leaf=4, max_leaf_nodes=15,
                  l2_regularization=1e-3, random_state=self.seed)
        return HistGradientBoostingRegressor(loss=loss, quantile=q, **kw) if q else \
            HistGradientBoostingRegressor(loss=loss, **kw)

    # Size scaling: tree ensembles cannot extrapolate past the largest job size seen, and
    # per-unit rates are not constant when jobs have fixed overhead. So each target is
    # log(y) = a_kind + b_kind * log(units)  [per-kind least-squares line]  + GBDT residual.
    # Beyond the training range the line carries the trend; the trees only learn the
    # (bounded) effects of mode, threads, contention and power state.
    def _fit_lines(self, df: pd.DataFrame, col: str) -> dict:
        lines = {}
        for k, g in df.groupby("kind"):
            x = np.log(g.units.values)
            y = np.log(np.maximum(g[col].values, 1e-3))
            if len(np.unique(x)) >= 2:
                b, a = np.polyfit(x, y, 1)
                b = float(np.clip(b, 0.3, 1.5))          # physically plausible scaling
                a = float(np.mean(y - b * x))
            else:
                b, a = 1.0, float(np.mean(y - x))
            lines[k] = (a, b)
        return lines

    def _line(self, kinds, units, col: str) -> np.ndarray:
        L = getattr(self, "lines", {}).get(col, {})      # models saved by v1.0: per-unit (b = 1)
        return np.array([L.get(k, (0.0, 1.0))[0] + L.get(k, (0.0, 1.0))[1] * math.log(max(u, 1e-6))
                         for k, u in zip(kinds, units)])

    def fit(self, df: pd.DataFrame, calib_df: pd.DataFrame | None = None):
        X = frame_features(df)
        u = df.units.values
        self.lines = {"runtime_s": self._fit_lines(df, "runtime_s"), "cpu_s": self._fit_lines(df, "cpu_s")}
        y = np.log(df.runtime_s.values) - self._line(df.kind.values, u, "runtime_s")
        self.models["q50"] = self._hgb("quantile", 0.5).fit(X, y)
        self.models["qhi"] = self._hgb("quantile", self.quantile).fit(X, y)
        yc = np.log(np.maximum(df.cpu_s.values, 1e-3)) - self._line(df.kind.values, u, "cpu_s")
        self.models["cpu"] = self._hgb("squared_error").fit(X, yc)
        # paired ratio models (non-NORMAL rows with a NORMAL reference of the same config)
        ref = _paired_reference(df, df)
        pr = df.join(ref).query("mode != 'NORMAL'").dropna(subset=["ref_runtime"])
        self.models["ratio_rt"] = self.models["ratio_cpu"] = None
        if len(pr) >= 20:
            Xr = frame_features(pr)
            self.models["ratio_rt"] = self._hgb("squared_error", max_iter=200).fit(
                Xr, np.log(pr.runtime_s.values / pr.ref_runtime.values))
            self.models["ratio_cpu"] = self._hgb("squared_error", max_iter=200).fit(
                Xr, np.log(np.maximum(pr.cpu_s.values, 1e-3) / np.maximum(pr.ref_cpu.values, 1e-3)))
        m = df.energy_wh_measured.notna() if "energy_wh_measured" in df else pd.Series(False, index=df.index)
        if m.sum() >= 20:
            self.models["energy"] = self._hgb("squared_error").fit(
                X[m.values], np.log(df.energy_wh_measured[m].values.clip(1e-7) / u[m.values]))
            self.has_energy = True
        # held-out ratio error per mode (fallback: inflated in-sample error)
        src = calib_df if calib_df is not None and len(calib_df) >= 10 else None
        self.rel_sigma = self._direct_ratio_errors(df, df, inflate=1.5)
        self._prior_cfgs = df.drop_duplicates([k for k in CONFIG_KEYS if k in df.columns]).copy()
        self.mode_prior = self._mode_prior(self._prior_cfgs)
        if src is not None:
            p = self.predict_frame(src, raw=True)
            r = np.log(src.runtime_s.values) - np.log(p.qhi.values)
            # error of the predicted mode effect (direct model) on held-out paired runs
            self.rel_sigma = self._direct_ratio_errors(src, pd.concat([df, src]))
            self.calib = float(math.exp(max(0.0, np.quantile(r, self.quantile))))
        return self

    def _ratio_pred(self, X: np.ndarray, modes) -> tuple[np.ndarray, np.ndarray]:
        rr = np.zeros(len(X)); rc = np.zeros(len(X))
        nn = np.array([m != "NORMAL" for m in modes])
        if self.models.get("ratio_rt") is not None and nn.any():
            rr[nn] = self.models["ratio_rt"].predict(X[nn])
            rc[nn] = self.models["ratio_cpu"].predict(X[nn])
        elif nn.any():   # no paired data: fall back to the level model's mode effect
            Xn = X.copy()
            mi = len(KINDS)
            Xn[:, mi:mi + len(MODES)] = 0.0
            Xn[:, mi] = 1.0  # NORMAL one-hot
            rr[nn] = (self.models["q50"].predict(X) - self.models["q50"].predict(Xn))[nn]
            rc[nn] = (self.models["cpu"].predict(X) - self.models["cpu"].predict(Xn))[nn]
        return rr, rc

    def _ratio_errors(self, eval_df, ref_pool, inflate=1.0, col="runtime_s") -> dict[str, float]:
        ref = _paired_reference(eval_df, ref_pool)
        d = eval_df.join(ref).query("mode != 'NORMAL'").dropna(subset=["ref_runtime"])
        out = {"NORMAL": 0.0}
        if len(d) == 0:
            return out
        rr, rc = self._ratio_pred(frame_features(d), list(d["mode"]))
        if col == "cpu_s":
            res = np.log(np.maximum(d.cpu_s.values, 1e-3) / np.maximum(d.ref_cpu.values, 1e-3)) - rc
        else:
            res = np.log(d.runtime_s.values / d.ref_runtime.values) - rr
        for mo in d["mode"].unique():
            sel = (d["mode"] == mo).values
            out[mo] = float(np.sqrt(np.mean(res[sel] ** 2)) * inflate) if sel.sum() >= 3 else 0.3
        return out

    def set_power_model(self, pm: PowerModel):
        """Swap in a (calibrated) power model and recompute energy-dependent priors."""
        self.pm = pm
        if getattr(self, "_prior_cfgs", None) is not None:
            self.mode_prior = self._mode_prior(self._prior_cfgs)

    def _mode_prior(self, df: pd.DataFrame) -> dict[str, tuple[float, float]]:
        """Population mode effect: mean and spread, over the training configurations, of
        the predicted log energy ratio E_m/E_NORMAL (used for shrinkage in the policy)."""
        cfgs = df.drop_duplicates([k for k in CONFIG_KEYS if k in df.columns])
        out = {}
        Xn = frame_features(cfgs.assign(mode="NORMAL"))
        _, _, cpun = self._level(Xn, cfgs.units.values)
        en = np.array([_energy_wh(c, "NORMAL", self.pm) for c in cpun])
        for mo in MODES:
            if mo == "NORMAL" or mo not in set(df["mode"]):
                continue
            _, _, cpu = self._level(frame_features(cfgs.assign(mode=mo)), cfgs.units.values)
            lr = np.log(np.array([_energy_wh(c, mo, self.pm) for c in cpu]) / en)
            out[mo] = (float(lr.mean()), float(lr.std()))
        return out

    def _direct_ratio_errors(self, eval_df, ref_pool, inflate=1.0) -> dict[str, float]:
        """RMSE per mode of log(cpu_m/cpu_NORMAL) predicted by the level model vs realised.
        Realised ratios contain run-to-run noise, so this over-states the error (safe side)."""
        ref = _paired_reference(eval_df, ref_pool)
        d = eval_df.join(ref).query("mode != 'NORMAL'").dropna(subset=["ref_runtime"])
        out = {"NORMAL": 0.0}
        if len(d) == 0:
            return out
        _, _, cpu = self._level(frame_features(d), d.units.values)
        _, _, cpun = self._level(frame_features(d.assign(mode="NORMAL")), d.units.values)
        res = np.log(cpu / cpun) - np.log(np.maximum(d.cpu_s.values, 1e-3) / np.maximum(d.ref_cpu.values, 1e-3))
        # the realised ratio carries label noise of one run plus the NORMAL reference median
        noise, _ = replicate_noise(ref_pool, "cpu_s")
        keys = [k for k in CONFIG_KEYS if k in ref_pool.columns]
        n_ref = ref_pool[ref_pool["mode"] == "NORMAL"].groupby(keys).size().mean() if len(ref_pool) else 1.0
        label_var = noise ** 2 * (1.0 + 1.0 / max(1.0, float(n_ref)))
        self.label_noise = noise
        for mo in d["mode"].unique():
            sel = (d["mode"] == mo).values
            if sel.sum() >= 3:
                mse = float(np.mean(res[sel] ** 2))
                # model error = observed error minus irreducible noise (floor 0.02)
                out[mo] = float(max(0.02, math.sqrt(max(0.0, mse - label_var))) * inflate)
            else:
                out[mo] = 0.3
        return out

    def _level(self, X: np.ndarray, units: np.ndarray, kinds=None):
        if kinds is None:   # recover kind from the one-hot features
            kinds = [KINDS[int(np.argmax(r[:len(KINDS)]))] for r in X]
        lr, lc = self._line(kinds, units, "runtime_s"), self._line(kinds, units, "cpu_s")
        q50 = np.exp(self.models["q50"].predict(X) + lr)
        qhi = np.maximum(np.exp(self.models["qhi"].predict(X) + lr), q50)
        cpu = np.exp(self.models["cpu"].predict(X) + lc)
        return q50, qhi, cpu

    def predict_frame(self, df: pd.DataFrame, raw: bool = False) -> pd.DataFrame:
        """Runtimes come from the direct per-mode level model (most accurate absolute
        prediction, used for deadlines); mode *comparisons* use the paired ratio."""
        X = frame_features(df)
        Xn = frame_features(df.assign(mode="NORMAL"))
        q50, qhi, cpu = self._level(X, df.units.values)
        q50n, _, cpun = self._level(Xn, df.units.values)
        rr, rc = self._ratio_pred(X, list(df["mode"]))
        qhi = qhi * (1.0 if raw else self.calib)
        return pd.DataFrame({"q50": q50, "qhi": np.maximum(qhi, q50), "cpu_s": cpu,
                             "q50_normal": q50n, "cpu_normal": cpun, "ratio_paired": np.exp(rr), "ratio_direct": q50 / q50n,
                             "cpu_ratio_paired": np.exp(rc)}, index=df.index)

    def predict_modes(self, job, modes: list[str], ctx) -> dict:
        """Batched prediction for all candidate modes."""
        ctx = ctx or Ctx()
        X = np.array([featurize(job.kind, job.units, job.threads, m, ctx) for m in modes])
        Xn = np.array([featurize(job.kind, job.units, job.threads, "NORMAL", ctx)])
        q50s, qhis, cpus = self._level(X, np.full(len(modes), float(job.units)))
        _, _, cpun = (float(v[0]) for v in self._level(Xn, np.array([job.units])))
        en_n = float(np.exp(self.models["energy"].predict(Xn)[0]) * job.units) if self.has_energy else None
        e_n_model = max(_energy_wh(cpun, "NORMAL", self.pm), 1e-12)
        out = {}
        for i, m in enumerate(modes):
            q50 = float(q50s[i])
            qhi = max(q50, float(qhis[i]) * self.calib)
            cpu = float(cpus[i])
            # with measured energy labels, scale the measured NORMAL energy by the mode effect
            ratio_e = _energy_wh(cpu, m, self.pm) / e_n_model
            e = (en_n if en_n is not None else e_n_model) * ratio_e
            out[m] = Prediction(q50, qhi, cpu, e, "ml+measured-energy" if self.has_energy else "ml",
                                rel_sigma=self.rel_sigma.get(m, 0.3 if m != "NORMAL" else 0.0))
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
    allp = pd.concat([train, test] + ([calib] if calib is not None else []))
    out["label_noise"] = {"runtime_log_sd": replicate_noise(allp, "runtime_s")[0],
                          "cpu_log_sd": replicate_noise(allp, "cpu_s")[0],
                          "replicate_groups": replicate_noise(allp, "runtime_s")[1]}
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
            # model error with irreducible run-to-run noise removed
            "model_log_err_sd": float(math.sqrt(max(0.0, np.var(np.log(q50 / y)) - out["label_noise"]["runtime_log_sd"] ** 2))),
        }
    # mode-comparison accuracy: predicted vs realised ratio runtime(m)/runtime(NORMAL)
    pool = pd.concat([train, test] + ([calib] if calib is not None else []))
    ref = _paired_reference(test, pool)
    d = test.join(ref).query("mode != 'NORMAL'").dropna(subset=["ref_runtime"])
    if len(d):
        real = np.log(d.runtime_s.values / d.ref_runtime.values)
        real_cpu = np.log(np.maximum(d.cpu_s.values, 1e-3) / np.maximum(d.ref_cpu.values, 1e-3))
        pml = ml.predict_frame(d)
        tb_ratio = np.log([tb._lookup(r.kind, r.threads, r.mode)[0] / tb._lookup(r.kind, r.threads, "NORMAL")[0]
                           for r in d.itertuples()])
        tb_cpu = np.log([tb._lookup(r.kind, r.threads, r.mode)[1] / tb._lookup(r.kind, r.threads, "NORMAL")[1]
                         for r in d.itertuples()])
        nl = out["label_noise"]["cpu_log_sd"]
        n_ref = pool[pool["mode"] == "NORMAL"].groupby([k for k in CONFIG_KEYS if k in pool]).size().mean()
        lv = nl ** 2 * (1 + 1 / max(1.0, float(n_ref)))
        rm = lambda e: float(np.sqrt(np.mean(e ** 2)))
        rmc = lambda e: float(math.sqrt(max(0.0, np.mean(e ** 2) - lv)))
        out["mode_comparison"] = {
            "n": int(len(d)),
            "note": "RMSE of log(mode/NORMAL) ratio vs realised paired ratio (includes label noise)",
            "runtime_ratio_rmse": {"ml_paired": rm(np.log(pml.ratio_paired.values) - real),
                                   "ml_direct_difference": rm(np.log(pml.ratio_direct.values) - real),
                                   "table": rm(tb_ratio - real)},
            "cpu_ratio_rmse": {"ml_paired": rm(np.log(pml.cpu_ratio_paired.values) - real_cpu),
                               "ml_direct_difference": rm(np.log(pml.cpu_s.values / pml.cpu_normal.values) - real_cpu),
                               "table": rm(tb_cpu - real_cpu)},
            "cpu_ratio_model_error_noise_removed": {
                "ml_direct_difference": rmc(np.log(pml.cpu_s.values / pml.cpu_normal.values) - real_cpu),
                "ml_paired": rmc(np.log(pml.cpu_ratio_paired.values) - real_cpu),
                "table": rmc(tb_cpu - real_cpu)},
        }
        out["ml"]["rel_sigma"] = ml.rel_sigma
        out["table"]["rel_sigma"] = tb.rel_sigma
    return out


def _row_job(r) -> Job:
    return Job(id="x", kind=r.kind, units=float(r.units), threads=int(r.threads))
