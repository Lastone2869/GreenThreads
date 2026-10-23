"""Executive Viva & Lab Review II Dashboard generator."""
from __future__ import annotations

import shutil
from pathlib import Path
from .config import Config, PACKAGE_ROOT


def write(cfg: Config, path: str | Path | None = None) -> Path:
    """Ensure or copy the self-contained HTML dashboard for presentation or demo."""
    src = PACKAGE_ROOT / "docs" / "DASHBOARD.html"
    dest = Path(path) if path else src
    if dest.resolve() != src.resolve():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
    return dest
