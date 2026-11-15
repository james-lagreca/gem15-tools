# GEM-15 tools

Stream Google's GEM-15 15 m global elevation model into ArcGIS Pro and QGIS
for mapping active faults, without downloading whole tiles.

**Status: pre-release (v0.1 in progress).** The shared core and the
command-line VRT builder work and are tested; the ArcGIS Pro toolbox has one
tool so far; the QGIS plugin has its v0.1 features (see `qgis/README.md`).

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
The QGIS plugin's tests need QGIS's own Python; see `qgis/README.md`.
Edit the core only in `core/gem15core/`, then run `python tools/vendor_core.py`.
