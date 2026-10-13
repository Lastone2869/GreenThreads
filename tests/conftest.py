import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture
def trace_csv(tmp_path):
    """24 h trace: high (500) except a clean window 10:00-14:00 (100)."""
    ts = pd.date_range("2020-01-01", periods=48, freq="1h")
    v = np.where((ts.hour >= 10) & (ts.hour < 14), 100.0, 500.0)
    p = tmp_path / "trace.csv"
    pd.DataFrame({"timestamp": ts.strftime("%Y-%m-%dT%H:%M:%S"), "carbon_intensity": v,
                  "zone": "TEST"}).to_csv(p, index=False)
    return p


@pytest.fixture
def synthetic_runs():
    rng = np.random.default_rng(0)
    rows = []
    for i in range(400):
        kind = rng.choice(["hash", "compress", "matmul"])
        units = float(rng.choice([100, 200, 400, 800]))
        threads = int(rng.choice([1, 2, 4]))
        mode = rng.choice(["NORMAL", "ECO", "HALF", "ECO_HALF"])
        bg = float(rng.choice([0.0, 0.5]))
        rate = {"hash": 0.002, "compress": 0.006, "matmul": 0.01}[kind]
        par = min(threads, 2 if "HALF" in mode else 4)
        rt = units * rate / par * (1.4 if "ECO" in mode else 1.0) * (1 + 0.8 * bg) * rng.lognormal(0, 0.05)
        rows.append(dict(sample_id=i, kind=kind, units=units, threads=threads, mode=mode, runtime_s=rt,
                         cpu_s=rt * par, bg_load=bg, bg_threads=int(bg > 0), on_ac=1, logical_cpus=4,
                         physical_cores=4, energy_wh_measured=np.nan, control_failures=""))
    return pd.DataFrame(rows)
