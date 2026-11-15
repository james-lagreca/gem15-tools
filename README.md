# GEM-15 tools

Stream Google's GEM-15 15 m global elevation model into **ArcGIS Pro** and
**QGIS** for mapping active faults, without downloading tiles. Zoom to your
area, press one button, and get a hillshade and a local relief model of the
bare-earth DTM, read on the fly from Google's public cloud tiles.

Version 0.1. Tested on Windows with ArcGIS Pro 3.4 and QGIS 3.28, mostly by
automated tests without the program windows; QGIS 4, macOS and Linux are
untested.

## Before you map faults

GEM-15 is new, and it is not lidar. Before reading a feature as a scarp:

- **The DTM is predicted by machine learning**, not measured. It is
  unreliable under trees and buildings. The *canopy check* layer shows where
  (the DSM is over 3 m above the DTM there).
- **Straight features along tile edges or UTM zone boundaries** (every 6
  degrees of longitude) can be seams between acquisitions, not scarps. The
  *tile edges* layer shows where tiles meet.
- **Regional vertical offsets** of -4.8 to +1.9 m against lidar are reported.
  They do not affect a scarp's height unless a seam lies between its two
  sides.
- **Heights are on the EGM96 geoid**, close to mean sea level but not AHD.
- **Water plus a 9 m buffer is masked**; the live local relief model is blank
  within one radius of it (use a clip there).
- **At 15 m**, features narrower than about 45 m (3 pixels), steps lower than
  1-2 m, and offsets under about 30 m are beyond what the data support.
- The description of the data is a **preprint**, not yet peer reviewed.

## Requirements

- Internet access to `storage.googleapis.com` (the bucket is public: no
  account or key).
- **ArcGIS Pro 3.x** (tested on 3.4). Mosaic datasets need a Standard or
  Advanced licence; with Basic the tools use VRT files instead. The best
  local relief model uses Spatial Analyst or Image Analyst; without either it
  falls back to a convolution.
- **QGIS 3.28 or later** (tested on 3.28).

## Install

Download from the repository's **Releases** page (not the green *Code*
button, whose zip holds the source code):

- **ArcGIS Pro:** unzip `gem15-arcgis-<version>.zip` to a folder you will
  keep. In Pro: *Catalog* pane > *Toolboxes* > right-click > *Add Toolbox* >
  `GEM15Tools.pyt` (keep the `gem15core` folder and the `.pyt.xml` files
  beside it). To have it in every new project: right-click the toolbox >
  *Add To Favorites*, then on the Catalog pane's *Favorites* tab right-click
  it > *Add To New Projects*. To update, replace the folder's contents, then
  right-click the toolbox > *Refresh*.
- **QGIS:** *Plugins > Manage and Install Plugins... > Install from ZIP* >
  `gem15-<version>.zip` > *Install Plugin*. A **GEM-15** toolbar appears (if
  not: *View > Toolbars > GEM-15*).

## Quick start: ArcGIS Pro

1. Zoom the map to your area (up to about 6 x 6 degrees).
2. Open **GEM-15 for Current View** (in the GEM-15 Tools toolbox) and press
   **Run**. No inputs are needed. It connects to the bucket, adds the DTM
   for the view, and adds a group with a live hillshade, a local relief model
   (LRM) and the tile edges. Hover over any parameter's (i) for help.
3. Zoom in to 1:50,000 or closer to see the LRM. Its colours are fixed:
   blue is lower and red higher than the surroundings, by up to 2.5 m.
4. To light a fault from the side, open *Hillshade* in the tool, choose
   *Across a fault trend* and give the trend. To check for trees, tick
   *Canopy check* under *Layers*.
5. To measure, draw a line across a scarp with **Swath Profile**. Give a
   distance range on each side, away from the scarp face, for its height.
   For a local GeoTIFF, use **Clip for Analysis** (in *Step by step*).

The **Step by step** toolset has the same work as separate tools with every
option: *Add GEM-15 for Extent* (mosaic dataset, separate layers or VRT;
any extent), *Terrain Stack*, *Clip for Analysis*, *Connect to GEM-15*.

**What to expect.** Zoomed out over more than about 20 tiles, a mosaic
dataset shows tile outlines only: zoom in. Raw elevation looks flat until a
hillshade is on top; the elevation layer sits switched off at the bottom of
the GEM-15 group. A VRT draws more slowly than a mosaic dataset, especially
the first zoomed-out view of a large area.

## Quick start: QGIS

1. Zoom the map to your area (up to about 6 x 6 degrees).
2. Press the **GEM-15** toolbar button: the DTM for the view, as a
   hillshade.
3. From the button's menu (the arrow), add the **live LRM** (zoom to
   1:50,000 or closer to see it) or the **canopy check**. The *LRM radius*
   submenu has 150 m (narrow scarps, offset channels), 300 m and 1000 m
   (broad scarps). Tile edges are added automatically.
4. Change the sun live in *Layer Properties > Symbology* (single or
   multidirectional, azimuth, altitude, z factor).
5. **Processing > GEM-15** has *Build GEM-15 VRT* (any area), *Clip GEM-15
   to GeoTIFF*, *Terrain derivatives from a clip* and *Swath profile across a
   scarp*.

More: `qgis/README.md`.

## The same thing in each program

| | ArcGIS Pro | QGIS |
| --- | --- | --- |
| One click for the view | GEM-15 for Current View | GEM-15 toolbar button |
| Any area | Add GEM-15 for Extent | Build GEM-15 VRT |
| Live hillshade, slope, LRM | Terrain Stack (slope too) | toolbar (hillshade, LRM) |
| Local copy | Clip for Analysis | Clip GEM-15 to GeoTIFF |
| Derivatives from a copy | Clip for Analysis, *Also write* | Terrain derivatives from a clip |
| Swath profile | Swath Profile | Swath profile across a scarp |
| Download limit | 2000 MB, 0 = none | 2000 MB, 0 = none |
| LRM drawn at | 1:50,000 and closer | 1:50,000 and closer |

## Glossary

- **DTM**: digital terrain model, the bare ground (here predicted by machine
  learning). **DSM**: digital surface model, the top of trees and
  buildings. The DSM has five bands: the surface, its 5th, 50th and 95th
  percentiles, and the spread of heights within a pixel.
- **LRM**: local relief model, each pixel's height minus the mean height
  within a radius. Scarps show as a red-blue pair along their foot and crest.
- **Hillshade**: the surface lit by a low sun. Multidirectional lighting uses
  several suns.
- **COG**: Cloud-Optimised GeoTIFF, a file GIS software can read part of
  over the internet. GEM-15 is stored as COGs.
- **VRT**: a small text file listing the remote tiles; it holds no
  elevation, so keep it with your project.
- **.acs**: ArcGIS Pro's connection file for a cloud bucket (here anonymous,
  read-only).
- **Mosaic dataset**: an ArcGIS layer joining many rasters into one, kept in
  a geodatabase.
- **DRA**: ArcGIS's dynamic range adjustment, a colour stretch recomputed
  for what is on screen. Only raw elevation uses it here; terrain layers have
  fixed ranges, so a scarp looks the same in every view.
- **UTM zone**: GEM-15 tiles are on the 6-degree UTM grid of their area.
  Tiles in one zone keep their native 15 m grid; across zones they are
  reprojected (*warped*, resampled bilinearly) to one projection: GDA2020 /
  GA LCC in Australia, NZTM2000 in New Zealand, a projection centred on the
  area elsewhere.
- **EGM96**: the geoid the heights are measured from.

## Troubleshooting

- **"Could not fetch" or "did not return the GEM-15 catalogue".** Open the
  catalogue address in a web browser:
  https://storage.googleapis.com/climate-and-sustainability/gem-15/stac/gem-15/collection.json
  If a Wi-Fi login page or a block page appears, sign in or ask your IT
  staff. Behind a proxy, QGIS's own proxy setting (*Settings > Options >
  Network*) is used, and in Pro the Windows proxy.
- **Old or wrong tiles.** The catalogue is saved for 30 days in
  `%LOCALAPPDATA%\gem15tools\stac`. Delete that folder, or run *Connect to
  GEM-15* with *Clear Saved Tile Catalogue*.
- **Nothing drawn.** Check you are on land between about 61 S and 84 N, and
  for the LRM and canopy check, zoomed in to 1:50,000 or closer.
- **Where outputs go.** ArcGIS: a `gem15` folder in the project's home
  folder (VRTs, layer files, clips, profiles); `gem15.acs` in the home
  folder. QGIS: `<project name>_gem15` beside a saved project, else a folder
  in your QGIS profile (*Open the GEM-15 VRT folder* in the toolbar menu).
- **ArcGIS Pro after updating the toolbox.** Right-click the toolbox >
  *Refresh*; if a tool still behaves the old way, restart Pro.
- Details of any error: Pro's *Geoprocessing History* (*View Details*);
  QGIS's *View > Panels > Log Messages*, *GEM-15* tab.

## Credit and citation

GEM-15 is licensed CC BY 4.0. On maps, credit it as:

> Elevation: GEM-15, produced by Google (CC BY 4.0)

and cite: Batchu et al. (2026), GEM-15, EarthArXiv preprint,
https://doi.org/10.31223/X55N75. The layers these tools add carry the same
credit in their metadata.

The code is licensed under the GNU General Public License v2 or later (see
`LICENSE`). To cite the tools themselves, see `CITATION.cff`.

## What is here

| Folder | Contents |
| --- | --- |
| `arcgis/` | ArcGIS Pro Python toolbox `GEM15Tools.pyt`, its help files and its copy of the core. |
| `qgis/gem15/` | QGIS plugin, with its copy of the core. |
| `core/gem15core/` | Shared core: catalogue lookup with a local cache, coordinate systems, VRT writer, GDAL warp and clip, swath profiles. |
| `cli/gem15_vrt.py` | Command line: build a VRT (and optionally a GeoTIFF clip) for an area. |
| `extras/gem15_fault_viewer.js` | Earth Engine Code Editor viewer (syntax-checked only). |

Command line, from the OSGeo4W Shell or the ArcGIS Pro Python Command
Prompt, in the unzipped source folder (`--bbox` is W S E N in degrees):

```
python cli/gem15_vrt.py --bbox 172.6 -41.95 173.1 -41.65 --out wairau_dtm.vrt
```

Development, tests and the release steps: `CONTRIBUTING.md`.
