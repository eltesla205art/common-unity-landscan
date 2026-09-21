# Common Unity LandScan

**See the ground before we buy it.** Point LandScan at a folder of LiDAR tiles and it
produces a bare-earth terrain model, four archaeological views (hillshade, local relief,
sky view factor, local dominance), a merged ground point cloud, and a technical report —
in one click, with nothing else to install.

Built for the Common Unity Initiative's site-search work: https://barnarium.com/#lidar

## What's different from other LiDAR tools

- **No QGIS, no SAGA, no plugins.** Everything runs inside the app (Python + numpy/scipy,
  laspy/lazrs for LAZ, rasterio for GeoTIFF, RVT's algorithms for the views).
- **Uses the ground classes you already have.** USGS 3DEP tiles come classified; LandScan
  trusts them. Unclassified tiles go through a built-in progressive morphological ground
  filter — or through LAStools `lasground` if you have it (optional).
- **Reports you can open anywhere.** Every GeoTIFF gets a PNG preview, and `report.html`
  shows all of them with the technical report — no GIS needed to look at results.
- **Windows and Mac.** One-file downloads for both; the command line runs on Linux too.

## Download

Releases: https://github.com/eltesla205art/common-unity-landscan/releases

- Windows 10/11: unzip and run `Common Unity LandScan.exe`. SmartScreen may warn the first
  time (the app is not code-signed); choose *More info → Run anyway*.
- macOS: unzip, drag `Common Unity LandScan.app` to Applications, right-click → Open the first time.

## Get free LiDAR for any U.S. parcel

1. Open https://apps.nationalmap.gov/downloader/
2. Draw a box around the land. Under *Data*, tick **Elevation Source Data (3DEP) – Lidar Point Cloud (LPC)**.
3. Search products, add the tiles that cover the box to the cart, download the `.laz` files
   into one folder. Tiles are usually 1 km squares of 50–300 MB; a 100-acre parcel is 1–4 tiles.

## Run it

**Window:** open the app → choose the tile folder → *Start processing*. The tool check at
the top right says whether everything is in place. When it finishes, *Open report*.

**Command line** (same pipeline):

```bash
pip install -r requirements.txt
python main.py path/to/tiles                 # results go next to the tiles
python main.py path/to/tiles -o ~/Desktop -n ParcelA -r 1.0 --no-svf
python main.py --check                       # only check the installed tools
```

## What you get

```
LandScan_20260921_1412/
├── terrain/terrain_model.tif  (+ .png)   bare-earth DTM, mean of ground returns per cell
├── visualizations/
│   ├── hillshade.tif  (+ .png)           sun azimuth 315°, elevation 35°
│   ├── local_relief_model.tif (+ .png)   DTM minus its 20 m mean
│   ├── sky_view_factor.tif (+ .png)      16 directions, 10 m radius
│   └── local_dominance.tif (+ .png)      10–20 m radius
├── point_cloud/ground_merged.las         all ground points, one file
├── ground_tiles/ground_NNN.las           per-tile ground points
├── technical_report.txt                  what ran, with what settings, timings, notes
├── report.html                           all previews + the report in one page
└── settings.json                         the exact settings used
```

## How we read the results

Open the GeoTIFFs in QGIS (free) or any GIS and overlay the parcel boundary. Look for
**water** (drainage, pooling, old channels), **the built past** (foundations, wells, roads,
cemeteries — things a title search misses) and **buildable ground** (flat, dry, quietly
overlooked spots for homes and the commons barn). Public LiDAR shows the land on the flight
date, not today. Walk the land too.

## Building the app yourself

```bash
pip install -r requirements.txt pyinstaller
pyinstaller LandScan.spec          # dist/Common Unity LandScan(.exe|.app)
```

GitHub Actions builds both platforms on every `v*` tag and attaches them to a release.

## Credits

Terrain visualizations use the algorithms of the **Relief Visualization Toolbox** (ZRC SAZU
and University of Ljubljana, Apache-2.0; `landscan/vendor/`). The six-step workflow follows
**LiDARch** by M. Carrero-Pazos (MIT). Ground filtering after Zhang et al. 2003 / Pingel et
al. 2013. MIT license — see LICENSE.

Common Unity Initiative · Together we build. Together we thrive. Together we rise.
