import math
import struct

import numpy as np
import pytest

from greenthread.accounting import BatteryCarbonLedger, attributable_carbon_g
from greenthread.backends.base import Capabilities, CpuSetInfo, Backend, Mode
from greenthread.backends.windows import parse_cpu_set_buffer
from greenthread.carbon import CarbonSignal, CSVReplaySource, StaticSource
from greenthread.config import load_config


# ---------------------------------------------------------------- windows ABI
def _cpuset_entry(id_, group, lp, core, eff):
    body = struct.pack("<IIIHBBBBBBI Q", 32, 0, id_, group, lp, core, 0, 0, eff, 0, 0, 0)
    return body


def test_parse_cpu_set_buffer_hybrid():
    raw = b"".join(_cpuset_entry(256 + i, 0, i, i // 2 if i < 4 else i - 2, 1 if i < 4 else 0) for i in range(8))
    sets = parse_cpu_set_buffer(raw)
    assert len(sets) == 8
    assert sets[0].id == 256 and sets[5].logical_index == 5
    assert {s.efficiency_class for s in sets} == {0, 1}


def test_half_core_selection_one_logical_per_core():
    sets = [CpuSetInfo(256 + i, 0, i, i // 2, 0) for i in range(8)]  # 4C/8T
    caps = Capabilities("Windows 11", logical_cpus=8, physical_cores=4, cpu_sets=sets, affinity=True)
    lps = Backend.half_core_logicals(caps)
    assert lps == [0, 2]                      # cores 0 and 1, one thread each


def test_supported_modes_homogeneous_vs_hybrid():
    caps = Capabilities("Windows 11", logical_cpus=8, physical_cores=4, ecoqos=True, priority=True, affinity=True)
    assert Mode.ECORES not in caps.supported_modes()
    caps.heterogeneous = True
    assert Mode.ECORES in caps.supported_modes()
    one = Capabilities("x", logical_cpus=1, physical_cores=1, priority=True, affinity=True)
    assert Mode.HALF not in one.supported_modes()


# ---------------------------------------------------------------- carbon
def test_csv_replay_time_mapping_and_compression(trace_csv):
    src = CSVReplaySource(trace_csv, "2020-01-01T00:00:00", seconds_per_trace_hour=10)
    assert src.true_value(0) == 500.0
    assert src.true_value(10 * 11) == 100.0          # 11:00 in trace = 110 engine seconds
    assert src.true_value(10 * 15) == 500.0
    assert src.forecast(0, [105, 150]) == [100.0, 500.0]


def test_forecast_noise_deterministic(trace_csv):
    a = CSVReplaySource(trace_csv, None, 3600, forecast_noise_frac=0.3, seed=3)
    b = CSVReplaySource(trace_csv, None, 3600, forecast_noise_frac=0.3, seed=3)
    offs = [3600 * k for k in range(1, 10)]
    assert a.forecast(0, offs) == b.forecast(0, offs)
    assert a.forecast(0, offs) != CSVReplaySource(trace_csv, None, 3600).forecast(0, offs)


def test_outage_serves_stale_value_and_confidence_decays(trace_csv):
    src = CSVReplaySource(trace_csv, "2020-01-01T09:00:00", 3600, outages=[(1800, 99999)])
    s = src.latest(7200)
    assert not s.available and s.observed_age_s == pytest.approx(5400)
    sig = CarbonSignal(src, prior=300, freshness_half_life_s=3600, adaptive=False)
    eff, sd = sig.plan_curve(7200, [0.0])
    c = 0.5 ** 1.5
    # the forecast issued before the outage still covers 'now' (noise-free here),
    # but it is trusted less the older it is
    assert eff[0] == pytest.approx(c * src.forecast(7200, [0.0])[0] + (1 - c) * 300)
    assert sd[0] > 0


def test_unaware_signal_trusts_raw(trace_csv):
    src = CSVReplaySource(trace_csv, None, 3600, outages=[(0, 1e9)])
    sig = CarbonSignal(src, prior=300, uncertainty_aware=False)
    eff, sd = sig.plan_curve(5000, [0.0, 36000.0])
    assert sd.sum() == 0 and eff[0] == src.true_value(0)


# ---------------------------------------------------------------- accounting
def test_ledger_weighted_intensity():
    led = BatteryCarbonLedger(None, charge_efficiency=0.8, init_intensity=0)
    led.initialise(stored_wh=10, intensity=500)
    led.charge(10, grid_intensity=200)               # stored at 200/0.8 = 250
    assert led.intensity == pytest.approx((10 * 500 + 10 * 250) / 20)
    led.discharge(5)
    assert led.state.stored_wh == pytest.approx(15)
    assert led.intensity == pytest.approx(375)       # discharge does not change intensity


def test_ledger_observe_charging_vs_discharging():
    led = BatteryCarbonLedger(None, 1.0)
    led.initialise(1, 100)
    led.observe(1000, 2000, on_ac=True, grid_intensity=300)   # +1 Wh charged at 300
    assert led.intensity == pytest.approx(200)
    led.observe(2000, 1500, on_ac=False, grid_intensity=999)  # discharge: grid irrelevant
    assert led.intensity == pytest.approx(200)


def test_attribution_single_path():
    # On AC: adapter loss + grid intensity; on battery: ledger only (no double count)
    assert attributable_carbon_g(1.0, True, 400, 999, 0.8) == pytest.approx(1.0 / 0.8 / 1000 * 400)
    assert attributable_carbon_g(1.0, False, 999999, 300, 0.8) == pytest.approx(0.3)
    assert attributable_carbon_g(0.0, True, 400, 300, 0.8) == 0.0


def test_config_rejects_unknown_key(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text("[scheduler]\nnot_a_key = 1\n")
    with pytest.raises(ValueError):
        load_config(p)
    p.write_text("[weights]\ncarbon = 2.0\n[carbon]\nsource='static'\n")
    cfg = load_config(p)
    assert cfg.weights.carbon == 2.0 and cfg.carbon.source == "static"


# ---------------------------------------------------------------- adaptive signal
def _year_trace(tmp_path, name="y.csv"):
    import pandas as pd
    ts = pd.date_range("2020-01-01", periods=24 * 60, freq="1h")
    v = 300 + 150 * np.sin((ts.hour.values - 6) / 24 * 2 * np.pi)       # clean diurnal cycle
    p = tmp_path / name
    pd.DataFrame({"timestamp": ts.strftime("%Y-%m-%dT%H:%M:%S"), "carbon_intensity": v, "zone": "T"}).to_csv(p, index=False)
    return p


def _warm(sig, days=20):
    for h in range(days * 24):
        sig.tick(h * 3600.0)


def test_adaptive_starts_like_fixed_decay(tmp_path):
    src = CSVReplaySource(_year_trace(tmp_path), None, 3600)
    sig = CarbonSignal(src, prior=300, forecast_half_life_h=12, spread=100)
    for lead in (1.0, 6.0, 24.0):
        assert sig.confidence(0, lead * 3600) == pytest.approx(0.5 ** (lead / 12), rel=1e-6)


def test_adaptive_learns_to_trust_a_good_forecast(tmp_path):
    src = CSVReplaySource(_year_trace(tmp_path), None, 3600)
    sig = CarbonSignal(src, prior=300, forecast_half_life_h=12, spread=100)
    c0 = sig.confidence(0, 12 * 3600)
    _warm(sig)
    assert sig.confidence(0, 12 * 3600) > 0.95 > c0
    assert sig.n.sum() > 100


def test_adaptive_learns_to_distrust_a_misleading_forecast(tmp_path):
    src = CSVReplaySource(_year_trace(tmp_path), None, 3600, forecast_shift_h=12)
    sig = CarbonSignal(src, prior=300, forecast_half_life_h=12, spread=100)
    _warm(sig)
    assert sig.confidence(0, 6 * 3600) < 0.2
    # the fallback is the hour-of-day climatology, which here is exactly right
    t = 25 * 24 * 3600.0
    eff, _ = sig.plan_curve(t, [6 * 3600.0])
    assert eff[0] == pytest.approx(src.true_value(t + 6 * 3600), abs=15)


def test_unaware_signal_is_not_learning(tmp_path):
    src = CSVReplaySource(_year_trace(tmp_path), None, 3600)
    sig = CarbonSignal(src, prior=300, uncertainty_aware=False)
    _warm(sig, 3)
    assert sig.n.sum() == 0
