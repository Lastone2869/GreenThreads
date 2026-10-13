"""Energy and carbon accounting.

Accounting rule (one physical path per joule, so nothing is double counted):

  On AC:       C = (E_dc / eta_adapter) [Wh] / 1000 * I_grid(t)          [g]
  On battery:  C = (E_dc / eta_charge)  [Wh] / 1000 * I_ledger           [g]

I_ledger is the energy-weighted carbon intensity of the charge currently stored
in the battery (the *Battery Carbon Ledger*). Running on battery is therefore not
"zero carbon": its emissions happened when the battery was charged. The battery
objective B is a separate *resource* objective (fraction of remaining stored
energy consumed), not a second carbon term.

E_dc is the workload-attributable (incremental) energy: measured device energy
over the job minus idle baseline power x duration.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, asdict
from pathlib import Path


@dataclass
class LedgerState:
    stored_wh: float = 0.0          # estimated energy currently in the battery
    intensity: float = 0.0          # gCO2e/kWh of that stored energy (at the wall)
    total_charged_wh: float = 0.0
    total_discharged_wh: float = 0.0
    initialised: bool = False


class BatteryCarbonLedger:
    """Tracks the carbon intensity of energy stored in the battery.

    Charging dE (DC Wh stored) at grid intensity I raises the ledger:
        I_new = (S*I_old + dE*I/eta_charge) / (S + dE)
    Discharging leaves the intensity unchanged and reduces S.
    """

    def __init__(self, path: str | Path | None, charge_efficiency: float = 0.85,
                 init_intensity: float = 700.0):
        self.path = Path(path) if path else None
        self.eta = charge_efficiency
        self.state = LedgerState(intensity=init_intensity)
        self._lock = threading.Lock()
        if self.path and self.path.exists():
            try:
                self.state = LedgerState(**json.loads(self.path.read_text()))
            except Exception:
                pass

    def initialise(self, stored_wh: float, intensity: float):
        with self._lock:
            if not self.state.initialised:
                self.state.stored_wh = stored_wh
                self.state.intensity = intensity
                self.state.initialised = True
                self._save()

    def charge(self, dc_wh: float, grid_intensity: float):
        if dc_wh <= 0:
            return
        with self._lock:
            s = self.state
            wall_int = grid_intensity / self.eta  # gCO2e per kWh *stored*
            s.intensity = (s.stored_wh * s.intensity + dc_wh * wall_int) / (s.stored_wh + dc_wh)
            s.stored_wh += dc_wh
            s.total_charged_wh += dc_wh
            self._save()

    def discharge(self, dc_wh: float):
        if dc_wh <= 0:
            return
        with self._lock:
            self.state.stored_wh = max(0.0, self.state.stored_wh - dc_wh)
            self.state.total_discharged_wh += dc_wh
            self._save()

    @property
    def intensity(self) -> float:
        """gCO2e per kWh of DC energy drawn from the battery (charge losses included)."""
        return self.state.intensity

    def _save(self):
        if self.path:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(json.dumps(asdict(self.state)))
            except OSError:
                pass

    def observe(self, prev_mwh: float | None, cur_mwh: float | None, on_ac: bool, grid_intensity: float):
        """Feed consecutive battery capacity readings (mWh) to keep the ledger current."""
        if prev_mwh is None or cur_mwh is None:
            return
        d = (cur_mwh - prev_mwh) / 1000.0
        if d > 0 and on_ac:
            self.charge(d, grid_intensity)
        elif d < 0:
            self.discharge(-d)


def attributable_carbon_g(energy_wh: float, on_ac: bool, grid_intensity: float,
                          ledger_intensity: float, adapter_eff: float) -> float:
    if energy_wh <= 0:
        return 0.0
    if on_ac:
        return energy_wh / adapter_eff / 1000.0 * grid_intensity
    # ledger intensity already includes charge losses (see BatteryCarbonLedger.charge)
    return energy_wh / 1000.0 * ledger_intensity


@dataclass
class PowerModel:
    """Utilisation-based fallback: P = idle + sum over busy cores of per-core power.

    Coefficients default to placeholders and should be calibrated per machine with
    `greenthread calibrate` (fits DischargeRate vs. CPU utilisation on battery).
    """
    idle_w: float = 6.0
    per_core_w_normal: float = 4.5
    per_core_w_eco: float = 2.6

    def incremental_w(self, busy_cores: float, eco: bool) -> float:
        return busy_cores * (self.per_core_w_eco if eco else self.per_core_w_normal)

    def to_dict(self):
        return asdict(self)
