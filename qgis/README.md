# GEM-15 Elevation: QGIS plugin

Streams Google's GEM-15 15 m global elevation (DTM and DSM) into QGIS from
the public Cloud-Optimised GeoTIFFs, without downloading tiles. Meant for
mapping active faults. QGIS 3.28 or later; written to also run on QGIS 4
(Qt6), which has not been tested yet (see the end of this page).

## Install from a zip

1. Build the zip from the repository root (any Python 3.9+):

   ```
   python tools/package_qgis_plugin.py
   ```

   This writes `dist/gem15-0.1.0.zip`, which holds the plugin, its copy of
   the core and the licence.
2. In QGIS: **Plugins > Manage and Install Plugins... > Install from ZIP**,
   choose the zip, then **Install Plugin**. The plugin is marked
   experimental, which only matters when browsing the online repository.
3. A **GEM-15** toolbar appears (if not, **View > Toolbars > GEM-15**), along
   with **Raster > GEM-15** and a **GEM-15** group in the Processing Toolbox.

For development, copy or link `qgis/gem15` into your profile's
`python/plugins` folder instead (**Settings > User Profiles > Open Active
Profile Folder**).

## What it does

**Toolbar button** (the arrow opens the menu):

| Entry | Result |
| --- | --- |
| GEM-15 DTM for current view | DTM of every tile touching the view, as a multidirectional hillshade (sun altitude 30 degrees). |
| GEM-15 DSM for current view | The same for the DSM (first band). |
| GEM-15 live LRM (DTM) for current view | Local relief model (DTM minus its mean within 300 m), computed as QGIS draws. Drawn only at 1:40,000 and closer. |
| Several UTM zones: one layer per zone | Off: one layer, warped to the automatic CRS. On: one native-grid layer per UTM zone, in a group. |
| Open the GEM-15 VRT folder, About | Where the VRTs are; credit and citation. |

The catalogue lookup runs in a background task (progress bottom right), so
QGIS stays responsive. The first lookup of an area makes a few small
requests; repeats come from a local cache (`%LOCALAPPDATA%\gem15tools\stac`
on Windows, 30 days). Views wider than about 6 x 6 degrees are refused: use
Processing instead.

**Coordinate system.** If all tiles share one UTM zone, they keep their
native 15 m grid and nothing is resampled. Across zones: GDA2020 / GA LCC
(EPSG:7845) in Australia, NZTM2000 (EPSG:2193) in New Zealand, elsewhere a
conformal projection centred on the area. GA LCC has up to about 1.2 % scale
error in central Australia: fine for display, but check it before measuring.

**Where the VRTs go.** These are small XML files that point at the remote
tiles; they hold no elevation. Saved project: `<project folder>/<project
name>_gem15/`. Unsaved project: a `gem15tools/vrt` folder in your QGIS
profile, which persists, so a project saved later still finds them.

**Processing > GEM-15:**

* **Build GEM-15 VRT**: any area, DTM or any DSM band, automatic CRS, one
  VRT per zone, or a CRS you choose. It can also write a live LRM VRT.
* **Clip GEM-15 to GeoTIFF**: a local, compressed copy for analysis. It
  reports the estimated download first (a DTM block is about 0.9 MB, a DSM
  block about 4.5 MB because its 5 bands are stored together) and stops
  above a limit (2000 MB by default).
* **Terrain derivatives from a clip**: multidirectional hillshade, slope in
  degrees and LRM from any projected DEM in metres. Use this on slow
  connections, for print maps, or for LRM at small scales.

## Things to know

* **Live LRM** (trial results in `gem15/lrm_vrt.py`): it matches a numpy
  reference to 1e-7 m. It runs at about 0.4 million pixels per second for the
  300 m radius, which is roughly 1 s per full-HD redraw at 1:25,000 and 2.5 s
  at 1:40,000. GDAL applies the filter only to full-resolution reads, so the
  layer is hidden when zoomed out further. Voids and masked water grow by the
  radius in it; the clip-based LRM does not have this problem.
* **Live slope is not offered.** QGIS has no slope renderer. A VRT could
  compute the two 3 x 3 gradients (as for the LRM), but turning them into
  degrees needs a non-linear step: an arctangent of their magnitude. GDAL
  3.6, as shipped with QGIS 3.28, has no built-in pixel function for that.
  Python pixel functions are off by default for security. It would also
  share the live LRM's zoomed-out limit. So use Terrain derivatives on a
  clip. (Not tried: nested VRTs with GDAL's built-in functions could at
  best give tan(slope).)
* **Seams across UTM zones.** In a warped multi-zone layer, a line about one
  pixel wide can appear where one zone's tiles end and the other zone's take
  over, because bilinear resampling there uses only part of its kernel. On a
  steep synthetic test surface (18 m of relief per pixel) it reached 8 m; on
  real ground it should be much smaller, but it is a straight line, so do
  not map it as a scarp. "One layer per zone" avoids resampling altogether.
* **GDAL settings.** The remote-read options apply only to paths in the
  GEM-15 bucket (GDAL 3.6 and later), so your other rasters keep their
  `.ovr` and `.aux.xml` files. The plugin never changes GDAL's exception
  setting, which other plugins rely on.
* **Data caveats.** The DTM is predicted by machine learning and is
  unreliable under dense canopy; acquisition seams and tile overlaps can
  look like scarps; water plus a 9 m buffer is masked; heights are EGM96
  (EPSG:5773).

## Checks to do in QGIS (v0.1)

1. Install from the zip. There should be no error, the GEM-15 toolbar should
   appear, and Processing should show 3 GEM-15 tools.
2. Victoria, around 144.1 E, 37.5 S, at about 1:250,000: click **GEM-15 DTM
   for current view**. Can you pan while the task runs? Does a hillshade
   appear? How fast are pan and zoom?
3. Zoom in to about 1:25,000 and add the **live LRM**. Does it look right,
   and how long does a redraw take? Zoomed out past 1:40,000 it should
   disappear.
4. Cook Strait, 173.5 to 174.5 E, 41.5 S (UTM 59S and 60S): with the
   per-zone option off you should get one layer in EPSG:2193, and with it
   on, two layers in a group. Look along 174 E for a seam line.
5. Central Australia, 131.5 to 132.5 E, 25 S: you should get one layer in
   EPSG:7845.
6. **Clip GEM-15 to GeoTIFF** on a small area: check the download estimate
   in the log. Then run **Terrain derivatives from a clip** on the result.
7. Save the project, restart QGIS and reopen it: the GEM-15 layers should
   load.
8. A local raster with `.ovr` pyramids should still use them after GEM-15
   layers have been added (Layer Properties > Pyramids).
9. Layer Properties > Metadata should show the GEM-15 credit and citation.

## Tests

Headless tests use QGIS's own Python. `tools/qgis_python.py` sets up the
environment QGIS Desktop uses. QGIS has no pytest, so the script borrows
the pure-Python parts of pytest from another install (here ArcGIS Pro) and
downloads nothing:

```
python tools/qgis_python.py --pytest-from "C:\Program Files\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\Lib\site-packages" -m pytest
```

Add `GEM15_LIVE=1` to include the checks against the live bucket. The tests
cover:

* loading through `qgis.utils`, from the source folder and from the zip;
* the view-to-lon/lat conversion, including across the antimeridian;
* the background task and the layers it adds;
* all three Processing algorithms;
* the live LRM against numpy;
* streaming byte counts.

## Not yet tested

* QGIS 4 / Qt6. Not installed here. The code uses `qgis.PyQt`, scoped enum
  names and fallbacks for renamed QGIS enums (`compat.py`), and a static
  test checks for Qt5-only patterns. It has not been run on Qt6.
* Real clicks in the QGIS window. Headless tests use a stand-in `iface`,
  real message bar and canvas, so the toolbar, menus and dialogs have not
  been clicked through.
* The Processing parameter dialogs. Only `processing.run` has been used.
* Linux and macOS. Also GDAL older than 3.6, where the remote options fall
  back to process-wide and the ones that would affect other data are left
  out.
* Screens that are not 96 dpi, which affects the 1:40,000 LRM limit, and
  6 x 6 degree views or DSM streaming speed on a real connection.
