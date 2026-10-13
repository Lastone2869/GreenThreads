"""Backend selection with capability-based fallbacks."""
from __future__ import annotations

import sys

from .base import Backend, Capabilities, Mode, PowerStatus, AppliedControls  # noqa: F401


class NullBackend(Backend):
    """Applies nothing. Used for dry runs and when OS control is unavailable."""
    name = "null"

    def __init__(self, caps: Capabilities | None = None, power=None):
        self._caps = caps or Capabilities(os_name="null", logical_cpus=4, physical_cores=4,
                                          priority=True, affinity=True, ecoqos=True)
        self._power = power or PowerStatus(on_ac=True, percent=None, source="null")

    def capabilities(self):
        return self._caps

    def power_status(self):
        return self._power

    def apply(self, pid, mode, caps):
        return AppliedControls(mode=Mode(mode), actions=[f"(dry-run) {Mode(mode).value}"])


def get_backend(name: str = "auto") -> Backend:
    if name == "null":
        return NullBackend()
    if name in ("auto", "windows") and sys.platform == "win32":
        from .windows import WindowsBackend
        return WindowsBackend()
    if name == "windows":
        raise RuntimeError("Windows backend requested on a non-Windows platform")
    from .posix import PosixBackend
    return PosixBackend()
