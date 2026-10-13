"""Job specifications and results."""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path


@dataclass
class Job:
    id: str
    kind: str                 # hash | compress | matmul | probe
    units: float              # work size (probe: duration in seconds)
    threads: int = 1
    arrival_s: float = 0.0    # engine time the job is submitted
    deadline_s: float = 1e9   # absolute engine time it must finish by
    deferrable: bool = False  # explicitly allowed to start later
    interactive: bool = False # latency-sensitive: never deferred/throttled

    @property
    def relative_deadline(self) -> float:
        return self.deadline_s - self.arrival_s


@dataclass
class Prediction:
    q50: float               # median runtime (s)
    qhi: float               # upper-quantile runtime (s)
    cpu_s: float             # predicted CPU-seconds
    energy_wh: float         # predicted incremental energy (Wh)
    source: str = "model"


@dataclass
class Decision:
    job_id: str
    start_at: float
    mode: str
    reason: str
    prediction: Prediction | None = None
    cost: dict = field(default_factory=dict)
    candidates_evaluated: int = 0
    decide_ms: float = 0.0


@dataclass
class JobResult:
    job_id: str
    policy: str
    kind: str
    units: float
    threads: int
    deferrable: bool
    interactive: bool
    mode: str
    mode_final: str
    arrival_s: float
    start_s: float
    end_s: float
    runtime_s: float
    cpu_s: float
    deadline_s: float
    deadline_met: bool
    deferred_s: float
    escalated: bool
    pred_q50: float | None
    pred_qhi: float | None
    energy_wh: float                # attributed incremental energy used for carbon
    energy_source: str              # measured-battery | model
    energy_wh_model: float
    carbon_g: float
    intensity_g_per_kwh: float
    on_ac: bool
    decision_reason: str
    decide_ms: float
    controls: list[str] = field(default_factory=list)
    control_failures: list[str] = field(default_factory=list)
    latency: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)


def load_jobs(path: str | Path) -> list[Job]:
    data = json.loads(Path(path).read_text())
    jobs = []
    for i, d in enumerate(data["jobs"] if isinstance(data, dict) else data):
        d = dict(d)
        d.setdefault("id", f"j{i}")
        if "relative_deadline_s" in d:
            d["deadline_s"] = d.get("arrival_s", 0.0) + d.pop("relative_deadline_s")
        jobs.append(Job(**d))
    return jobs
