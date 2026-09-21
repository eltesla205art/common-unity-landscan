"""Command-line entry: `landscan <folder> [options]` — the same pipeline without the window."""

from __future__ import annotations

import argparse
import sys
import threading

from . import APP_NAME, VERSION
from .pipeline import Cancelled, JobSettings, Pipeline, StepError, VisSettings
from .tools import check_environment


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="landscan", description=f"{APP_NAME} {VERSION} — LiDAR tiles to terrain model and archaeological views.")
    p.add_argument("input", nargs="?", help="folder with .laz / .las tiles")
    p.add_argument("-o", "--output", default="", help="folder to create the project in (default: next to the input folder)")
    p.add_argument("-n", "--name", default="", help="project folder name (default: LandScan_<timestamp>)")
    p.add_argument("-r", "--resolution", type=float, default=1.0, help="terrain cell size in the tiles' units (default 1)")
    p.add_argument("--lastools", default=None, help="LAStools bin folder (optional)")
    p.add_argument("--ignore-classes", action="store_true", help="re-classify ground even when tiles already have class 2")
    p.add_argument("--no-hillshade", action="store_true"); p.add_argument("--no-slrm", action="store_true")
    p.add_argument("--no-svf", action="store_true"); p.add_argument("--no-ld", action="store_true")
    p.add_argument("--keep", action="store_true", help="keep intermediate files")
    p.add_argument("--check", action="store_true", help="only check the installed tools and exit")
    p.add_argument("--gui", action="store_true", help="open the desktop window")
    return p


def main(argv=None) -> int:
    # Windows consoles default to a legacy code page; keep the log printable everywhere.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    args = build_parser().parse_args(argv)
    if args.gui or (args.input is None and not args.check):
        from .gui import run_gui
        return run_gui()
    rep = check_environment(args.lastools)
    for line in rep.lines():
        print(line)
    if args.check:
        return 0 if rep.ready else 1
    if not rep.ready:
        print("Missing required packages — see above.")
        return 1
    vis = VisSettings(hillshade=not args.no_hillshade, slrm=not args.no_slrm, svf=not args.no_svf, local_dominance=not args.no_ld)
    s = JobSettings(input_dir=args.input, output_root=args.output, project_name=args.name, resolution=args.resolution,
                    lastools_path=args.lastools, use_existing_classes=not args.ignore_classes, vis=vis, keep_intermediate=args.keep)
    cancel = threading.Event()

    def prog(pct, msg, eta=None):
        eta_s = f"  ~{eta / 60:.0f} min left" if eta and eta > 90 else (f"  ~{eta:.0f}s left" if eta else "")
        print(f"[{pct:5.1f}%] {msg}{eta_s}")

    try:
        out = Pipeline(s, progress=prog, log=print, cancel=cancel).run()
        print(f"\nOutput folder: {out}")
        return 0
    except StepError as e:
        print(f"\nStopped: {e}", file=sys.stderr)
        return 2
    except Cancelled:
        print("\nCancelled.")
        return 130
    except KeyboardInterrupt:
        cancel.set()
        print("\nCancelled.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
