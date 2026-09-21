"""The LandScan pipeline: LAZ/LAS tiles -> bare-earth DTM -> archaeological visualizations.

Six steps, mirroring the workflow archaeologists use (LiDARch, RVT), but with no
dependency on QGIS or SAGA. Only Python packages are needed; LAStools is optional.

  1. Read tiles (LAZ is decompressed in memory with lazrs)
  2. Ground classification (existing classes -> LAStools lasground -> built-in filter)
  3. Ground filtering (keep class 2, write one clean LAS per tile)
  4. Terrain model (grid the ground points, fill small gaps, one GeoTIFF)
  5. Merge (one ground point cloud for the whole area)
  6. Visualizations (hillshade, local relief, sky-view factor, local dominance) + report
"""

from __future__ import annotations

import json
import math
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

from . import APP_NAME, VERSION
from .tools import check_environment, lastools_exe

GROUND = 2


class Cancelled(Exception):
    pass


class StepError(Exception):
    """A failure with a plain-language message for the person running the app."""


@dataclass
class VisSettings:
    hillshade: bool = True
    hs_azimuth: float = 315.0
    hs_altitude: float = 35.0
    slrm: bool = True
    slrm_radius_m: float = 20.0
    svf: bool = True
    svf_directions: int = 16
    svf_radius_m: float = 10.0
    local_dominance: bool = True
    ld_min_m: float = 10.0
    ld_max_m: float = 20.0


@dataclass
class JobSettings:
    input_dir: str
    output_root: str = ""          # default: next to the input folder
    project_name: str = ""         # default: LandScan_<timestamp>
    resolution: float = 1.0        # metres per DTM cell
    lastools_path: str | None = None
    use_existing_classes: bool = True
    ground_slope: float = 0.15     # built-in filter: allowed slope (rise/run)
    ground_max_window_m: float = 20.0
    ground_threshold_m: float = 0.5
    vis: VisSettings = field(default_factory=VisSettings)
    keep_intermediate: bool = False

    def resolved_project_dir(self) -> Path:
        root = Path(self.output_root) if self.output_root else Path(self.input_dir).resolve().parent
        name = self.project_name or f"LandScan_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        return root / name


@dataclass
class TileInfo:
    path: str
    points: int = 0
    ground_points: int = 0
    ground_source: str = ""
    minx: float = 0
    miny: float = 0
    maxx: float = 0
    maxy: float = 0
    seconds: float = 0


class Pipeline:
    STEPS = [
        ("Reading tiles", 0, 12),
        ("Classifying ground", 12, 45),
        ("Filtering ground points", 45, 50),
        ("Building the terrain model", 50, 70),
        ("Merging the point cloud", 70, 76),
        ("Rendering visualizations", 76, 98),
        ("Writing the report", 98, 100),
    ]

    def __init__(self, settings: JobSettings, progress=None, log=None, cancel: threading.Event | None = None):
        self.s = settings
        self._progress = progress or (lambda pct, msg, eta=None: None)
        self._log = log or (lambda msg: print(msg))
        self.cancel = cancel or threading.Event()
        self.project_dir = settings.resolved_project_dir()
        self.tiles: list[TileInfo] = []
        self.stats: dict = {}
        self.step_times: dict[str, float] = {}
        self.t0 = 0.0
        self.crs_wkt: str | None = None
        self.warnings: list[str] = []
        self.outputs: dict[str, str] = {}

    # ── plumbing ──────────────────────────────────────────────────────────
    def log(self, msg: str):
        self._log(msg)

    def warn(self, msg: str):
        self.warnings.append(msg)
        self._log("Note: " + msg)

    def progress(self, step_idx: int, frac: float, msg: str):
        if self.cancel.is_set():
            raise Cancelled()
        name, a, b = self.STEPS[step_idx]
        pct = a + (b - a) * max(0.0, min(1.0, frac))
        elapsed = time.time() - self.t0
        eta = None
        if pct > 3:
            eta = elapsed * (100 - pct) / pct
        self._progress(pct, f"{name} — {msg}" if msg else name, eta)

    # ── run ───────────────────────────────────────────────────────────────
    def run(self) -> Path:
        self.t0 = time.time()
        env = check_environment(self.s.lastools_path)
        if not env.ready:
            missing = [c.name for c in env.checks if c.required and not c.ok]
            raise StepError("Some required Python packages are missing: " + ", ".join(missing) + ". Reinstall the app or run: pip install -r requirements.txt")
        self.lastools = env.lastools
        self.log(f"{APP_NAME} {VERSION}")
        self.log(f"Input:  {self.s.input_dir}")
        self.log(f"Output: {self.project_dir}")
        self._setup()
        steps = [self._step_read, self._step_classify, self._step_filter, self._step_dtm, self._step_merge, self._step_vis, self._step_report]
        for i, fn in enumerate(steps):
            name = self.STEPS[i][0]
            self.log("")
            self.log(f"── {i + 1}/7  {name}")
            t = time.time()
            fn(i)
            self.step_times[name] = time.time() - t
            self.log(f"   done in {self.step_times[name]:.1f}s")
        if not self.s.keep_intermediate:
            shutil.rmtree(self.project_dir / "_work", ignore_errors=True)
        self.stats["total_seconds"] = time.time() - self.t0
        self.log("")
        self.log(f"Finished in {self.stats['total_seconds'] / 60:.1f} minutes. Open: {self.project_dir}")
        return self.project_dir

    def _setup(self):
        p = self.project_dir
        for sub in ("terrain", "visualizations", "point_cloud", "ground_tiles", "_work"):
            (p / sub).mkdir(parents=True, exist_ok=True)
        files = sorted([*Path(self.s.input_dir).glob("*.laz"), *Path(self.s.input_dir).glob("*.las"),
                        *Path(self.s.input_dir).glob("*.LAZ"), *Path(self.s.input_dir).glob("*.LAS")])
        # de-duplicate case-insensitive globs on Windows
        seen, uniq = set(), []
        for f in files:
            k = str(f).lower()
            if k not in seen:
                seen.add(k); uniq.append(f)
        if not uniq:
            raise StepError(f"No .laz or .las files were found in {self.s.input_dir}. Download tiles from the USGS National Map into that folder first.")
        self.tiles = [TileInfo(path=str(f)) for f in uniq]
        self.stats["input_files"] = len(uniq)
        self.log(f"   {len(uniq)} tile(s)")

    # ── 1. read ───────────────────────────────────────────────────────────
    def _read(self, path: str):
        import laspy
        try:
            las = laspy.read(path)
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "laz" in msg.lower() or "compress" in msg.lower():
                raise StepError(f"{Path(path).name} is a LAZ file but the decompressor (lazrs) could not read it: {msg}")
            raise StepError(f"Could not read {Path(path).name}: {msg}")
        return las

    def _step_read(self, i):
        n = len(self.tiles)
        total = 0
        for k, t in enumerate(self.tiles):
            self.progress(i, k / n, f"{Path(t.path).name}")
            las = self._read(t.path)
            t.points = len(las.points)
            total += t.points
            h = las.header
            t.minx, t.miny, t.maxx, t.maxy = float(h.mins[0]), float(h.mins[1]), float(h.maxs[0]), float(h.maxs[1])
            if self.crs_wkt is None:
                try:
                    crs = h.parse_crs()
                    if crs is not None:
                        self.crs_wkt = crs.to_wkt()
                        self.stats["crs"] = crs.name
                except Exception:  # noqa: BLE001
                    pass
            self.log(f"   {Path(t.path).name}: {t.points:,} points, {t.maxx - t.minx:.0f} × {t.maxy - t.miny:.0f} units")
        self.stats["input_points"] = total
        if self.crs_wkt is None:
            self.warn("The tiles carry no coordinate system. Outputs will be in the tiles' own units; set the CRS in your GIS when you open them.")
        self.progress(i, 1, f"{total:,} points")

    # ── 2 + 3. ground ─────────────────────────────────────────────────────
    def _step_classify(self, i):
        n = len(self.tiles)
        for k, t in enumerate(self.tiles):
            self.progress(i, k / n, f"{Path(t.path).name}")
            tt = time.time()
            las = self._read(t.path)
            cls = np.asarray(las.classification)
            ground = cls == GROUND
            frac = ground.mean() if len(cls) else 0
            if self.s.use_existing_classes and frac >= 0.02:
                t.ground_source = "existing classification"
            elif self.lastools:
                ground = self._lasground(las, t)
                t.ground_source = "LAStools lasground"
            else:
                ground = self._builtin_ground(las, k, n, i)
                t.ground_source = "built-in morphological filter"
            t.ground_points = int(ground.sum())
            if t.ground_points < 100:
                raise StepError(f"{Path(t.path).name}: only {t.ground_points} ground points were found. The tile may be water, a data gap, or need LAStools for classification.")
            out = las[ground] if hasattr(las, "__getitem__") else None
            if out is None:
                import laspy
                out = laspy.LasData(las.header)
                out.points = las.points[ground]
            out.classification[:] = GROUND
            gpath = self.project_dir / "ground_tiles" / f"ground_{k + 1:03d}.las"
            out.write(str(gpath))
            t.seconds = time.time() - tt
            self.log(f"   {Path(t.path).name}: {t.ground_points:,} ground points ({t.ground_points / max(t.points, 1):.0%}) via {t.ground_source}")
        self.stats["ground_points"] = sum(t.ground_points for t in self.tiles)
        self.progress(i, 1, "")

    def _lasground(self, las, t: TileInfo) -> np.ndarray:
        exe = lastools_exe(self.lastools, "lasground")
        work = self.project_dir / "_work"
        src = work / (Path(t.path).stem + "_in.las")
        dst = work / (Path(t.path).stem + "_ground.las")
        las.write(str(src))
        cmd = [exe, "-i", str(src), "-o", str(dst), "-step", "5", "-spike", "1", "-offset", "0.05"]
        flags = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0
        r = subprocess.run(cmd, capture_output=True, text=True, creationflags=flags)
        if r.returncode != 0 or not dst.exists():
            self.warn(f"lasground failed on {Path(t.path).name} (it may be in demo mode or licensed); using the built-in filter instead. {r.stderr.strip()[:200]}")
            return self._builtin_ground(las, 0, 1, 1)
        if "demo" in (r.stderr + r.stdout).lower():
            self.warn("LAStools is running in demo mode: large tiles may be thinned or slightly distorted. Buy a license for production work.")
        g = self._read(str(dst))
        return np.asarray(g.classification) == GROUND if len(g.points) == len(las.points) else self._builtin_ground(las, 0, 1, 1)

    def _builtin_ground(self, las, k, n, step_idx) -> np.ndarray:
        """Progressive morphological ground filter (after Zhang 2003 / SMRF).

        Grids the lowest return per cell, opens the surface with growing windows,
        rejects cells that jump more than slope × window, interpolates the
        remaining surface and keeps every point within a threshold of it.
        """
        from scipy import ndimage

        x = np.asarray(las.x); y = np.asarray(las.y); z = np.asarray(las.z)
        cell = max(self.s.resolution, 0.5)
        minx, miny = x.min(), y.min()
        cols = int((x.max() - minx) / cell) + 1
        rows = int((y.max() - miny) / cell) + 1
        ci = ((x - minx) / cell).astype(np.int64)
        ri = ((y - miny) / cell).astype(np.int64)
        flat = ri * cols + ci
        zmin = np.full(rows * cols, np.inf)
        np.minimum.at(zmin, flat, z)
        zmin = zmin.reshape(rows, cols)
        empty = ~np.isfinite(zmin)
        surf = self._fill_nearest(np.where(empty, np.nan, zmin))
        keep = np.ones_like(surf, dtype=bool)
        prev = surf.copy()
        w = 1
        max_w = int(self.s.ground_max_window_m / cell)
        while w <= max_w:
            size = 2 * w + 1
            opened = ndimage.grey_opening(prev, size=(size, size), mode="nearest")
            thr = self.s.ground_slope * w * cell + self.s.ground_threshold_m * 0.5
            bad = (prev - opened) > thr
            keep &= ~bad
            prev = opened
            w = w * 2 if w < 4 else w + 4
            self.progress(step_idx, (k + min(w / max_w, 1)) / n, f"built-in ground filter, window {w * cell:.0f} m")
        ground_surf = self._fill_nearest(np.where(keep & ~empty, zmin, np.nan))
        ground_surf = ndimage.uniform_filter(ground_surf, size=3, mode="nearest")
        dz = z - ground_surf[ri, ci]
        return np.abs(dz) <= self.s.ground_threshold_m

    @staticmethod
    def _fill_smooth(a: np.ndarray, max_iter: int = 50) -> np.ndarray:
        """Grow known values into gaps one cell per pass (normalised 3×3 averaging).

        Produces a smooth, artefact-free fill for ponds and small data holes;
        gaps wider than 2×max_iter cells stay nodata.
        """
        from scipy import ndimage
        out = a.copy()
        for _ in range(max_iter):
            nan = np.isnan(out)
            if not nan.any():
                break
            w = (~nan).astype(np.float64)
            v = np.where(nan, 0.0, out)
            sv = ndimage.uniform_filter(v, size=3, mode="constant")
            sw = ndimage.uniform_filter(w, size=3, mode="constant")
            grow = nan & (sw > 0)
            if not grow.any():
                break
            out[grow] = sv[grow] / sw[grow]
        return out

    @staticmethod
    def _fill_nearest(a: np.ndarray, max_dist: int | None = None) -> np.ndarray:
        from scipy import ndimage
        mask = np.isnan(a)
        if not mask.any():
            return a
        if mask.all():
            return np.zeros_like(a)
        dist, (ri, ci) = ndimage.distance_transform_edt(mask, return_distances=True, return_indices=True)
        out = a[ri, ci]
        if max_dist is not None:
            out = np.where(dist > max_dist, np.nan, out)
        return out

    def _step_filter(self, i):
        # Ground tiles were written during classification; verify them here.
        files = sorted((self.project_dir / "ground_tiles").glob("ground_*.las"))
        if len(files) != len(self.tiles):
            raise StepError("Some ground tiles are missing after classification. Re-run the job.")
        self.log(f"   {len(files)} ground-only tiles written")
        self.progress(i, 1, "")

    # ── 4. DTM ────────────────────────────────────────────────────────────
    def _step_dtm(self, i):
        res = self.s.resolution
        files = sorted((self.project_dir / "ground_tiles").glob("ground_*.las"))
        bounds = []
        import laspy
        for f in files:
            with laspy.open(str(f)) as fh:
                h = fh.header
                bounds.append((float(h.mins[0]), float(h.mins[1]), float(h.maxs[0]), float(h.maxs[1])))
        minx = min(b[0] for b in bounds); miny = min(b[1] for b in bounds)
        maxx = max(b[2] for b in bounds); maxy = max(b[3] for b in bounds)
        cols = int(math.ceil((maxx - minx) / res)) + 1
        rows = int(math.ceil((maxy - miny) / res)) + 1
        if rows * cols > 60_000_000:
            raise StepError(f"The terrain model would be {cols:,} × {rows:,} cells at {res} m. Raise the resolution (e.g. 2 m) or process fewer tiles at once.")
        acc = np.zeros(rows * cols, dtype=np.float64)
        cnt = np.zeros(rows * cols, dtype=np.int32)
        for k, f in enumerate(files):
            self.progress(i, 0.7 * k / len(files), f"gridding {f.name}")
            g = self._read(str(f))
            x = np.asarray(g.x); y = np.asarray(g.y); z = np.asarray(g.z)
            ci = ((x - minx) / res).astype(np.int64)
            ri = ((maxy - y) / res).astype(np.int64)   # north-up rows
            ok = (ci >= 0) & (ci < cols) & (ri >= 0) & (ri < rows)
            flat = ri[ok] * cols + ci[ok]
            np.add.at(acc, flat, z[ok]); np.add.at(cnt, flat, 1)
        with np.errstate(invalid="ignore", divide="ignore"):
            dtm = np.where(cnt > 0, acc / np.maximum(cnt, 1), np.nan).reshape(rows, cols).astype(np.float32)
        empty = int(np.isnan(dtm).sum())
        self.progress(i, 0.8, "filling gaps")
        max_gap = int(50 / res)   # fill gaps up to 50 m; leave larger holes as nodata
        dtm_out = self._fill_smooth(dtm.astype(np.float64), max_iter=max_gap).astype(np.float32)
        self.dtm = dtm_out
        self.transform = (minx, maxy, res)
        remaining = int(np.isnan(dtm_out).sum())
        self.stats.update({"dtm_cols": cols, "dtm_rows": rows, "dtm_resolution": res, "dtm_cells_filled": empty - remaining, "dtm_cells_nodata": remaining,
                           "extent": [float(minx), float(miny), float(maxx), float(maxy)]})
        path = self.project_dir / "terrain" / "terrain_model.tif"
        self._write_tif(path, dtm_out)
        self.outputs["terrain_model"] = str(path)
        self._write_png(self.project_dir / "terrain" / "terrain_model.png", dtm_out)
        self.log(f"   {cols:,} × {rows:,} cells at {res} m; {empty - remaining:,} gap cells filled, {remaining:,} left empty")
        self.progress(i, 1, "")

    def _write_tif(self, path: Path, arr: np.ndarray):
        import rasterio
        from rasterio.transform import from_origin
        minx, maxy, res = self.transform
        kw = dict(driver="GTiff", height=arr.shape[0], width=arr.shape[1], count=1, dtype="float32",
                  transform=from_origin(minx, maxy, res, res), nodata=-9999.0, compress="deflate", tiled=True)
        if self.crs_wkt:
            kw["crs"] = rasterio.crs.CRS.from_wkt(self.crs_wkt)
        data = np.where(np.isnan(arr), -9999.0, arr).astype(np.float32)
        with rasterio.open(path, "w", **kw) as dst:
            dst.write(data, 1)

    @staticmethod
    def _write_png(path: Path, arr: np.ndarray, lo_pct=2, hi_pct=98, max_px=2400):
        from PIL import Image
        a = np.array(arr, dtype=np.float64)
        valid = np.isfinite(a)
        if not valid.any():
            return
        lo, hi = np.percentile(a[valid], [lo_pct, hi_pct])
        if hi <= lo:
            hi = lo + 1
        img = np.clip((a - lo) / (hi - lo), 0, 1)
        img = np.where(valid, img, 0)
        im = Image.fromarray((img * 255).astype(np.uint8), mode="L")
        if max(im.size) > max_px:
            im.thumbnail((max_px, max_px))
        im.save(path)

    # ── 5. merge ──────────────────────────────────────────────────────────
    def _step_merge(self, i):
        import laspy
        files = sorted((self.project_dir / "ground_tiles").glob("ground_*.las"))
        first = self._read(str(files[0]))
        header = laspy.LasHeader(point_format=first.header.point_format, version=first.header.version)
        header.offsets = first.header.offsets
        header.scales = first.header.scales
        try:
            for vlr in first.header.vlrs:
                header.vlrs.append(vlr)
        except Exception:  # noqa: BLE001
            pass
        out = self.project_dir / "point_cloud" / "ground_merged.las"
        n = 0
        with laspy.open(str(out), mode="w", header=header) as w:
            for k, f in enumerate(files):
                self.progress(i, k / len(files), f.name)
                g = self._read(str(f))
                w.write_points(g.points)
                n += len(g.points)
        self.outputs["ground_merged"] = str(out)
        self.stats["merged_points"] = n
        self.log(f"   {n:,} ground points in one file")
        self.progress(i, 1, "")

    # ── 6. visualizations ─────────────────────────────────────────────────
    def _step_vis(self, i):
        from .vendor import rvt_vis as rvt
        v = self.s.vis
        dem = self.dtm.astype(np.float64)
        res = self.s.resolution
        vis_dir = self.project_dir / "visualizations"
        jobs = []
        if v.hillshade:
            jobs.append(("hillshade", lambda: rvt.hillshade(dem, res, res, sun_azimuth=v.hs_azimuth, sun_elevation=v.hs_altitude, no_data=np.nan)))
        if v.slrm:
            jobs.append(("local_relief_model", lambda: rvt.slrm(dem, radius_cell=max(1, int(round(v.slrm_radius_m / res))), no_data=np.nan)))
        if v.svf:
            jobs.append(("sky_view_factor", lambda: rvt.sky_view_factor(dem, res, compute_svf=True, compute_opns=False, compute_asvf=False,
                                                                        svf_n_dir=v.svf_directions, svf_r_max=max(1, int(round(v.svf_radius_m / res))), no_data=np.nan)["svf"]))
        if v.local_dominance:
            jobs.append(("local_dominance", lambda: rvt.local_dominance(dem, min_rad=max(1, int(round(v.ld_min_m / res))), max_rad=max(2, int(round(v.ld_max_m / res))),
                                                                        rad_inc=1, angular_res=15, observer_height=1.7, no_data=np.nan)))
        if not jobs:
            self.warn("No visualizations were selected; only the terrain model was produced.")
        for k, (name, fn) in enumerate(jobs):
            self.progress(i, k / max(len(jobs), 1), name.replace("_", " "))
            t = time.time()
            try:
                arr = np.asarray(fn(), dtype=np.float32)
            except MemoryError:
                raise StepError(f"Ran out of memory rendering {name}. Try a coarser resolution (2 m) or fewer tiles.")
            arr = np.where(np.isnan(self.dtm), np.nan, arr)
            self._write_tif(vis_dir / f"{name}.tif", arr)
            lo, hi = (1, 99) if name != "sky_view_factor" else (2, 99.5)
            self._write_png(vis_dir / f"{name}.png", arr, lo, hi)
            self.outputs[name] = str(vis_dir / f"{name}.tif")
            self.log(f"   {name.replace('_', ' ')}: {time.time() - t:.1f}s")
        self.progress(i, 1, "")

    # ── 7. report ─────────────────────────────────────────────────────────
    def _step_report(self, i):
        s = self.s
        st = self.stats
        lines = [
            f"{APP_NAME} {VERSION} — technical report",
            f"Generated {datetime.now():%Y-%m-%d %H:%M} on {platform.node()} ({platform.system()} {platform.release()})",
            "",
            "INPUT",
            f"  Folder:        {s.input_dir}",
            f"  Tiles:         {st.get('input_files')}",
            f"  Points:        {st.get('input_points', 0):,}",
            f"  Coordinate system: {st.get('crs', 'not recorded in the tiles')}",
            "",
            "GROUND CLASSIFICATION",
        ]
        for t in self.tiles:
            lines.append(f"  {Path(t.path).name}: {t.ground_points:,} / {t.points:,} ground ({t.ground_points / max(t.points, 1):.0%}) — {t.ground_source}")
        if self.lastools:
            lines.append(f"  LAStools: {self.lastools} (lasground -step 5 -spike 1 -offset 0.05)")
        lines += [
            f"  Built-in filter settings: slope {s.ground_slope}, max window {s.ground_max_window_m} m, threshold {s.ground_threshold_m} m",
            "",
            "TERRAIN MODEL",
            f"  File:          terrain/terrain_model.tif",
            f"  Resolution:    {st.get('dtm_resolution')} units per cell (mean of ground returns per cell)",
            f"  Size:          {st.get('dtm_cols'):,} × {st.get('dtm_rows'):,} cells",
            f"  Extent:        {st.get('extent')}",
            f"  Gap filling:   nearest-neighbour up to 50 units, 3×3 smoothing on filled cells; {st.get('dtm_cells_filled', 0):,} filled, {st.get('dtm_cells_nodata', 0):,} left as nodata (-9999)",
            "",
            "MERGED POINT CLOUD",
            f"  File:          point_cloud/ground_merged.las ({st.get('merged_points', 0):,} ground points, class 2)",
            "",
            "VISUALIZATIONS (Relief Visualization Toolbox algorithms, ZRC SAZU, Apache-2.0)",
        ]
        v = s.vis
        if v.hillshade: lines.append(f"  hillshade.tif           sun azimuth {v.hs_azimuth}°, elevation {v.hs_altitude}°")
        if v.slrm: lines.append(f"  local_relief_model.tif  radius {v.slrm_radius_m} m (DEM minus its {v.slrm_radius_m} m mean)")
        if v.svf: lines.append(f"  sky_view_factor.tif     {v.svf_directions} directions, search radius {v.svf_radius_m} m")
        if v.local_dominance: lines.append(f"  local_dominance.tif     radius {v.ld_min_m}–{v.ld_max_m} m, 15° angular step, observer 1.7 m")
        lines += ["  Each .tif has a matching .png preview (2–98 % stretch) for viewing without GIS software.", "", "TIMING"]
        for k, val in self.step_times.items():
            lines.append(f"  {k:<32} {val:7.1f} s")
        lines.append(f"  {'Total':<32} {st.get('total_seconds', 0):7.1f} s")
        if self.warnings:
            lines += ["", "NOTES"] + [f"  - {w}" for w in self.warnings]
        lines += ["", "HOW TO READ THE RESULTS",
                  "  Open the GeoTIFFs in QGIS (free) or any GIS and overlay the parcel boundary. Look for water (drainage, pooling, old channels),",
                  "  the built past (foundations, wells, roads, cemeteries) and buildable ground (flat, dry, quietly overlooked spots).",
                  "  Public LiDAR shows the land on the flight date, not today. Walk the land too.",
                  "", "Common Unity Initiative — https://barnarium.com/#lidar"]
        rep = self.project_dir / "technical_report.txt"
        rep.write_text("\n".join(lines), encoding="utf-8")
        (self.project_dir / "settings.json").write_text(json.dumps(asdict(s), indent=2), encoding="utf-8")
        self._write_html_report()
        self.outputs["report"] = str(rep)
        self.progress(i, 1, "")

    def _write_html_report(self):
        vis_dir = self.project_dir / "visualizations"
        cards = []
        for name, title, blurb in [
            ("terrain/terrain_model", "Terrain model", "Bare earth, higher is brighter."),
            ("visualizations/hillshade", "Hillshade", "Sunlight from a set angle. Slopes, terraces, the big shape of the land."),
            ("visualizations/local_relief_model", "Local relief model", "Small bumps and dips with the broad landform removed — plough lines, platforms, ditches, paths."),
            ("visualizations/sky_view_factor", "Sky view factor", "How much sky each point sees. Hollows dark, ridges bright, whatever the light."),
            ("visualizations/local_dominance", "Local dominance", "How much each point overlooks its neighbours — low mounds, banks, embankments."),
        ]:
            png = self.project_dir / f"{name}.png"
            if png.exists():
                cards.append(f'<figure><img src="{name}.png" alt="{title}"><figcaption><b>{title}</b><br>{blurb}</figcaption></figure>')
        html = f"""<!doctype html><html><head><meta charset="utf-8"><title>{APP_NAME} — {self.project_dir.name}</title>
<style>body{{margin:0;background:#0B0F0C;color:#EFEADB;font:15px/1.5 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;padding:32px}}
h1{{font-weight:400;letter-spacing:.02em;margin:0 0 4px}} .k{{color:#8FBE3A;font-size:11px;letter-spacing:.2em;text-transform:uppercase}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(360px,1fr));gap:18px;margin-top:24px}}
figure{{margin:0;background:#10150F;border:1px solid rgba(240,235,221,.14);padding:10px}} img{{width:100%;display:block;image-rendering:auto}}
figcaption{{padding:10px 4px 2px;color:#B9B7A6;font-size:13px}} b{{color:#EFEADB}} pre{{background:#10150F;padding:16px;overflow:auto;font-size:12px;color:#B9B7A6}}
a{{color:#F5C463}}</style></head><body>
<div class="k">Common Unity Initiative · LandScan</div><h1>{self.project_dir.name}</h1>
<p>{self.stats.get('input_files')} tile(s), {self.stats.get('input_points', 0):,} points → {self.stats.get('merged_points', 0):,} ground points → {self.stats.get('dtm_cols', 0):,} × {self.stats.get('dtm_rows', 0):,} terrain cells at {self.s.resolution} m.</p>
<div class="grid">{''.join(cards)}</div>
<h2 class="k" style="margin-top:32px">Technical report</h2><pre>{(self.project_dir / 'technical_report.txt').read_text(encoding='utf-8')}</pre>
<p><a href="https://barnarium.com/#lidar">How we use these views — barnarium.com</a></p></body></html>"""
        (self.project_dir / "report.html").write_text(html, encoding="utf-8")
        self.outputs["report_html"] = str(self.project_dir / "report.html")
