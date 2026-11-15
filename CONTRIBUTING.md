# Contributing

## Layout

| Path | What |
| --- | --- |
| `core/gem15core/` | Shared core. Standard library only (Python 3.9+), except `gdalops` (GDAL) and `profile` (numpy). Edit the core here only. |
| `arcgis/` | ArcGIS Pro toolbox `GEM15Tools.pyt`; `gem15help.py` (hover help text); generated `GEM15Tools*.pyt.xml`; vendored `gem15core/`. |
| `qgis/gem15/` | QGIS plugin; vendored `gem15core/`. |
| `cli/` | Command-line VRT builder. |
| `tools/` | `vendor_core.py`, `build_pyt_help.py`, `package_qgis_plugin.py`, `package_arcgis_toolbox.py`, `qgis_python.py`. |
| `tests/` | pytest suite; `tests/qgis_plugin/` needs QGIS's Python. |

Modules flag their assumptions in their docstrings with short codes (A1 in
the toolbox, S1 in `stac`, G1 in `gdalops`, and so on); comments refer to
them. Keep them out of messages users see.

## Two generated things

- **Vendored core.** After editing `core/gem15core/`, run
  `python tools/vendor_core.py` to copy it into `arcgis/` and `qgis/gem15/`.
  A test fails if the copies drift.
- **Toolbox help.** ArcGIS Pro reads hover help from
  `arcgis/GEM15Tools.<ToolClass>.pyt.xml`. Edit the text in
  `arcgis/gem15help.py`, then run `python tools/build_pyt_help.py`. A test
  fails if a parameter has no help or the files are stale. Renaming a tool
  class changes its file name.

## Tests

ArcGIS Pro's Python has GDAL, numpy and arcpy, so it runs everything except
the QGIS plugin's tests:

```
"<ArcGIS Pro>\bin\Python\envs\arcgispro-py3\python.exe" -m pytest
```

The QGIS plugin's tests use QGIS's own Python. `tools/qgis_python.py` sets
up QGIS Desktop's environment and borrows the pure-Python parts of pytest
from another install (nothing is downloaded):

```
python tools/qgis_python.py --pytest-from "<ArcGIS Pro>\bin\Python\envs\arcgispro-py3\Lib\site-packages" -m pytest tests
```

- Set `GEM15_LIVE=1` to add the tests that read the live, public bucket
  (anonymous, read-only; a few MB).
- Tests marked `arcpy` run the toolbox headless in Pro's Python.
- If an arcpy run stops with exit code -1 and no traceback, suspect an arcpy
  call that leaves native state behind: assigning `layer.metadata` did that
  (the next geoprocessing call killed the process), so the toolbox writes
  layer credits through the CIM instead.
- Without GDAL, arcpy or QGIS (as on GitHub Actions), those tests skip and
  the rest run.

## Releasing

1. Set the version in `core/gem15core/__init__.py` and
   `qgis/gem15/metadata.txt` (keep them equal), and add a `CHANGELOG.md`
   entry.
2. Run `python tools/vendor_core.py` and `python tools/build_pyt_help.py`,
   then both test suites.
3. Tag `v<version>` and push the tag. GitHub Actions
   (`.github/workflows/release.yml`) builds `gem15-<version>.zip` (QGIS) and
   `gem15-arcgis-<version>.zip` (ArcGIS) and attaches them to a release. To
   build them locally: `python tools/package_qgis_plugin.py` and
   `python tools/package_arcgis_toolbox.py` (both write to `dist/`).
4. Upload the QGIS zip to plugins.qgis.org (it needs a login; done by a
   maintainer).

## Checks to do by hand

Headless tests cannot click through the program windows. ArcGIS Pro:
`tests/manual_pro_checks.md`. QGIS:

1. Install from the zip: no error; the GEM-15 toolbar appears; Processing
   shows 4 GEM-15 tools.
2. Victoria, about 144.1 E, 37.5 S, at about 1:250,000: click the button.
   Can you pan while it runs? Do a hillshade and the tile edges appear?
3. Zoom to about 1:25,000 and add the live LRM, then the canopy check. How
   long does a redraw take? Past 1:50,000 both should disappear.
4. Cook Strait, 173.5 to 174.5 E, 41.5 S (UTM 59S and 60S): with the
   per-zone option off, one layer in EPSG:2193; on, two layers in a group.
5. Central Australia, 131.5 to 132.5 E, 25 S: one layer in EPSG:7845.
6. Clip a small area; run Terrain derivatives on it with *Across a fault
   trend*; draw a line and run the Swath profile.
7. Save the project, restart QGIS and reopen it: the layers should load.
8. A local raster's `.ovr` pyramids should still be used after GEM-15 layers
   are added (Layer Properties > Information).

## Not yet tested

- QGIS 4 / Qt6 (the code uses `qgis.PyQt`, scoped enum names and fallbacks
  in `qgis/gem15/compat.py`; a static test checks for Qt5-only patterns).
- The program windows: toolbar, menus, Processing and geoprocessing dialogs,
  hover help display, layer drawing speed.
- macOS and Linux; GDAL older than 3.6.
- Screens that are not 96 dpi (the 1:50,000 LRM limit assumes 96 dpi).
- A proxy that needs a user name and password.
