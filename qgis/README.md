# GEM-15 Elevation: QGIS plugin

Streams Google's GEM-15 15 m global elevation (DTM and DSM) into QGIS from
the public Cloud-Optimised GeoTIFFs, without downloading tiles. Meant for
mapping active faults. QGIS 3.28 or later; written to also run on QGIS 4
(Qt6), which is untested. Read
[Before you map faults](../README.md#before-you-map-faults) in the main
README first. An independent project, not affiliated with or endorsed by
Google.

## Install

Download `gem15-qgis-<version>.zip` from the
[latest release](https://github.com/james-lagreca/gem15-tools/releases/latest).
In QGIS: **Plugins > Manage and Install Plugins... > Install from ZIP**,
choose the zip, then **Install Plugin** (QGIS warns that the plugin is from
an untrusted source: choose *Yes*). A **GEM-15** toolbar appears (if not,
**View > Toolbars > GEM-15**), with **Raster > GEM-15** and a **GEM-15**
entry in the Processing Toolbox.

## The toolbar

The button adds the DTM for the current view; its arrow opens the menu:

| Entry | Result |
| --- | --- |
| GEM-15 DTM for current view | DTM of every tile touching the view, as a hillshade (multidirectional, sun 30 degrees). The button's default. |
| GEM-15 DSM for current view | The same for the DSM (first band: the surface). |
| GEM-15 live LRM (DTM) for current view | Local relief model (DTM minus its mean within the radius), blue to red over +/- 2.5 m, computed as QGIS draws. Shown at 1:50,000 and closer. |
| GEM-15 canopy check (DSM minus DTM) for current view | Green where the DSM is over 3 m above the DTM: trees or buildings, where the DTM is the model's guess. Streams the DSM too (5 times the data). Shown at 1:50,000 and closer. |
| LRM radius | 150 m (narrow scarps, offset channels), 300 m (default), 1000 m (broad scarps; slower). |
| Show tile edges (where seams may be) | On: each run adds the tiles' true outlines, dashed and labelled, where seams may look like scarps. |
| Several UTM zones: one layer per zone (no resampling) | Off: one layer, reprojected to the automatic CRS. On: one native-grid layer per zone, in a group. |
| Open the GEM-15 VRT folder; About GEM-15 and credit | Where the files are; credit, citation and licence. |

The work runs in the background (progress bottom right). Views wider than
about 6 x 6 degrees are refused: use the Processing tools. A layer already in the
project is not added again. To change the light, open *Layer Properties >
Symbology*: single or multidirectional sun, azimuth, altitude, z factor.

**Coordinate system.** Tiles in one UTM zone keep their native 15 m grid
(nothing resampled). Across zones: GDA2020 / GA LCC (EPSG:7845) in
Australia, NZTM2000 (EPSG:2193) in New Zealand, else a conformal projection
centred on the area. Reprojecting resamples (bilinear), which smooths scarp
faces: measure on a single-zone clip, or use one layer per zone.

**Files.** VRTs are small XML files pointing at the remote tiles; they hold
no elevation. Saved project: `<project folder>/<project name>_gem15/`.
Unsaved project: a `gem15tools/vrt` folder in your QGIS profile. The tile
edges are in `gem15_tile_edges.gpkg` there.

## Processing tools

In the **Processing Toolbox** (*Processing > Toolbox*, Ctrl+Alt+T), under
**GEM-15**:

* **Build GEM-15 VRT**: any area, DTM or any DSM band, automatic CRS, one
  VRT per zone, or a projected CRS in metres you choose. It can also write a
  live LRM VRT. The DSM's spread band (dsm_std) is coloured, not hillshaded.
* **Clip GEM-15 to GeoTIFF**: a local, compressed copy for analysis. It
  estimates the download first (a DTM block is about 0.9 MB, a DSM block
  about 4.5 MB) and stops above a limit (2000 MB by default; 0 = none).
* **Terrain derivatives from a clip**: hillshade (multidirectional, one sun,
  or across a fault trend; vertical exaggeration), slope in degrees and LRM
  from any projected DEM in metres. Use it on slow connections, for print
  maps, and for LRM zoomed out. Near water, data gaps and the clip's edges
  the LRM averages only part of its circle and is biased: ignore it within
  one radius of them (the live LRM leaves that band blank).
* **Swath profile across a scarp**: samples the DEM in a band along a line
  and summarises each step (lowest, quartiles, median, highest); with a
  distance range on each side, fits a line to each far field and reports
  their vertical separation (VS) halfway between the ranges, with the fit's
  error (a lower bound: the DEM's own error is not included). Writes a CSV
  (with its source, datum and credit in `#` lines) and a PNG plot.

## Things to know

* **Live slope is not offered** in QGIS: it needs an arctangent that GDAL
  3.6's built-in pixel functions lack. Use Terrain derivatives on a clip.
* **Seams across UTM zones.** In a reprojected multi-zone layer a line about
  one pixel wide can appear where one zone's tiles end; it is a straight
  line, so do not map it as a scarp. "One layer per zone" avoids it.
* **GDAL settings.** The plugin's remote-read settings apply only to the
  GEM-15 bucket (GDAL 3.6 and later), so your other rasters keep their
  `.ovr` and `.aux.xml` files. It never changes GDAL's exception setting,
  which other plugins rely on.
* **Errors**: details are in *View > Panels > Log Messages*, *GEM-15* tab.

Development notes, tests and the checks still to do in the QGIS window are
in [CONTRIBUTING.md](../CONTRIBUTING.md). Questions and bug reports:
[Issues](https://github.com/james-lagreca/gem15-tools/issues).
