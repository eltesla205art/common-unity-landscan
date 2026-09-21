"""Environment check: what is installed, what is optional, where LAStools lives."""

from __future__ import annotations

import importlib
import os
import platform
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

LASTOOLS_HINTS = [
    r"C:\LAStools\bin",
    r"C:\LASTools\bin",
    r"C:\Program Files\LAStools\bin",
    r"C:\Program Files (x86)\LAStools\bin",
    str(Path.home() / "LAStools" / "bin"),
    "/opt/LAStools/bin",
    "/usr/local/LAStools/bin",
]

REQUIRED = [
    ("numpy", "numpy", "Number crunching"),
    ("scipy", "scipy", "Filters and interpolation"),
    ("laspy", "laspy", "Reads and writes LAS files"),
    ("lazrs", "lazrs", "Decompresses LAZ files"),
    ("rasterio", "rasterio", "Writes GeoTIFF rasters"),
    ("PIL", "Pillow", "Preview images"),
]
OPTIONAL_GUI = [("customtkinter", "customtkinter", "Desktop window")]


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    required: bool = True


@dataclass
class ToolReport:
    checks: list[Check] = field(default_factory=list)
    lastools: str | None = None

    @property
    def ready(self) -> bool:
        return all(c.ok for c in self.checks if c.required)

    def lines(self) -> list[str]:
        out = []
        for c in self.checks:
            mark = "OK " if c.ok else ("!! " if c.required else "-- ")
            out.append(f"{mark}{c.name}: {c.detail}")
        return out


def find_lastools(user_path: str | None = None) -> str | None:
    """Return a folder that contains lasground(64).exe, or None."""
    candidates = []
    if user_path:
        candidates += [user_path, os.path.join(user_path, "bin")]
    env = os.environ.get("LASTOOLS")
    if env:
        candidates += [env, os.path.join(env, "bin")]
    candidates += LASTOOLS_HINTS
    for exe in ("lasground64", "lasground"):
        found = shutil.which(exe)
        if found:
            candidates.insert(0, str(Path(found).parent))
    for c in candidates:
        if not c:
            continue
        p = Path(c)
        for exe in ("lasground64.exe", "lasground.exe", "lasground64", "lasground"):
            if (p / exe).exists():
                return str(p)
    return None


def lastools_exe(folder: str, name: str) -> str | None:
    for candidate in (f"{name}64.exe", f"{name}.exe", f"{name}64", name):
        p = Path(folder) / candidate
        if p.exists():
            return str(p)
    return None


def check_environment(lastools_path: str | None = None) -> ToolReport:
    rep = ToolReport()
    rep.checks.append(Check("Python", True, f"{platform.python_version()} on {platform.system()} {platform.machine()}"))
    for mod, pkg, why in REQUIRED:
        try:
            m = importlib.import_module(mod)
            ver = getattr(m, "__version__", "")
            rep.checks.append(Check(pkg, True, f"{why} — {ver}".strip(" —")))
        except Exception as e:  # noqa: BLE001
            rep.checks.append(Check(pkg, False, f"{why} — missing ({e.__class__.__name__}). Run: pip install {pkg}"))
    for mod, pkg, why in OPTIONAL_GUI:
        try:
            importlib.import_module(mod)
            rep.checks.append(Check(pkg, True, why, required=False))
        except Exception:  # noqa: BLE001
            rep.checks.append(Check(pkg, False, f"{why} — not installed; the command line still works", required=False))

    lt = find_lastools(lastools_path)
    rep.lastools = lt
    if lt:
        rep.checks.append(Check("LAStools", True, f"found at {lt} — lasground will be used for tiles without ground classes", required=False))
    else:
        rep.checks.append(Check("LAStools", False, "not found (optional). USGS 3DEP tiles are already classified; other tiles use the built-in ground filter.", required=False))

    # Free disk where the temp files go
    try:
        usage = shutil.disk_usage(Path.home())
        free_gb = usage.free / 1e9
        rep.checks.append(Check("Free disk", free_gb > 5, f"{free_gb:.0f} GB free in your home folder" + ("" if free_gb > 5 else " — LiDAR jobs need room; free some space"), required=False))
    except Exception:  # noqa: BLE001
        pass
    return rep


def frozen() -> bool:
    return getattr(sys, "frozen", False)
