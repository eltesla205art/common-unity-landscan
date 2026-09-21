"""Make a small synthetic LiDAR survey for testing: 2×2 LAZ tiles, 200 m each.

Terrain: gentle slope + rolling hills; features: a 1.2 m burial-style mound, a
sunken track (0.6 m deep, 4 m wide), an old field bank; vegetation: scattered
trees 5–18 m tall and a wood; a small pond as a data gap.
Tile A/B carry ground classification (like USGS 3DEP); tiles C/D are unclassified
so the built-in filter is exercised.
"""

import sys
from pathlib import Path

import laspy
import numpy as np

rng = np.random.default_rng(7)
out = Path(sys.argv[1] if len(sys.argv) > 1 else "synthetic_tiles")
out.mkdir(parents=True, exist_ok=True)
TILE = 200
DENSITY = 4  # points per m² ground

def ground_z(x, y):
    z = 30 + 0.02 * x + 2.0 * np.sin(x / 60) * np.cos(y / 80)
    # mound at (150, 150), r=12, h=1.2
    r = np.hypot(x - 150, y - 150); z += 1.2 * np.clip(1 - (r / 12) ** 2, 0, None)
    # sunken track along y = 0.6x + 20, 4 m wide, 0.6 m deep
    d = np.abs(y - (0.6 * x + 20)) / np.sqrt(1 + 0.36); z -= 0.6 * np.clip(1 - (d / 2) ** 2, 0, None)
    # field bank along x = 260, 3 m wide, 0.4 m high
    z += 0.4 * np.clip(1 - (np.abs(x - 260) / 1.5) ** 2, 0, None)
    return z

trees = rng.uniform(0, 2 * TILE, size=(140, 2))
wood_center = np.array([320, 90])

for ti, (ox, oy, classified, name) in enumerate([(0, 0, True, "tile_A_classified"), (TILE, 0, True, "tile_B_classified"),
                                                 (0, TILE, False, "tile_C_raw"), (TILE, TILE, False, "tile_D_raw")]):
    n = TILE * TILE * DENSITY
    x = rng.uniform(ox, ox + TILE, n); y = rng.uniform(oy, oy + TILE, n)
    z = ground_z(x, y) + rng.normal(0, 0.05, n)
    cls = np.full(n, 2, dtype=np.uint8)
    # pond: drop points
    pond = np.hypot(x - 60, y - 300) < 15
    x, y, z, cls = x[~pond], y[~pond], z[~pond], cls[~pond]
    # trees: canopy returns above ground
    cx, cy, cz, cc = [], [], [], []
    for tx, ty in trees:
        if not (ox <= tx < ox + TILE and oy <= ty < oy + TILE):
            continue
        h = rng.uniform(5, 18); rad = rng.uniform(2, 5); k = int(rad * rad * 12)
        a = rng.uniform(0, 2 * np.pi, k); rr = rad * np.sqrt(rng.uniform(0, 1, k))
        px, py = tx + rr * np.cos(a), ty + rr * np.sin(a)
        pz = ground_z(px, py) + h * (0.4 + 0.6 * rng.uniform(0, 1, k)) * (1 - (rr / rad) ** 2 * 0.5)
        cx.append(px); cy.append(py); cz.append(pz); cc.append(np.full(k, 5, dtype=np.uint8))
    # a wood
    k = 6000
    a = rng.uniform(0, 2 * np.pi, k); rr = 40 * np.sqrt(rng.uniform(0, 1, k))
    px, py = wood_center[0] + rr * np.cos(a), wood_center[1] + rr * np.sin(a)
    inside = (ox <= px) & (px < ox + TILE) & (oy <= py) & (py < oy + TILE)
    px, py = px[inside], py[inside]
    pz = ground_z(px, py) + rng.uniform(3, 15, len(px))
    cx.append(px); cy.append(py); cz.append(pz); cc.append(np.full(len(px), 5, dtype=np.uint8))
    x = np.concatenate([x, *cx]); y = np.concatenate([y, *cy]); z = np.concatenate([z, *cz]); cls = np.concatenate([cls, *cc])
    if not classified:
        cls[:] = 1  # unclassified
    header = laspy.LasHeader(point_format=6, version="1.4")
    header.offsets = [0, 0, 0]; header.scales = [0.01, 0.01, 0.01]
    try:
        header.add_crs(__import__("pyproj").CRS.from_epsg(26917))  # NAD83 / UTM 17N (Florida)
    except Exception:
        pass
    las = laspy.LasData(header)
    las.x, las.y, las.z = x, y, z
    las.classification = cls
    las.write(str(out / f"{name}.laz"))
    print(name, len(x), "points", "classified" if classified else "raw")
