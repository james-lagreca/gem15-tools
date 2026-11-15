# GEM-15 tools

Stream Google's GEM-15 15 m global elevation model into ArcGIS Pro and QGIS
for mapping active faults, without downloading whole tiles.

**Status: pre-release (v0.1 in progress).** The shared core, the
command-line VRT builder, the ArcGIS Pro toolbox's four tools and the QGIS
plugin's v0.1 features work and are tested headless (arcpy without the Pro
window; QGIS 3.28 without its window). Checks still to do by hand in the Pro
map view are listed in `tests/manual_pro_checks.md`; QGIS 4 is untested.

## What is here

| Folder | Contents |
| --- | --- |
| `core/gem15core/` | Shared core, standard library only (Python 3.9+): catalogue lookup with a local cache, cell names, CRS choice, VRT writer. `gdalops.py` adds GDAL warp and clip. |
| `arcgis/` | ArcGIS Pro Python toolbox `GEM15Tools.pyt`, with its own copy of the core. |
| `qgis/gem15/` | QGIS plugin: current-view hillshade and live LRM, Processing tools (build VRT, clip, derivatives), with its own copy of the core. Install and use: `qgis/README.md`. |
| `cli/gem15_vrt.py` | Command line: build a VRT (and optionally a GeoTIFF clip) for an area. |
| `extras/gem15_fault_viewer.js` | Earth Engine Code Editor viewer. Syntax-checked only. |
| `tools/vendor_core.py` | Copies the core into the plugin folders. |
| `tools/package_qgis_plugin.py`, `tools/qgis_python.py` | Build the QGIS plugin zip; run QGIS's own Python (for its tests). |

## Quick start (ArcGIS Pro 3.x)

1. Catalog pane > Toolboxes > right-click > Add Toolbox > `arcgis/GEM15Tools.pyt`
   (keep the `gem15core` folder beside it).
2. Run **Connect to GEM-15**. It writes `gem15.acs` to the project home
   folder: an anonymous, read-only connection to the public bucket.
3. Zoom to your area and run **Add GEM-15 For Extent** with the current
   map extent. Output options:
   - *Mosaic dataset* (Standard or Advanced licence): one layer referencing
     the cloud tiles. Leave the name blank and it is named after its
     coordinate system (for example `GEM15_dtm_UTM55S`): running again in
     the same zone adds tiles to it, and an area in another zone gets its own
     mosaic. For one mosaic across Australia, type a name and choose
     GDA2020 / GA LCC (EPSG 7845).
   - *Separate layers* (any licence): one layer per tile.
   - *VRT file* (any licence, no `.acs`): a small GDAL VRT that streams the
     tiles over https.

   Leave the coordinate system blank for the automatic choice: the tiles'
   own UTM zone when they share one (nothing resampled), else GDA2020 / GA
   LCC (EPSG 7845) in Australia, NZTM2000 (EPSG 2193) in New Zealand, else a
   conformal projection centred on the area. Extents across the antimeridian
   work.
4. Run **Terrain Stack (live)** on the new layer: a multidirectional
   hillshade (sun altitude 30 degrees), slope in degrees and a local relief
   model (DEM minus its mean over a 300 m circle), computed as you pan, with
   DRA. The live LRM draws only at 1:100,000 and closer.
5. For measurement, run **Clip for Analysis**: a local GeoTIFF on the tiles'
   native UTM grid, plus optional hillshade, slope and LRM GeoTIFFs. It
   estimates the download first and stops above a limit you set (1000 MB by
   default).

GDA2020 / GA LCC has up to about 1.2 % scale error in central Australia:
fine for display, but clip to a UTM grid for measurements.

## Quick start (QGIS 3.28 or later)

1. Build the plugin zip: `python tools/package_qgis_plugin.py` (writes
   `dist/gem15-<version>.zip`).
2. **Plugins > Manage and Install Plugins... > Install from ZIP**.
3. Zoom to your area and press the **GEM-15** toolbar button: the DTM for the
   current view, as a multidirectional hillshade. The button's menu also has
   the DSM and a live local relief model; **Processing > GEM-15** builds
   VRTs, clips and terrain derivatives for any area.

Full details: `qgis/README.md`.

## Quick start (command line)

From the OSGeo4W Shell (QGIS) or the ArcGIS Pro Python Command Prompt:

```
python cli/gem15_vrt.py --bbox 172.6 -41.95 173.1 -41.65 --out wairau_dtm.vrt
```

Open the `.vrt` in QGIS and use a hillshade renderer. Tiles in one UTM zone
keep their native grid. Across zones the tool picks a projection: GDA2020 /
GA LCC in Australia, NZTM2000 in New Zealand, and a custom conformal
projection centred on the area elsewhere.

## Data, licence and credit

GEM-15 is CC-BY 4.0: "This dataset is produced by Google". Cite Batchu et al.
(2026), EarthArXiv preprint, doi:10.31223/X55N75. Heights are orthometric
on the EGM96 geoid (EPSG 5773).

Caveats for fault mapping: the DTM is predicted by machine learning and is
unreliable under dense canopy; acquisition seams and tile overlaps can look
like scarps; water plus a 9 m buffer is masked; regional vertical offsets of
-4.8 to +1.9 m against lidar are reported in the preprint.

The code is licensed under the GNU General Public License v2 or later (see
`LICENSE`).

## Development

Tests run with pytest. ArcGIS Pro's Python has GDAL and arcpy, so it runs
everything:

```
"<ArcGIS Pro>\bin\Python\envs\arcgispro-py3\python.exe" -m pytest
```

Set `GEM15_LIVE=1` to also run the checks against the live, public bucket.
Tests marked `arcpy` run the toolbox headless in Pro's Python (skipped
elsewhere). The QGIS plugin's tests need QGIS's own Python; see
`qgis/README.md`. Edit the core only in `core/gem15core/`, then run
`python tools/vendor_core.py`.
