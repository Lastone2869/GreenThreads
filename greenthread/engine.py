"""Runtime engine: admits jobs, asks the policy for (start, mode), launches the
workload processes, applies native controls before they run, monitors them,
escalates when a deadline is at risk, and accounts energy and carbon."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import psutil

from .accounting import BatteryCarbonLedger, PowerModel, attributable_carbon_g
from .backends.base import Backend, Capabilities, Mode, PowerStatus
from .config import Config, PACKAGE_ROOT
from .jobs import Decision, Job, JobResult
from .model import Ctx
from .policy import Policy, PolicyContext
from .telemetry import PowerSampler


def _busy_cpu_seconds() -> float:
    ct = psutil.cpu_times()
    return sum(ct) - ct.idle - getattr(ct, "iowait", 0.0)


@dataclass
class _Running:
    job: Job
    decision: Decision
    proc: subprocess.Popen
    start_t: float
    on_ac: bool
    mode_cur: str
    controls: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    escalated_at: float | None = None
    sys_busy0: float = 0.0
    end_t: float | None = None
    output: str = ""
    sys_busy1: float = 0.0


class Engine:
    def __init__(self, cfg: Config, backend: Backend, policy: Policy, signal, source,
                 ledger: BatteryCarbonLedger, power_model: PowerModel, sampler: PowerSampler | None = None,
                 log_path: str | Path | None = None, verbose: bool = True):
        self.cfg, self.backend, self.policy = cfg, backend, policy
        self.signal, self.source, self.ledger = signal, source, ledger
        self.pm, self.sampler = power_model, sampler
        self.caps: Capabilities = backend.capabilities()
        allowed = set(cfg.scheduler.allow_modes)
        self.modes = [m.value for m in self.caps.supported_modes() if m.value in allowed]
        self.log_path = Path(log_path) if log_path else None
        self.verbose = verbose
        self._power: PowerStatus | None = None
        self._power_t = -1e9
        self._prev_mwh: float | None = None
        self.t0 = time.monotonic()

    # ------------------------------------------------------------------ utils
    def now(self) -> float:
        return time.monotonic() - self.t0

    def log(self, msg: str):
        if self.verbose:
            print(f"[{self.now():8.2f}s] {msg}", flush=True)

    def _jlog(self, rec: dict):
        if self.log_path:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.log_path, "a") as fh:
                fh.write(json.dumps(rec, default=str) + "\n")

    def power(self) -> PowerStatus:
        if self.sampler and self.sampler.latest():
            s = self.sampler.latest()
            ps = PowerStatus(s.on_ac, s.percent, s.remaining_mwh, None, s.discharge_mw, "sampler")
        elif self._power is None or self.now() - self._power_t > 30:
            ps = self.backend.power_status()
            self._power, self._power_t = ps, self.now()
        else:
            ps = self._power
        if ps.remaining_mwh is not None:
            if self._prev_mwh is not None:
                self.ledger.observe(self._prev_mwh, ps.remaining_mwh, ps.on_ac, self.source.true_value(self.now()))
            self._prev_mwh = ps.remaining_mwh
        return ps

    def _ctx(self) -> PolicyContext:
        ps = self.power()
        mctx = Ctx(self.caps.logical_cpus, self.caps.physical_cores,
                   psutil.cpu_percent(interval=None) / 100.0, ps.on_ac)
        return PolicyContext(self.now(), ps.on_ac, None if ps.percent is None else ps.percent / 100.0,
                             self.ledger.intensity, self.signal, mctx, self.modes)

    # --------------------------------------------------------------- launching
    def _launch(self, job: Job, dec: Decision) -> _Running:
        env = dict(os.environ)
        for k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
            env[k] = str(job.threads)
        env["PYTHONPATH"] = str(PACKAGE_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        proc = subprocess.Popen([sys.executable, "-m", "greenthread.workload", job.kind, str(job.units),
                                 str(job.threads)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                text=True, env=env, cwd=str(PACKAGE_ROOT), creationflags=flags)
        ready = proc.stdout.readline().strip()
        if ready != "READY":
            proc.kill()
            raise RuntimeError(f"workload {job.id} failed to start: {ready!r}")
        mode = Mode(dec.mode)
        applied = self.backend.apply(proc.pid, mode, self.caps)
        if applied.failures and mode != Mode.NORMAL:
            self.log(f"WARN {job.id}: control failures {applied.failures} -> continuing in degraded mode")
        ps = self.power()
        r = _Running(job, dec, proc, self.now(), ps.on_ac, dec.mode, applied.actions, applied.failures,
                     sys_busy0=_busy_cpu_seconds())
        proc.stdin.write("GO\n")
        proc.stdin.flush()

        def waiter():
            r.output = proc.stdout.read()
            proc.wait()
            r.end_t = self.now()
            r.sys_busy1 = _busy_cpu_seconds()
        threading.Thread(target=waiter, daemon=True).start()
        self.log(f"start {job.id:<10} mode={dec.mode:<8} {dec.reason} {applied.actions}")
        return r

    def _maybe_escalate(self, r: _Running):
        if not self.cfg.scheduler.escalate or r.escalated_at is not None or r.mode_cur == "NORMAL":
            return
        pred = r.decision.prediction
        if pred is None or self.policy.predictor is None:
            return
        now = self.now()
        elapsed = now - r.start_t
        frac = min(0.95, elapsed / max(pred.q50, 1e-6))
        remaining = (1 - frac) * pred.qhi
        if now + remaining > r.job.deadline_s - self.cfg.scheduler.safety_margin_s:
            res = self.backend.apply(r.proc.pid, Mode.BOOST, self.caps)
            r.escalated_at, r.mode_cur = now, "BOOST"
            r.controls += res.actions
            r.failures += res.failures
            self.log(f"ESCALATE {r.job.id}: deadline at risk -> {res.actions}")

    # --------------------------------------------------------------- finishing
    def _finish(self, r: _Running) -> JobResult:
        out = {}
        for line in reversed(r.output.strip().splitlines()):
            try:
                out = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
        dur = max(r.end_t - r.start_t, 1e-6)
        cpu_s = float(out.get("cpu_s", 0.0))
        eco_mode = r.decision.mode in ("ECO", "ECO_HALF")
        eco_frac = 0.0
        if eco_mode:
            eco_frac = 1.0 if r.escalated_at is None else (r.escalated_at - r.start_t) / dur
        w = eco_frac * self.pm.per_core_w_eco + (1 - eco_frac) * self.pm.per_core_w_normal
        e_model = cpu_s * w / 3600.0
        e_meas = None
        if self.sampler and self.sampler.idle_w is not None and not r.on_ac:
            dev = self.sampler.device_energy_wh(r.start_t + self.t0, r.end_t + self.t0)
            if dev is not None:
                share = min(1.0, cpu_s / max(r.sys_busy1 - r.sys_busy0, 1e-6))
                e_meas = max(0.0, dev - self.sampler.idle_w * dur / 3600.0) * share
        energy, src = (e_meas, "measured-battery") if e_meas is not None else (e_model, "model")
        if r.on_ac:
            inten = self.source.mean_true(r.start_t, r.end_t)
        else:
            inten = self.ledger.intensity
        carbon = attributable_carbon_g(energy, r.on_ac, inten, self.ledger.intensity,
                                       self.cfg.energy.adapter_efficiency)
        p = r.decision.prediction
        res = JobResult(
            job_id=r.job.id, policy=self.policy.name, kind=r.job.kind, units=r.job.units, threads=r.job.threads,
            deferrable=r.job.deferrable, interactive=r.job.interactive, mode=r.decision.mode,
            mode_final=r.mode_cur, arrival_s=r.job.arrival_s, start_s=r.start_t, end_s=r.end_t,
            runtime_s=float(out.get("wall_s", dur)), cpu_s=cpu_s, deadline_s=r.job.deadline_s,
            deadline_met=r.end_t <= r.job.deadline_s + 1e-6, deferred_s=max(0.0, r.start_t - r.job.arrival_s),
            escalated=r.escalated_at is not None, pred_q50=p.q50 if p else None, pred_qhi=p.qhi if p else None,
            energy_wh=energy, energy_source=src, energy_wh_model=e_model, carbon_g=carbon,
            intensity_g_per_kwh=inten, on_ac=r.on_ac, decision_reason=r.decision.reason,
            decide_ms=r.decision.decide_ms, controls=r.controls, control_failures=r.failures,
            latency={k: out[k] for k in ("p50_ms", "p95_ms", "p99_ms", "mean_ms") if k in out})
        self.log(f"done  {r.job.id:<10} {res.runtime_s:7.2f}s met={res.deadline_met} "
                 f"E={energy*1000:.2f}mWh({src}) C={carbon*1000:.2f}mg")
        self._jlog({"event": "done", **res.to_dict()})
        return res

    # ------------------------------------------------------------------- loop
    def run(self, jobs: list[Job], timeout_s: float | None = None) -> list[JobResult]:
        self.t0 = time.monotonic()
        psutil.cpu_percent(interval=None)
        pending = sorted(jobs, key=lambda j: j.arrival_s)
        planned: dict[str, tuple[Job, Decision, float]] = {}
        running: list[_Running] = []
        results: list[JobResult] = []
        sc = self.cfg.scheduler
        replan_every = max(1.0, sc.defer_step_s / 2)
        try:
            while pending or planned or running:
                now = self.now()
                if timeout_s and now > timeout_s:
                    raise TimeoutError("engine timeout")
                # admit
                while pending and pending[0].arrival_s <= now:
                    job = pending.pop(0)
                    dec = self.policy.decide(job, self._ctx())
                    planned[job.id] = (job, dec, now)
                    self._jlog({"event": "decision", "t": now, "job": job.id, "mode": dec.mode,
                                "start_at": dec.start_at, "reason": dec.reason, "cost": dec.cost,
                                "decide_ms": dec.decide_ms})
                    if dec.start_at > now + 1e-6:
                        self.log(f"plan  {job.id:<10} defer to t={dec.start_at:.1f}s mode={dec.mode} ({dec.reason})")
                # receding-horizon replanning of deferred jobs
                for jid, (job, dec, planned_at) in list(planned.items()):
                    if dec.start_at > now and now - planned_at >= replan_every:
                        nd = self.policy.decide(job, self._ctx())
                        # commitment rule: a replan may start a job *earlier* (better or
                        # less certain information), never later -> no procrastination
                        if nd.start_at > dec.start_at:
                            planned[jid] = (job, dec, now)
                            continue
                        planned[jid] = (job, nd, now)
                        if abs(nd.start_at - dec.start_at) > 1e-3 or nd.mode != dec.mode:
                            self._jlog({"event": "replan", "t": now, "job": jid, "mode": nd.mode,
                                        "start_at": nd.start_at, "reason": nd.reason})
                # start due jobs (interactive jobs bypass the concurrency limit)
                busy = sum(1 for r in running if not r.job.interactive)
                for jid, (job, dec, _) in sorted(planned.items(), key=lambda kv: kv[1][1].start_at):
                    if dec.start_at <= now and (job.interactive or busy < sc.max_concurrent):
                        running.append(self._launch(job, dec))
                        del planned[jid]
                        busy += 0 if job.interactive else 1
                # monitor
                for r in list(running):
                    if r.end_t is not None:
                        results.append(self._finish(r))
                        self.backend.cleanup(r.proc.pid)
                        running.remove(r)
                    else:
                        self._maybe_escalate(r)
                time.sleep(sc.tick_s)
        finally:
            for r in running:
                if r.proc.poll() is None:
                    r.proc.kill()
        return results
