# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""
GEM15Tools.pyt - ArcGIS Pro Python toolbox for Google GEM-15 (15 m) elevation.

Tools:
  GEM-15 for Current View  (start here; no inputs needed)
       Zoom to your area and run it. It connects to the bucket, adds the
       elevation for the view (a mosaic dataset with a Standard or Advanced
       licence, a VRT with Basic) and a live hillshade and local relief model
       in one group, with the tile edges. Rarely needed options are folded
       away under "Options".
  Swath Profile
       Summarises the DEM in a band along a line drawn across a scarp, and
       estimates the scarp height from the far-field surfaces either side.
  Step by step (the same work in separate tools, with every option):
    Connect to GEM-15       makes gem15.acs, an anonymous, read-only cloud
                            storage connection (the other tools make it
                            themselves when needed).
    Add GEM-15 for Extent   adds the tiles for an extent (blank = the view)
                            as a mosaic dataset (named after its coordinate
                            system when the name is blank, so a later run in
                            the same zone adds to it), separate layers, or a
                            VRT file that streams them over https (A8).
    Terrain Stack           live raster-function layers on any DEM layer:
                            hillshade, slope and local relief model (LRM).
    Clip for Analysis       a local GeoTIFF of an extent, after estimating
                            the download, with optional derivative GeoTIFFs.

Outputs (VRTs, layer files, clips, profiles) go to a "gem15" folder in the
project's home folder unless you choose another.

Install: Catalog pane > Toolboxes > right-click > Add Toolbox > this file.
Keep the "gem15core" folder next to this file: the toolbox imports it.
After updating the toolbox folder, right-click the toolbox > Refresh: a
changed gem15core is re-imported (restarting Pro also works).

Data: CC-BY 4.0, "This dataset is produced by Google". Cite Batchu et al.
(2026), EarthArXiv preprint, doi:10.31223/X55N75. Heights are EGM96.

Assumptions (flagged; verify if something looks wrong):
 A1. Your .acs connection points at bucket "climate-and-sustainability"
     with folder "gem-15". If it was made without the folder, the tool
     detects that and adds "gem-15" to the paths itself.
 A2. Catalogue layout, names and asset URLs as checked on 2026-11-15 in
     both hemispheres (see gem15core.cells and gem15core.stac):
     https://storage.googleapis.com/climate-and-sustainability/gem-15/
         <group>/<cell>/<dtm|dsm>/zall/mosaic.tif
 A3. Mosaic datasets should honour the COGs' NaN nodata, so overlapping
     tiles fill each other's gaps. Not tested. If you see steps along tile
     overlaps, try Mosaic Method = Closest to Center on the layer's Data tab
     before reading them as landforms.
 A4. No overviews, pyramids or statistics are built (that would read the
     data), so display relies on the COGs' internal overviews and on DRA.
 A5. Extents are converted to WGS 84 lon/lat by projecting points along
     their outline, so extents crossing the antimeridian (180 degrees) work.
     Datum shifts (GDA2020, NZGD2000 vs WGS 84, under 2 m) are ignored.
 A6. ArcGIS will not add rasters that fall outside the valid extent of the
     mosaic dataset's coordinate system (reported on Esri Community as the
     cause of a bare ERROR 999999). A UTM mosaic therefore only accepts tiles
     in or next to its own zone (+/-1 zone tolerance is my assumption).
     Zones come from each tile's own EPSG code in the catalogue. Leave the
     coordinate system blank for the automatic choice: one UTM zone if the
     tiles share it, else GDA2020 / GA LCC (EPSG 7845) in Australia,
     NZTM2000 (EPSG 2193) in New Zealand, else a conformal projection
     centred on the extent (gem15core.crs). GDA94/GDA2020 MGA zones are
     checked as the UTM zones they share numbers with. Leave the mosaic name
     blank too and it is named after that coordinate system, so a second
     area in a distant zone gets its own mosaic instead of failing.
 A7. "Connect to GEM-15" checks the connection by opening one known tile
     (Victoria, cell w144sm38).
 A8. ArcGIS reads GEM-15 VRTs through its own GDAL. Headless arcpy (Pro 3.4)
     reads them with pixels identical to GDAL, including VRTs warped across
     UTM zones; display in the Pro map view is still to be confirmed.
 A9. Live layers: Pro may compute raster functions from full-resolution
     pixels even when zoomed out (headless arcpy does, for the LRM's focal
     mean), which would read every full-resolution block in view, or at
     screen resolution, which would widen a radius given in cells. The live
     LRM is therefore drawn only at 1:LIVE_LRM_MIN_SCALE and closer
     (gem15core.terrain T6; the same limit as the QGIS plugin).
 A13. Terrain layers use fixed display ranges (gem15core.terrain T5), not
     DRA, set as custom statistics (statsType "GlobalStats") so Pro does not
     compute statistics from the cloud. Raw elevation keeps DRA.
 A14. Swath Profile reads the DEM through arcpy (RasterToNumPyArray), so it
     works on mosaic datasets, VRTs and clips alike; only the window around
     the line is read.
 A15. Making a layer from a raster function (MakeRasterLayer) evaluates it
     over the whole source at full resolution: 110-210 s for one cloud tile
     (2026-11-15, Pro 3.4), though nothing needs drawing yet. So live layers
     on remote sources (VRTs, mosaic datasets) are built on a stand-in: an
     empty sparse raster (in a mosaic dataset for a mosaic) on exactly the
     source's grid, then the saved .lyrx is pointed at the real source by
     replacing its workspace and name (8-30 s; the same pixels when read).
     The layer stores the source's extent as it was, so the tools rebuild a
     GEM-15 terrain group rather than reuse it when a mosaic has grown.
 A10. The LRM uses the Focal Statistics raster function (circle, NoData
     ignored) when Spatial Analyst or Image Analyst is available; it matched
     a numpy circular mean to 2e-5 m in tests. Without either extension it
     uses a convolution with the same circular kernel: identical away from
     NoData, up to ~2 m different next to NoData (water, coast), and padded
     differently within the radius of the dataset's edge.
 A11. Derivatives are computed on the first band (for the DSM, "dsm").
     Within the LRM radius of a clip's edge the LRM uses a partial circle,
     so draw a clip's extent a little larger than the area you need.
 A12. Pro's own multidirectional hillshade ignores the sun altitude (tested:
     identical output at 30 and 60 degrees). So the multidirectional
     hillshade here is built from four traditional hillshades at the chosen
     altitude (azimuths 225, 270, 315, 360), weighted by cos^2(aspect -
     azimuth) and halved: the multidirectional oblique weighting of Mark
     (1992, USGS OF 92-422) that GDAL and QGIS use. Flat ground comes out at
     255 x sin(altitude), as for a single sun.
"""

import hashlib
import json
import math
import os
import re
import sys
import tempfile
import uuid

import arcpy

_here = os.path.dirname(os.path.abspath(__file__))
if _here not in sys.path:
    sys.path.insert(0, _here)


def _core_fingerprint(folder):
    """Hash of the gem15core sources in a folder (line endings ignored)."""
    h = hashlib.sha1()
    for name in sorted(os.listdir(folder)):
        if name.endswith(".py"):
            with open(os.path.join(folder, name), "rb") as fh:
                h.update(name.encode() + fh.read().replace(b"\r\n", b"\n"))
    return h.hexdigest()


# Pro keeps imported modules for the whole session, so after the toolbox
# folder is updated (or another copy was loaded first) a refresh would still
# run the old gem15core, failing on anything new or silently behaving the old
# way. Re-import it only when it differs from the copy beside this file:
# re-importing an unchanged core would give its classes (Gem15Error) new
# identities and break `except` clauses in code that imported them earlier.
try:
    _core_fp = _core_fingerprint(os.path.join(_here, "gem15core"))
except OSError:
    _core_fp = None
_loaded_core = sys.modules.get("gem15core")
if _loaded_core is not None and _core_fp is not None:
    _loaded_fp = getattr(_loaded_core, "_toolbox_fingerprint", None)
    if _loaded_fp is None:             # imported by something else: compare its files
        try:
            _loaded_fp = _core_fingerprint(os.path.dirname(_loaded_core.__file__))
        except (OSError, TypeError):
            _loaded_fp = ""
    if _loaded_fp != _core_fp:
        for _name in [m for m in sys.modules if m == "gem15core" or m.startswith("gem15core.")]:
            del sys.modules[_name]

from gem15core import (ATTRIBUTION, BUCKET, BUCKET_URL, CAVEAT, CITATION,  # noqa: E402
                       DATA_FOLDER, DATA_LICENCE, DSM_BANDS, MAP_CREDIT, VERTICAL_DATUM,
                       Gem15Error)
from gem15core import crs as gcrs  # noqa: E402
from gem15core import download  # noqa: E402
from gem15core import extent as gext  # noqa: E402
from gem15core import paths as gpaths  # noqa: E402
from gem15core import stac, terrain, vrt  # noqa: E402
import gem15core as _core  # noqa: E402

if _core_fp is not None:
    _core._toolbox_fingerprint = _core_fp       # what was loaded, for the next refresh

DEFAULT_ACS_NAME = "gem15.acs"  # looked for in the project home folder
OUTPUT_SUBFOLDER = "gem15"      # VRTs, layer files, clips, profiles: <home>/gem15
WARN_CELLS = 60                 # warn above this many 1-degree cells
MAX_CELLS = 1500                # refuse before any request (Australia's box is ~1400)
MAX_VIEW_CELLS = 36             # "for current view": 6 x 6 degrees, as the QGIS plugin
PAD_DEG = 0.005                 # pad the extent slightly (~500 m) after projecting
COLLECTION_ENV = "GEM15_COLLECTION_URL"  # catalogue override (tests or a mirror)
PROBE_TILE = BUCKET_URL + DATA_FOLDER + "/m40_p140/w144sm38/dtm/zall/mosaic.tif"  # A7
LIVE_LRM_MIN_SCALE = terrain.LRM_MAX_SCALE   # A9: live LRM drawn at 1:50,000 and closer
DEFAULT_MAX_MB = 2000.0         # Clip for Analysis stops above this download (as QGIS)
NEAR_LIMIT = 0.8                # estimates above this share of the limit are warnings
TILE_EDGES_FC = "GEM15_tile_edges"   # in the project's default geodatabase

MOSAIC, LAYERS, VRT = "Mosaic dataset", "Separate layers", "VRT file"
AUTOMATIC = "Automatic"
NO_TILES = ("No GEM-15 tiles cover this extent. GEM-15 covers land between about 61 S "
            "and 84 N; zoom to land, or check the extent's coordinate system.")

# Readable choices in the dialogs -> core keys.
PRODUCT_LABELS = {"DTM (bare earth)": "dtm", "DSM (top of trees and buildings)": "dsm"}
ILLUMINATION_LABELS = {"Multidirectional": "multidirectional",
                       "Single sun (azimuth)": "single",
                       "Across a fault trend": "across_trend"}
LRM_PRESET_LABELS = {f"{r:g} m: {terrain.LRM_PRESET_NOTES[r]}": r for r in terrain.LRM_PRESETS_M}


def clip_limit(value):
    """The download limit in MB from the parameter: blank uses the default,
    0 (or less) means no limit, as in the QGIS plugin."""
    if value is None or value == "":
        return DEFAULT_MAX_MB
    v = float(value)
    return float("inf") if v <= 0 else v


def product_key(value):
    """'dtm', 'dsm' or a readable label -> 'dtm' / 'dsm'."""
    v = str(value or "dtm").strip("'\"")
    return PRODUCT_LABELS.get(v, v.lower() if v.lower() in ("dtm", "dsm") else "dtm")


def illumination_key(value):
    v = str(value or "Multidirectional").strip("'\"")
    return ILLUMINATION_LABELS.get(v, v if v in terrain.ILLUMINATIONS else "multidirectional")


def lrm_radius_value(value):
    """A preset label or a number -> radius in metres."""
    if value is None or value == "":
        return terrain.LRM_RADIUS_M
    v = str(value).strip("'\"")
    if v in LRM_PRESET_LABELS:
        return LRM_PRESET_LABELS[v]
    return float(v.split(" ")[0])

# Terrain derivatives: key, label in the tools, in drawing order (bottom first).
DERIVATIVES = (("hillshade", "Hillshade (multidirectional)"),
               ("slope", "Slope (degrees)"),
               ("lrm", "Local relief model"))
DERIV_LABELS = [label for _, label in DERIVATIVES]


# --------------------------------------------------------------------------
# Catalogue
# --------------------------------------------------------------------------
def _cancelled():
    """True once the user has pressed Cancel on the running tool (Pro 3.x)."""
    env = getattr(arcpy, "env", None)
    return bool(getattr(env, "isCancelled", False))


def _lookup_progress(done, total):
    """Step progressor while the catalogue documents arrive."""
    try:
        if done == 1:
            arcpy.SetProgressor("step", "Reading the GEM-15 tile catalogue...", 0, total, 1)
        if done == 1 or done == total or done % 10 == 0:
            arcpy.SetProgressorPosition(done)
    except Exception:                  # no progressor (e.g. a stub arcpy in tests)
        pass


def find_cells(bbox, product, fetch=None, collection_url=None, max_cells=MAX_CELLS):
    """Tiles (gem15core.stac.Tile) with a `product` asset whose cell overlaps bbox.
    Refuses areas of more than max_cells 1-degree cells before any request, and
    stops if the user cancels while the catalogue is read."""
    kw = {"fetch": fetch, "max_cells": max_cells, "progress": _lookup_progress,
          "is_cancelled": _cancelled}
    collection_url = collection_url or os.environ.get(COLLECTION_ENV)
    if collection_url:
        kw["collection_url"] = collection_url
    tiles = stac.find_tiles(bbox, product, **kw)
    try:
        arcpy.ResetProgressor()
    except Exception:
        pass
    return tiles


def cloud_paths(acs, asset_urls, exists):
    """Map https asset URLs to paths inside the .acs connection (A1)."""
    return gpaths.cloud_paths(acs, asset_urls, exists)


# --------------------------------------------------------------------------
# Extents (A5)
# --------------------------------------------------------------------------
def _source_sr(sr):
    """A clean arcpy.SpatialReference rebuilt from a parameter value's SR.

    The value a GPExtent parameter returns in a Python toolbox is not a full
    arcpy.Extent, and calling projectAs on it fails with "CreateObject error
    creating spatial reference" (Esri Community). Rebuilding from the code
    (or the WKT for a custom CRS) avoids that.
    """
    code = int(getattr(sr, "factoryCode", 0) or 0)
    if code:
        return arcpy.SpatialReference(code)
    clean = arcpy.SpatialReference()
    clean.loadFromString(sr.exportToString())
    return clean


def extent_points_lonlat(ext, densify=11):
    """Points along the outline of an extent, as (lon, lat) in degrees.

    Each point is projected on its own (a projected polygon would be cut or
    wrapped at 180 degrees). Points that cannot be projected are dropped.
    """
    x0, y0, x1, y1 = (float(ext.XMin), float(ext.YMin), float(ext.XMax), float(ext.YMax))
    ring = gext.densified_ring(x0, y0, x1, y1, densify)   # curved edges covered
    sr = getattr(ext, "spatialReference", None)
    code = int(getattr(sr, "factoryCode", 0) or 0) if sr is not None else 0
    if sr is None or code == 4326:
        # Already lon/lat, or assumed to be. Values far outside the globe are
        # map units of an extent that lost its coordinate system.
        if any(abs(x) > 360 or abs(y) > 90.5 for x, y in ring):
            raise ValueError("The extent has no coordinate system and is not in degrees; "
                             "set its coordinate system or enter it in degrees.")
        return ring
    mp = arcpy.Multipoint(arcpy.Array([arcpy.Point(x, y) for x, y in ring]), _source_sr(sr))
    return [(p.X, p.Y) for p in mp.projectAs(arcpy.SpatialReference(4326)).getPart()]


def poles_inside(ext):
    """(north, south): whether each pole lies inside an extent drawn in a
    projected CRS (e.g. a polar stereographic map), whose outline alone would
    give a box that stops short of the pole (gem15core.extent.include_poles)."""
    sr = getattr(ext, "spatialReference", None)
    code = int(getattr(sr, "factoryCode", 0) or 0) if sr is not None else 0
    if sr is None or code == 4326 or getattr(sr, "type", "") == "Geographic":
        return (False, False)
    found = []
    for lat in (90.0, -90.0):
        try:
            p = arcpy.PointGeometry(arcpy.Point(0.0, lat), arcpy.SpatialReference(4326)
                                    ).projectAs(_source_sr(sr)).firstPoint
            found.append(p is not None and math.isfinite(p.X) and math.isfinite(p.Y)
                         and float(ext.XMin) <= p.X <= float(ext.XMax)
                         and float(ext.YMin) <= p.Y <= float(ext.YMax))
        except Exception:              # the pole cannot be projected (e.g. Mercator)
            found.append(False)
    return tuple(found)


def extent_to_lonlat(ext, densify=11):
    """GPExtent value in any CRS -> padded (W, S, E, N) in degrees; W > E when
    the extent crosses the antimeridian (stac.find_tiles accepts that)."""
    pts = [(x, max(-90.0, min(90.0, y))) for x, y in extent_points_lonlat(ext, densify)
           if x is not None and y is not None and math.isfinite(x) and math.isfinite(y)]
    try:
        bbox = gext.check(gext.from_lonlat_points(pts))
    except Gem15Error as e:
        raise ValueError("Could not convert the extent to longitude/latitude; "
                         "set the extent's coordinate system or enter it in degrees.") from e
    bbox = gext.include_poles(bbox, *poles_inside(ext))
    return gext.pad(bbox, PAD_DEG)


# --------------------------------------------------------------------------
# Coordinate systems (A6)
# --------------------------------------------------------------------------
def sr_utm_zone(sr):
    """UTM zone number from a coordinate system name like 'WGS_1984_UTM_Zone_55S'
    or 'GDA2020_MGA_Zone_55' (MGA zones share UTM's zone numbers); else None."""
    m = re.search(r"(?:UTM|MGA)_Zone_(\d+)", getattr(sr, "name", "") or "", re.IGNORECASE)
    return int(m.group(1)) if m else None


def zone_mismatch(md_zone, tile_epsgs, tolerance=1):
    """UTM zones of tiles more than `tolerance` zones from the mosaic's zone (A6)."""
    if md_zone is None:
        return []
    bad = set()
    for epsg in tile_epsgs:
        info = gcrs.utm_zone_of_epsg(epsg)
        if info is None:
            bad.add(epsg)                      # not UTM at all: cannot judge, refuse
        elif gcrs.zone_distance(info[0], md_zone) > tolerance:
            bad.add(info[0])
    return sorted(bad)


def sr_for_choice(choice):
    """arcpy.SpatialReference for a gem15core.crs.CrsChoice."""
    if choice.epsg:
        return arcpy.SpatialReference(int(choice.epsg))
    sr = arcpy.SpatialReference()
    sr.loadFromString(choice.esri_wkt())
    return sr


def auto_sr(bbox, tiles):
    """(arcpy.SpatialReference, CrsChoice) chosen automatically for the tiles (A6)."""
    choice = gcrs.auto_crs(bbox, [t.epsg for t in tiles])
    return sr_for_choice(choice), choice


def sr_to_gdal(sr):
    """arcpy.SpatialReference -> CRS string for GDAL: 'EPSG:xxxx' when GDAL knows
    the code (Esri-only codes such as 54009 it does not), else the Esri WKT."""
    from gem15core import gdalops                      # needs GDAL (osgeo)
    code = int(getattr(sr, "factoryCode", 0) or 0)
    if code:
        try:
            gdalops.srs(f"EPSG:{code}")
            return f"EPSG:{code}"
        except Exception:
            pass
    return sr.exportToString().split(";")[0]          # drop arcpy's x/y domain suffix


# Short labels for the national grids the automatic choice uses.
_CRS_TAGS = {7845: "GA_LCC", 2193: "NZTM"}


def crs_tag(sr_name, factory_code=0):
    """Short, geodatabase-safe label for a coordinate system, for automatic
    mosaic names: 'UTM55S', 'GA_LCC', 'NZTM', else the cleaned-up CRS name
    (e.g. 'TM_133_5E' for gem15core's custom Transverse Mercator)."""
    code = int(factory_code or 0)
    if code in _CRS_TAGS:
        return _CRS_TAGS[code]
    name = sr_name or ""
    m = re.search(r"UTM_Zone_(\d+)([NS])", name, re.IGNORECASE)
    if m:
        return f"UTM{int(m.group(1))}{m.group(2).upper()}"
    for long, short in (("GEM-15_custom_", ""), ("Transverse_Mercator", "TM"),
                        ("Lambert_Conformal_Conic", "LCC")):
        name = name.replace(long, short)
    tag = re.sub(r"[^0-9A-Za-z]+", "_", name).strip("_")[:40]
    return tag or (f"EPSG{code}" if code else "custom")


def auto_mosaic_name(product, sr):
    """Mosaic dataset name used when the name is left blank: GEM15_<product>_<CRS>."""
    return f"GEM15_{product}_{crs_tag(getattr(sr, 'name', ''), getattr(sr, 'factoryCode', 0))}"


# --------------------------------------------------------------------------
# Cloud connection (A1, A7)
# --------------------------------------------------------------------------
def create_connection(folder, name=DEFAULT_ACS_NAME, replace=False):
    """Make an anonymous .acs for the GEM-15 bucket. Returns (path, created);
    an existing file is kept unless replace=True."""
    if not name.lower().endswith(".acs"):
        name += ".acs"
    path = os.path.join(folder, name)
    if os.path.exists(path):
        if not replace:
            return path, False
        os.remove(path)
    arcpy.management.CreateCloudStorageConnectionFile(
        folder, name, "GOOGLE", BUCKET,
        config_options="GS_NO_SIGN_REQUEST YES",   # anonymous, public bucket
        folder=DATA_FOLDER)
    return path, True


def connection_works(acs):
    """True if a known tile (A7) can be opened through the connection."""
    paths, _ = cloud_paths(acs, [PROBE_TILE], arcpy.Exists)
    return bool(arcpy.Exists(paths[0]))


# --------------------------------------------------------------------------
# Maps (only inside ArcGIS Pro; headless runs return paths instead)
# --------------------------------------------------------------------------
def _project():
    try:
        return arcpy.mp.ArcGISProject("CURRENT")
    except Exception:                  # headless: no project is open
        return None


def active_map():
    aprx = _project()
    return getattr(aprx, "activeMap", None) if aprx is not None else None


def home_folder():
    aprx = _project()
    return getattr(aprx, "homeFolder", None) if aprx is not None else None


def output_folder(folder=None):
    """Where outputs go: `folder` if given, else <project home>/gem15, else
    (headless) the scratch folder. Created if missing."""
    if not folder:
        home = home_folder()
        folder = os.path.join(home, OUTPUT_SUBFOLDER) if home else arcpy.env.scratchFolder
    os.makedirs(folder, exist_ok=True)
    return folder


def default_gdb():
    aprx = _project()
    gdb = getattr(aprx, "defaultGeodatabase", None) if aprx is not None else None
    return gdb or arcpy.env.scratchGDB


def current_view_extent():
    """The active map view's extent, with the map's coordinate system, or None
    (headless, or a layout rather than a map is active)."""
    aprx = _project()
    view = getattr(aprx, "activeView", None) if aprx is not None else None
    camera = getattr(view, "camera", None)
    if camera is None:
        return None
    try:
        ext = camera.getExtent()
        sr = getattr(ext, "spatialReference", None) or view.map.spatialReference
        return arcpy.Extent(ext.XMin, ext.YMin, ext.XMax, ext.YMax, spatial_reference=sr)
    except Exception:
        return None


def extent_or_view(value):
    """(W, S, E, N) degrees from an Extent parameter value, or from the current
    map view when it is blank. Raises Gem15Error if there is neither."""
    ext = value if value is not None else current_view_extent()
    if ext is None:
        raise Gem15Error("Open a map view and zoom to your area, or set the extent.")
    if value is None:
        _log("Extent: the current map view.")
    try:
        return extent_to_lonlat(ext)
    except ValueError as e:
        raise Gem15Error(str(e)) from e


def default_mode():
    """Mosaic dataset with a Standard or Advanced licence; a VRT at Basic (one
    seamless layer, unlike separate per-tile layers)."""
    return VRT if _is_basic() else MOSAIC


def ensure_connection(acs=None):
    """The .acs to use: `acs` if given, else gem15.acs in the project home
    folder (scratch folder headless), made if missing and then checked."""
    if acs:
        return acs
    path, created = create_connection(home_folder() or arcpy.env.scratchFolder)
    if created:
        _log(f"Created {path}, an anonymous read-only connection to the GEM-15 bucket.")
        if not connection_works(path):
            arcpy.AddWarning("Could not open a known tile through the new connection. "
                             "Check the internet connection, or a proxy or firewall "
                             "blocking storage.googleapis.com.")
    return path


def _norm(path):
    return os.path.normcase(os.path.normpath(str(path)))


def find_layer(m, source=None, name=None):
    """A layer in map m with this data source (or name), or None."""
    for lyr in m.listLayers():
        try:
            if source and lyr.supports("DATASOURCE") and _norm(lyr.dataSource) == _norm(source):
                return lyr
            if name and lyr.name == name:
                return lyr
        except Exception:
            continue
    return None


def credit_text():
    """Credit, licence, citation and caveat, as CC BY 4.0 asks (for layer metadata)."""
    return (f"{MAP_CREDIT}. {ATTRIBUTION}; licence {DATA_LICENCE}. Cite: {CITATION}. "
            f"Heights: {VERTICAL_DATUM}. {CAVEAT}")


def set_credits(layer, title):
    """Put the GEM-15 credit and caveat in a layer's metadata (Layer > Properties >
    Metadata), as the QGIS plugin does."""
    try:
        md = arcpy.metadata.Metadata()
        md.title = title
        md.summary = "GEM-15 15 m elevation, streamed from Google's public cloud tiles."
        md.description = credit_text()
        md.credits = f"{ATTRIBUTION}. Licence: {DATA_LICENCE}. Cite: {CITATION}"
        layer.metadata = md
    except Exception:
        pass                           # metadata is a courtesy, not essential


def add_dem_layer(m, path, name):
    """Add an elevation layer (mosaic dataset, VRT or tile) with DRA, unless the
    map already shows that data. Returns (layer, added)."""
    lyr = find_layer(m, source=path)
    if lyr is not None:
        return lyr, False
    lyr = m.addDataFromPath(path)
    lyr.name = name
    set_dra(lyr)
    set_credits(lyr, name)
    return lyr, True


def set_dra(layer):
    """Turn on DRA (stretch statistics from the pixels in view) for a raster
    layer, or for the sublayers of a mosaic layer. Returns True if set."""
    done = False
    try:
        subs = list(layer.listLayers() or [])   # a mosaic layer's image sublayer etc.
    except Exception:
        subs = []
    for lyr in [layer] + subs:
        try:
            cim = lyr.getDefinition("V3")
            col = getattr(cim, "colorizer", None)
            if col is not None and hasattr(col, "statsType"):
                col.statsType = "AreaOfView"
                lyr.setDefinition(cim)
                done = True
        except Exception:
            pass                      # not a raster layer, or no CIM access
    return done


def add_rasters_to_map(m, raster_paths, names):
    """Add rasters to a map as layers with DRA (no duplicates); returns the layers."""
    return [add_dem_layer(m, path, name)[0] for path, name in zip(raster_paths, names)]


def add_layer_files(m, lyrx_paths, group_name, dem_source=None, dem_name=None):
    """Add layer files to a group layer, the first at the bottom; returns the
    group. A group of the same name is replaced: its live layers store the
    source's extent as it was (A15), so a mosaic that has grown needs new ones.
    With dem_source, that elevation layer goes into the group too, at the
    bottom and switched off: the opaque hillshade hides it, so drawing it
    would only cost downloads."""
    for old in [lyr for lyr in m.listLayers()
                if lyr.name == group_name and getattr(lyr, "isGroupLayer", False)]:
        m.removeLayer(old)
    group = m.createGroupLayer(group_name)
    if dem_source:
        dem_layer, _ = add_dem_layer(m, dem_source, dem_name or os.path.basename(dem_source))
        try:
            m.addLayerToGroup(group, dem_layer, "BOTTOM")      # adds a copy
        except Exception:
            pass                       # leave the DEM where it is
        else:
            for lyr in group.listLayers():
                if lyr.name == dem_layer.name:
                    lyr.visible = False
            try:
                m.removeLayer(dem_layer)                        # the original
            except Exception:
                pass
    for path in lyrx_paths:
        m.addLayerToGroup(group, arcpy.mp.LayerFile(path), "TOP")
    return group


def update_tile_edges(tiles, product, gdb=None):
    """Add the tiles' true outlines (not their 1-degree cells) to a polygon
    feature class in the project geodatabase, skipping tiles already there.
    Straight features along these lines may be seams between acquisitions,
    not scarps. Returns the feature class path."""
    gdb = gdb or default_gdb()
    fc = os.path.join(gdb, TILE_EDGES_FC)
    wgs84 = arcpy.SpatialReference(4326)
    if not arcpy.Exists(fc):
        arcpy.management.CreateFeatureclass(gdb, TILE_EDGES_FC, "POLYGON",
                                            spatial_reference=wgs84)
        arcpy.management.AddField(fc, "cell_id", "TEXT", field_length=16)
        arcpy.management.AddField(fc, "utm_zone", "TEXT", field_length=12)
    with arcpy.da.SearchCursor(fc, ["cell_id"]) as cur:
        have = {row[0] for row in cur}
    with arcpy.da.InsertCursor(fc, ["SHAPE@", "cell_id", "utm_zone"]) as cur:
        for t in tiles:
            if t.cell_id in have:
                continue
            epsg, ring = stac.tile_outline(t, product)
            poly = arcpy.Polygon(arcpy.Array([arcpy.Point(x, y) for x, y in ring]),
                                 arcpy.SpatialReference(int(epsg)))
            cur.insertRow([poly.projectAs(wgs84), t.cell_id, gcrs.zone_label(epsg)])
            have.add(t.cell_id)
    return fc


def style_tile_edges(layer):
    """No fill, dashed dark-orange outline, labelled with cell and zone."""
    try:
        cim = layer.getDefinition("V3")
        sym = cim.renderer.symbol.symbol
        stroke = arcpy.cim.CreateCIMObjectFromClassName("CIMSolidStroke", "V3")
        stroke.color = arcpy.cim.CreateCIMObjectFromClassName("CIMRGBColor", "V3")
        stroke.color.values = [217, 95, 2, 100]
        stroke.width = 1.2
        dash = arcpy.cim.CreateCIMObjectFromClassName("CIMGeometricEffectDashes", "V3")
        dash.dashTemplate = [6, 4]
        stroke.effects = [dash]
        sym.symbolLayers = [stroke]
        for lc in getattr(cim, "labelClasses", []) or []:
            lc.expression = '$feature.cell_id + " (" + $feature.utm_zone + ")"'
            lc.expressionEngine = "Arcade"
        cim.labelVisibility = True
        layer.setDefinition(cim)
    except Exception:
        pass                           # unstyled is still useful


def add_tile_edges(m, tiles, product):
    """Update the tile-edges feature class and show it in map m (once)."""
    fc = update_tile_edges(tiles, product)
    if m is not None and find_layer(m, source=fc) is None:
        lyr = m.addDataFromPath(fc)
        lyr.name = "GEM-15 tile edges (straight features on these may be seams)"
        style_tile_edges(lyr)
    return fc


# --------------------------------------------------------------------------
# VRT route (A8)
# --------------------------------------------------------------------------
def vrt_stem(bbox, band):
    """File stem from the extent, e.g. gem15_dtm_144p100_m37p520_144p140_m37p490."""
    def f(v):
        return f"{v:.3f}".replace("-", "m").replace(".", "p")
    return "gem15_" + band + "_" + "_".join(f(v) for v in bbox)


def product_label(product, band=None):
    """'DTM', 'DSM' or e.g. 'DSM dsm_std', for layer names."""
    if product == "dtm":
        return "DTM"
    return "DSM" if (band or "dsm") == "dsm" else f"DSM {band}"


def dem_layer_name(tiles, product, band, where):
    n = len(tiles)
    return f"GEM-15 {product_label(product, band)} ({where}, {n} tile{'s' if n != 1 else ''})"


def write_vrt_for_extent(bbox, tiles, product, band, folder, sr=None, log=None,
                         whole_tiles=True):
    """Write a VRT over the tiles (A8); returns (path, where) with `where`
    naming its grid, e.g. "UTM 55S" or "GDA2020 / GA LCC".

    whole_tiles: cover every tile in full, not just the extent, so the layer
    stays useful when panning nearby (as the QGIS plugin and mosaic datasets).
    The file is named after the tiles (gem15core.stac.tiles_tag), so the same
    tiles give the same file, which is reused: Pro may hold it open.
    One UTM zone (and sr blank or that zone): the tiles' native grid, written
    from catalogue metadata alone, with the GDAL options in each source path
    (gem15core.paths P2) so Pro opens it quickly in any session.
    Otherwise GDAL warps to `sr` or the automatic CRS (A6), each zone's warp
    covering only its own tiles (gdalops G5), with the same source paths.
    Without our GDAL settings (which Pro's drawing never gets) GDAL still
    probes 7 side-car files per tile on first use (gem15core.paths P2), so
    the first zoomed-out draw of a large area is slow: about 90 s for 32
    tiles over 7 x 4 degrees, checked 2026-11-15 with GDAL 3.9.2.
    """
    from gem15core import gdalops                      # needs GDAL (osgeo)
    area = stac.tiles_bbox(tiles) if whole_tiles else bbox
    dst = sr_to_gdal(sr) if sr is not None else None
    zones = stac.group_by_epsg(tiles)
    key = band or product
    if len(zones) == 1:
        epsg = next(iter(zones))
        if dst is None or gdalops.srs(dst).IsSame(gdalops.srs(f"EPSG:{epsg}")):
            extra = "" if whole_tiles else ",".join(f"{v:.4f}" for v in area)
            out = os.path.join(folder, f"gem15_{key}_{stac.tiles_tag(tiles, product, key, extra)}"
                                       f"_epsg{epsg}.vrt")
            if not os.path.exists(out):
                info = vrt.write_vrt(tiles, product, out, band,
                                     bounds=gdalops.aoi_bounds_in(f"EPSG:{epsg}", area),
                                     path_for=gpaths.portable_gdal_path)
                if log:
                    log(f"Wrote {info.path} ({info.cols} x {info.rows} px, native "
                        f"EPSG:{epsg}, nothing resampled)")
            elif log:
                log(f"Reusing {out}")
            return out, gcrs.zone_label(epsg)
    choice = None if dst is not None else gcrs.auto_crs(area, list(zones))
    target = dst or choice.gdal_string()
    extra = target + ("" if whole_tiles else ",".join(f"{v:.4f}" for v in area))
    out = os.path.join(folder, f"gem15_{key}_{stac.tiles_tag(tiles, product, key, extra)}.vrt")
    if os.path.exists(out):
        if log:
            log(f"Reusing {out}")
    else:
        gdalops.build_vrt(area, out, product, band, dst_crs=target, tiles=tiles, log=log,
                          portable=True)
    where = choice.name if choice is not None else (getattr(sr, "name", "") or "chosen CRS")
    return out, where


# --------------------------------------------------------------------------
# Terrain derivatives (A9-A11; settings from gem15core.terrain)
# --------------------------------------------------------------------------
def deriv_keys(labels):
    """Tool labels (or keys) -> keys in drawing order."""
    chosen = {str(v).strip("'\"") for v in (labels or [])}
    return [k for k, label in DERIVATIVES if k in chosen or label in chosen]


def layer_label(name, key, radius_m=terrain.LRM_RADIUS_M,
                altitude=terrain.HILLSHADE_ALTITUDE, azimuth=None, z=1.0):
    if key == "hillshade":
        return f"{name} hillshade ({terrain.hillshade_label(None, altitude, azimuth, z)})"
    if key == "slope":
        return f"{name} slope (0-{terrain.SLOPE_DISPLAY_MAX_DEG:g} degrees)"
    if key == "canopy":
        return f"{name} canopy check (DSM - DTM > {terrain.CANOPY_THRESHOLD_M:g} m)"
    return (f"{name} LRM ({radius_m:g} m, +/-{terrain.LRM_DISPLAY_RANGE_M:g} m; "
            f"1:{LIVE_LRM_MIN_SCALE:,.0f} and closer)")


def file_suffix(key, radius_m=terrain.LRM_RADIUS_M, azimuth=None, z=1.0):
    if key == "lrm":
        return f"lrm{radius_m:g}m"
    if key == "hillshade" and (azimuth is not None or abs(float(z) - 1.0) > 1e-9):
        sun = "md" if azimuth is None else f"az{azimuth:g}"
        return f"hillshade_{sun}_z{float(z):g}"
    return key


def safe_name(text):
    """Text -> something safe in a file name."""
    out = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(text))
    return out.strip("_") or "gem15"


def circle_kernel(radius_m, cell_m):
    """Square kernel (list of rows) averaging over a circle: 1/N inside, 0 outside.
    Same circle as terrain.circle_offsets and the Focal Statistics NbrCircle."""
    r = terrain.radius_cells(radius_m, cell_m)
    offsets = terrain.circle_offsets(radius_m, cell_m)
    w = 1.0 / len(offsets)
    k = [[0.0] * (2 * r + 1) for _ in range(2 * r + 1)]
    for dx, dy in offsets:
        k[dy + r][dx + r] = w
    return k


def lrm_method():
    """'focal' if Spatial Analyst or Image Analyst is available, else 'convolution' (A10)."""
    for ext in ("Spatial", "ImageAnalyst"):
        try:
            if arcpy.CheckExtension(ext) == "Available":
                return "focal"
        except Exception:
            pass
    return "convolution"


def dem_band(src):
    """arcpy.Raster of the first band of a path, layer or Raster (A11)."""
    r = src if isinstance(src, arcpy.Raster) else arcpy.Raster(src)
    if r.bandCount > 1:
        r = arcpy.ia.ExtractBand(r, band_ids=[1])
    return r


MD_AZIMUTHS = (225.0, 270.0, 315.0, 360.0)


def hillshade(dem, azimuth, altitude=terrain.HILLSHADE_ALTITUDE, z=terrain.HILLSHADE_Z_FACTOR):
    """Traditional (single-sun) hillshade as a float raster function, 0-255."""
    return arcpy.ia.Float(arcpy.ia.Hillshade(
        dem, azimuth=float(azimuth), altitude=float(altitude),
        z_factor=float(z), slope_type=1, hillshade_type=0))


def multidirectional_hillshade(dem, altitude=terrain.HILLSHADE_ALTITUDE,
                               z=terrain.HILLSHADE_Z_FACTOR):
    """Multidirectional hillshade that honours the sun altitude (A12).

    0.5 * sum of cos^2(aspect - az) * hillshade(az) over the four azimuths,
    which with w225 + w315 = w270 + w360 = 1 reduces to
    0.25 * (sum of the four + sin(2a) (h225 - h315) + cos(2a) (h360 - h270)).
    """
    h = {az: hillshade(dem, az, altitude, z) for az in MD_AZIMUTHS}
    two_a = arcpy.ia.Times(arcpy.ia.Aspect(dem), math.pi / 90.0)   # 2 x aspect, radians
    total = h[225.0] + h[270.0] + h[315.0] + h[360.0]
    return 0.25 * (total + arcpy.ia.Sin(two_a) * (h[225.0] - h[315.0])
                   + arcpy.ia.Cos(two_a) * (h[360.0] - h[270.0]))


def canopy_raster(dsm_src, dtm_src, threshold=terrain.CANOPY_THRESHOLD_M):
    """1 where DSM minus DTM exceeds the threshold (trees, buildings: the DTM
    there is a guess), NoData elsewhere; a lazy raster function (T7)."""
    diff = arcpy.ia.Arithmetic(dem_band(dsm_src), dem_band(dtm_src), "Minus")
    # NoData wherever the difference is small (Con without a false value gave 0,
    # which the green ramp would paint everywhere).
    return arcpy.ia.SetNull(arcpy.ia.LessThanEqual(diff, float(threshold)), 1)


def terrain_rasters(src, keys, radius_m=terrain.LRM_RADIUS_M,
                    altitude=terrain.HILLSHADE_ALTITUDE, method=None, azimuth=None,
                    z=terrain.HILLSHADE_Z_FACTOR):
    """{key: arcpy.Raster} of lazy raster functions on the DEM (nothing is read).

    hillshade : multidirectional (azimuth None) or one sun at `azimuth`, sun
                altitude `altitude`, z factor `z` (T1, T4, A12)
    slope     : degrees, z factor 1: true slope (T3)
    lrm       : DEM minus its mean over a circle of radius_m (T2, A10)
    """
    dem = dem_band(src)
    out = {}
    if "hillshade" in keys:
        out["hillshade"] = (multidirectional_hillshade(dem, altitude, z) if azimuth is None
                            else hillshade(dem, azimuth, altitude, z))
    if "slope" in keys:
        out["slope"] = arcpy.ia.Slope(dem, z_factor=1.0, slope_type="DEGREE")  # x, y, z in m
    if "lrm" in keys:
        cell = float(dem.meanCellWidth)
        if (method or lrm_method()) == "focal":
            # Radius in cells, not "MAP" units: with MAP units arcpy asks for the
            # input's cell size through Get Raster Properties, which took 85 s on
            # a cloud mosaic dataset. Same circle as terrain.circle_offsets.
            nbr = arcpy.ia.NbrCircle(terrain.radius_cells(radius_m, cell), "CELL")
            mean = arcpy.ia.FocalStatistics(dem, nbr, "MEAN", "DATA")
        else:
            mean = arcpy.ia.Convolution(dem, circle_kernel(radius_m, cell))
        out["lrm"] = arcpy.ia.Arithmetic(dem, mean, "Minus")
    return out


def _rgb(r, g, b):
    return {"type": "CIMRGBColor", "values": [r, g, b, 100]}


def _ramp(*colours):
    """CIM multipart colour ramp through the given CIMRGBColor dicts."""
    space = {"type": "CIMICCColorSpace", "url": "Default RGB"}
    parts = [{"type": "CIMLinearContinuousColorRamp", "colorSpace": space,
              "fromColor": a, "toColor": b} for a, b in zip(colours, colours[1:])]
    return {"type": "CIMMultipartColorRamp", "colorSpace": space,
            "colorRamps": parts, "weights": [1] * len(parts)}


# Fixed display ranges (A13, gem15core.terrain T5): key -> (min, max).
DISPLAY_RANGES = {
    "hillshade": (0.0, 255.0),
    "slope": (0.0, terrain.SLOPE_DISPLAY_MAX_DEG),
    "lrm": (-terrain.LRM_DISPLAY_RANGE_M, terrain.LRM_DISPLAY_RANGE_M),
    "canopy": (0.0, 1.0),
}


def _fixed_stretch(col, lo, hi):
    """Linear stretch over [lo, hi] from custom statistics: the same in every
    view, and Pro has no reason to compute statistics from the cloud (A13)."""
    col["stretchType"] = "MinimumMaximum"
    col["statsType"] = "GlobalStats"
    col["useCustomStretchMinMax"] = True
    col["customStretchMin"], col["customStretchMax"] = lo, hi
    col["stretchStats"] = {"type": "StatsHistogram", "min": lo, "max": hi,
                           "mean": (lo + hi) / 2.0, "stddev": (hi - lo) / 4.0, "bandIndex": 0}
    col.pop("minPercent", None)
    col.pop("maxPercent", None)


def style_layer_json(layer_def, key):
    """Style one CIMRasterLayer definition (a dict from a .lyrx) in place,
    with the fixed ranges in DISPLAY_RANGES (gem15core.terrain T5).

    hillshade : grey, 0-255.
    slope : white (flat) to black (steep or steeper than the range); added
            switched off, since it would hide the hillshade below it.
    lrm : blue (low) - white - red (high) over +/- 2.5 m (as the QGIS plugin),
          40 % transparent so a hillshade below shows through, drawn only at
          1:LIVE_LRM_MIN_SCALE and closer (A9).
    canopy : solid green, 50 % transparent, where DSM - DTM > 3 m.
    """
    col = layer_def.setdefault("colorizer", {"type": "CIMRasterStretchColorizer"})
    _fixed_stretch(col, *DISPLAY_RANGES[key])
    if key == "slope":
        col["colorRamp"] = _ramp(_rgb(255, 255, 255), _rgb(0, 0, 0))
        col.pop("colorScheme", None)
        layer_def["visibility"] = False
    if key == "lrm":
        col["colorRamp"] = _ramp(_rgb(33, 102, 172), _rgb(247, 247, 247), _rgb(178, 24, 43))
        col.pop("colorScheme", None)
        layer_def["transparency"] = 40
        layer_def["minScale"] = LIVE_LRM_MIN_SCALE
    if key == "canopy":
        col["colorRamp"] = _ramp(_rgb(27, 158, 119), _rgb(27, 158, 119))
        col.pop("colorScheme", None)
        layer_def["transparency"] = 50
    return layer_def


def _swap_strings(obj, swaps):
    """Replace text in every string of a JSON document (dicts and lists)."""
    if isinstance(obj, str):
        for old, new in swaps:
            obj = obj.replace(old, new)
        return obj
    if isinstance(obj, list):
        return [_swap_strings(v, swaps) for v in obj]
    if isinstance(obj, dict):
        return {k: _swap_strings(v, swaps) for k, v in obj.items()}
    return obj


def save_layer_file(raster, name, path, key, swaps=()):
    """Write a .lyrx for a (function) raster, styled for `key`. The layer
    stays live: the file holds the function chain, not pixels. `swaps`
    ((old, new) text pairs) point a layer built on stand-ins at the real
    sources (A15)."""
    tmp = "gem15_" + uuid.uuid4().hex[:12]
    with arcpy.EnvManager(addOutputsToMap=False):
        lyr = arcpy.management.MakeRasterLayer(raster, tmp).getOutput(0)
        if os.path.exists(path):
            os.remove(path)
        arcpy.management.SaveToLayerFile(lyr, path, "ABSOLUTE")
        arcpy.management.Delete(tmp)
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    if swaps:
        doc = _swap_strings(doc, swaps)
    for d in doc.get("layerDefinitions", []):
        style_layer_json(d, key)
        d["name"] = name
        d["description"] = f"{name}. {credit_text()}"
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
    return path


def is_remote(src):
    """True for sources read over the network: our VRTs and mosaic datasets."""
    if str(src).lower().endswith(".vrt"):
        return True
    try:
        return arcpy.Describe(src).dataType == "MosaicDataset"
    except Exception:
        return False


def make_stand_in(src, folder):
    """A local, empty raster on exactly the source's grid (extent, cell size,
    size, bands, coordinate system): a sparse GeoTIFF with no data blocks,
    inside a mosaic dataset when the source is one (A15). Returns its path."""
    from osgeo import gdal, osr
    r = arcpy.Raster(src)
    tag = "gem15standin" + uuid.uuid4().hex[:8]
    tif = os.path.join(folder, tag + ".tif")
    s = osr.SpatialReference()
    s.SetFromUserInput(sr_to_gdal(r.spatialReference))
    ds = gdal.GetDriverByName("GTiff").Create(
        tif, int(r.width), int(r.height), int(r.bandCount), gdal.GDT_Float32,
        ["SPARSE_OK=TRUE", "TILED=YES", "COMPRESS=DEFLATE", "BIGTIFF=IF_SAFER"])
    if ds is None:
        raise Gem15Error(f"Could not write a stand-in raster in {folder}.")
    ds.SetGeoTransform((r.extent.XMin, r.meanCellWidth, 0.0, r.extent.YMax, 0.0,
                        -r.meanCellHeight))
    ds.SetProjection(s.ExportToWkt())
    for b in range(1, int(r.bandCount) + 1):
        ds.GetRasterBand(b).SetNoDataValue(float("nan"))
    ds = None
    if arcpy.Describe(src).dataType != "MosaicDataset":
        return tif
    gdb = arcpy.management.CreateFileGDB(folder, tag + ".gdb").getOutput(0)
    md = arcpy.management.CreateMosaicDataset(gdb, tag, r.spatialReference, int(r.bandCount),
                                              "32_BIT_FLOAT").getOutput(0)
    arcpy.management.AddRastersToMosaicDataset(md, "Raster Dataset", tif)
    return md


def stand_in_swaps(stand, src):
    """Text swaps that point a layer built on `stand` at `src`."""
    return [(os.path.dirname(stand), os.path.dirname(src)),
            (os.path.basename(stand), os.path.basename(src))]


def terrain_layer_files(src, label, keys, folder, radius_m=terrain.LRM_RADIUS_M,
                        altitude=terrain.HILLSHADE_ALTITUDE, method=None, azimuth=None,
                        z=terrain.HILLSHADE_Z_FACTOR):
    """Write one live .lyrx per derivative into folder; returns their paths,
    bottom layer first. Remote sources are built on a stand-in (A15)."""
    os.makedirs(folder, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="gem15_", ignore_cleanup_errors=True) as tmp:
        base = make_stand_in(src, tmp) if is_remote(src) else src
        swaps = stand_in_swaps(base, src) if base != src else ()
        rasters = terrain_rasters(base, keys, radius_m, altitude, method, azimuth, z)
        out = []
        for key in deriv_keys(keys):
            path = os.path.join(folder, f"{safe_name(label)}_"
                                        f"{file_suffix(key, radius_m, azimuth, z)}.lyrx")
            out.append(save_layer_file(rasters[key],
                                       layer_label(label, key, radius_m, altitude, azimuth, z),
                                       path, key, swaps))
    return out


def canopy_layer_file(dsm, dtm, name, path):
    """A live canopy-check .lyrx (DSM - DTM > threshold), built on stand-ins
    when the sources are remote (A15)."""
    with tempfile.TemporaryDirectory(prefix="gem15_", ignore_cleanup_errors=True) as tmp:
        sd = make_stand_in(dsm, tmp) if is_remote(dsm) else dsm
        st = make_stand_in(dtm, tmp) if is_remote(dtm) else dtm
        swaps = ([*stand_in_swaps(sd, dsm)] if sd != dsm else []) + \
                ([*stand_in_swaps(st, dtm)] if st != dtm else [])
        return save_layer_file(canopy_raster(sd, st), name, path, "canopy", swaps)


def save_derivatives(dem_tif, keys, radius_m=terrain.LRM_RADIUS_M,
                     altitude=terrain.HILLSHADE_ALTITUDE, method=None, azimuth=None,
                     z=terrain.HILLSHADE_Z_FACTOR):
    """Write derivative GeoTIFFs next to a local DEM: <stem>_hillshade.tif,
    <stem>_slope.tif, <stem>_lrm300m.tif. Returns their paths."""
    rasters = terrain_rasters(dem_tif, keys, radius_m, altitude, method, azimuth, z)
    stem = os.path.splitext(dem_tif)[0]
    out = []
    with arcpy.EnvManager(compression="LZW", addOutputsToMap=False):
        for key in deriv_keys(keys):
            path = f"{stem}_{file_suffix(key, radius_m, azimuth, z)}.tif"
            if arcpy.Exists(path):
                arcpy.management.Delete(path)
            rasters[key].save(path)
            compute_statistics(path)
            out.append(path)
    return out


def compute_statistics(path):
    """Band statistics into <path>.aux.xml with GDAL, so a local GeoTIFF draws
    with a sensible stretch. (Headless, arcpy's Calculate Statistics took
    ~32 s even on a 200 x 160 px file; GDAL takes milliseconds.)"""
    from osgeo import gdal
    ds = gdal.Open(path)
    for i in range(1, ds.RasterCount + 1):
        try:
            ds.GetRasterBand(i).ComputeStatistics(False)
        except RuntimeError:
            pass                       # all NoData: nothing to compute
    ds = None


# --------------------------------------------------------------------------
# Clip (GDAL, from the shared core)
# --------------------------------------------------------------------------
def clip_estimate(bbox, tiles, product):
    """download.Estimate for reading the extent at full resolution."""
    from gem15core import gdalops                      # needs GDAL (osgeo)
    zones = stac.group_by_epsg(tiles)
    bounds = {z: gdalops.aoi_bounds_in(f"EPSG:{z}", bbox) for z in zones}
    return download.estimate(tiles, product, bounds)


def clip_to_tif(bbox, tiles, product, band, out_tif, sr=None, log=None, callback=None):
    """Write a compressed GeoTIFF of the extent: the tiles' native UTM grid
    if they share one zone, else `sr` or the automatic CRS (A6).
    callback: GDAL progress function; returning 0 cancels (Gem15Error "Cancelled.")."""
    from gem15core import gdalops                      # needs GDAL (osgeo)
    folder = os.path.dirname(os.path.abspath(out_tif))
    os.makedirs(folder, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="gem15_", ignore_cleanup_errors=True) as tmp:
        src = gdalops.build_vrt(bbox, os.path.join(tmp, "clip.vrt"), product, band,
                                dst_crs=sr_to_gdal(sr) if sr is not None else None,
                                tiles=tiles, log=log)
        gdalops.clip_to_geotiff(src, out_tif, log=log, callback=callback)
    return out_tif


# --------------------------------------------------------------------------
# Parameter helpers
# --------------------------------------------------------------------------
def _by_name(params):
    return {p.name: p for p in params}


def _param(name, label, datatype, required=True, direction="Input", **kw):
    p = arcpy.Parameter(displayName=label, name=name, datatype=datatype,
                        parameterType="Required" if required else "Optional",
                        direction=direction, multiValue=kw.pop("multiValue", False))
    for k, v in kw.items():
        setattr(p, k, v)
    return p


def _derived(name, label, datatype, multi=False):
    return arcpy.Parameter(displayName=label, name=name, datatype=datatype,
                           parameterType="Derived", direction="Output", multiValue=multi)


def _choice(name, label, options, default, required=True, multi=False):
    p = _param(name, label, "GPString", required, multiValue=multi)
    p.filter.type = "ValueList"
    p.filter.list = list(options)
    if multi:
        p.values = list(default or [])
    else:
        p.value = default
    return p


def _is_basic():
    try:
        return arcpy.ProductInfo() == "ArcView"
    except Exception:
        return False


def _log(msg):
    arcpy.AddMessage(msg)


def _reports_errors(execute):
    """Wrap a tool's execute so failures become tool error messages rather
    than Python tracebacks: our own errors, a failed geoprocessing step, GDAL
    errors and file errors (locked, read-only or OneDrive/network folders)."""
    def wrapper(self, params, messages):
        exec_error = getattr(arcpy, "ExecuteError", RuntimeError)
        try:
            return execute(self, params, messages)
        except Gem15Error as e:
            arcpy.AddError(str(e))
        except exec_error:
            arcpy.AddError(arcpy.GetMessages(2) or "A geoprocessing step failed.")
        except OSError as e:
            arcpy.AddError(f"{e}. Check the file is not open in a map or another program, "
                           "and that the folder can be written to (OneDrive and network "
                           "folders sometimes block writes).")
        except RuntimeError as e:
            arcpy.AddError(f"{e}")
    wrapper.__name__, wrapper.__doc__ = execute.__name__, execute.__doc__
    return wrapper


def _gdal_progress(label):
    """GDAL progress callback that drives Pro's progressor and stops GDAL
    (by returning 0) when the user presses Cancel."""
    try:
        arcpy.SetProgressor("step", label, 0, 100, 1)
    except Exception:
        pass
    last = [-1]

    def callback(fraction, message, data):
        pct = int(fraction * 100)
        if pct != last[0]:
            last[0] = pct
            try:
                arcpy.SetProgressorPosition(pct)
            except Exception:
                pass
        return 0 if _cancelled() else 1
    return callback


def sr_problem(sr):
    """Why a coordinate system cannot be used for 15 m pixels, or None: degrees
    give a raster one pixel wide, and feet disagree with heights in metres."""
    if sr is None:
        return None
    if getattr(sr, "type", "") == "Geographic":
        return ("This coordinate system uses degrees. Choose a projected one in metres, "
                "or leave it blank for the automatic choice.")
    per_unit = float(getattr(sr, "metersPerUnit", 1.0) or 1.0)
    if abs(per_unit - 1.0) > 1e-9:
        return (f"This coordinate system is in {getattr(sr, 'linearUnitName', 'non-metre units')}"
                "; GEM-15 heights are in metres. Choose one in metres, or leave it blank.")
    return None


# --------------------------------------------------------------------------
# Shared parameters
# --------------------------------------------------------------------------
STEP_BY_STEP = "Step by step"      # toolset holding the separate tools
OPTIONS = "Options"                # collapsed parameter group
HILLSHADE = "Hillshade"
SCARP = "Scarp height (optional)"
CANOPY_LABEL = "Canopy check (DSM minus DTM)"
EDGES_LABEL = "Tile edges"
DEM_TYPES = ["GPRasterLayer", "GPMosaicLayer", "DERasterDataset", "DEMosaicDataset"]


def _range(p, lo, hi):
    """Limit a number parameter to [lo, hi]; the dialog shows the range."""
    p.filter.type = "Range"
    p.filter.list = [lo, hi]
    return p


def _sun_params(category=HILLSHADE):
    """Illumination, sun azimuth, fault trend, sun altitude and z factor (T1, T4)."""
    illum = _choice("illumination", "Illumination", list(ILLUMINATION_LABELS),
                    "Multidirectional", required=False)
    az = _range(_param("azimuth", "Sun Azimuth (degrees)", "GPDouble", required=False,
                       value=terrain.HILLSHADE_AZIMUTH), 0.0, 360.0)
    trend = _range(_param("fault_trend", "Fault Trend (degrees)", "GPDouble",
                          required=False), 0.0, 360.0)
    alt = _range(_param("altitude", "Sun Altitude (degrees)", "GPDouble", required=False,
                        value=terrain.HILLSHADE_ALTITUDE), 1.0, 90.0)
    z = _range(_param("z_factor", "Vertical Exaggeration", "GPDouble", required=False,
                      value=terrain.HILLSHADE_Z_FACTOR), 0.1, 20.0)
    for p in (illum, az, trend, alt, z):
        p.category = category
    return [illum, az, trend, alt, z]


def _sun_enable(p, on=True):
    how = illumination_key(p["illumination"].valueAsText)
    for key in ("illumination", "altitude", "z_factor"):
        p[key].enabled = on
    p["azimuth"].enabled = on and how == "single"
    p["fault_trend"].enabled = on and how == "across_trend"


def _sun_messages(p):
    if p["fault_trend"].enabled and p["fault_trend"].value is None:
        p["fault_trend"].setErrorMessage("Give the fault's trend in degrees, e.g. 30 for a "
                                         "NNE-SSW fault. The sun shines across it.")


def _sun_values(p):
    """(azimuth or None for multidirectional, altitude, z) from the parameters."""
    how = illumination_key(p["illumination"].valueAsText)
    az_in = p["azimuth"].value
    az = terrain.sun_azimuth(how, terrain.HILLSHADE_AZIMUTH if az_in is None else az_in,
                             p["fault_trend"].value)
    alt = float(p["altitude"].value or terrain.HILLSHADE_ALTITUDE)
    z = float(p["z_factor"].value or terrain.HILLSHADE_Z_FACTOR)
    if az is not None:
        _log(f"Hillshade: one sun at azimuth {az:g} degrees, altitude {alt:g}.")
    return az, alt, z


def gem15_layer(m):
    """The top GEM-15 elevation layer in map m (by name), or None: the default
    input for Terrain Stack and Swath Profile."""
    if m is None:
        return None
    for lyr in m.listLayers():
        try:
            name = lyr.name
            if (name.startswith(("GEM-15 DTM", "GEM-15 DSM", "GEM15_dtm", "GEM15_dsm"))
                    and (lyr.isRasterLayer or not lyr.isGroupLayer)
                    and lyr.supports("DATASOURCE")):
                return lyr
        except Exception:
            continue
    return None


def _lrm_note(keys):
    if "lrm" in keys:
        _log(f"The LRM is drawn at 1:{LIVE_LRM_MIN_SCALE:,.0f} and closer: zoom in to see it. "
             "It is blank within one radius of masked water; use Clip for Analysis there.")


def _caveat():
    _log("Note: " + CAVEAT)


# --------------------------------------------------------------------------
# Toolbox
# --------------------------------------------------------------------------
class Toolbox:
    def __init__(self):
        self.label = "GEM-15 Tools"
        self.alias = "gem15"
        self.tools = [GEM15ForCurrentView, SwathProfile, ConnectToGEM15, AddGEM15ForExtent,
                      TerrainStack, ClipForAnalysis]


class GEM15ForCurrentView:
    def __init__(self):
        self.label = "GEM-15 for Current View"
        self.description = (
            "Add GEM-15 elevation for the current map view, with a live hillshade and "
            "local relief model, in one step: zoom to your area and press Run. Nothing is "
            "downloaded beyond what is drawn.")
        self.canRunInBackground = False  # layers are added to the active map

    def getParameterInfo(self):
        prod = _choice("product", "Elevation", list(PRODUCT_LABELS), "DTM (bare earth)")
        layers = _choice("layers", "Layers", DERIV_LABELS + [CANOPY_LABEL, EDGES_LABEL],
                         [DERIV_LABELS[0], DERIV_LABELS[2], EDGES_LABEL], required=False,
                         multi=True)
        radius = _choice("lrm_radius", "LRM Radius", list(LRM_PRESET_LABELS),
                         next(k for k, v in LRM_PRESET_LABELS.items()
                              if v == terrain.LRM_RADIUS_M), required=False)
        ext = _param("extent", "Extent", "GPExtent", required=False, category=OPTIONS)
        mode = _choice("mode", "Output Type", [AUTOMATIC, MOSAIC, VRT], AUTOMATIC,
                       required=False)
        mode.category = OPTIONS
        sr = _param("sr", "Coordinate System", "GPSpatialReference", required=False,
                    category=OPTIONS)
        out = _derived("out_paths", "Outputs", "GPString", multi=True)
        return [prod, layers, radius, ext, mode, sr] + _sun_params() + [out]

    def isLicensed(self):
        return True

    def updateParameters(self, params):
        p = _by_name(params)
        chosen = {str(v).strip("'\"") for v in (p["layers"].values or [])}
        p["lrm_radius"].enabled = DERIV_LABELS[2] in chosen
        _sun_enable(p, DERIV_LABELS[0] in chosen)

    def updateMessages(self, params):
        p = _by_name(params)
        if p["mode"].valueAsText == MOSAIC and _is_basic():
            p["mode"].setErrorMessage("Mosaic datasets need a Standard or Advanced licence; "
                                      "choose Automatic or VRT file.")
        problem = sr_problem(p["sr"].value)
        if problem:
            p["sr"].setErrorMessage(problem)
        _sun_messages(p)

    @_reports_errors
    def execute(self, params, messages):
        p = _by_name(params)
        product = product_key(p["product"].valueAsText)
        chosen = {str(v).strip("'\"") for v in (p["layers"].values or [])}
        keys = deriv_keys(chosen)
        bbox = extent_or_view(p["extent"].value)
        msg = stac.too_many_cells(bbox, MAX_VIEW_CELLS)
        if msg:
            raise Gem15Error(msg + " For larger areas use Add GEM-15 for Extent.")
        tiles = find_cells(bbox, product)
        if not tiles:
            raise Gem15Error(NO_TILES)
        _log(f"{len(tiles)} tile(s): " + ", ".join(t.cell_id for t in tiles))
        mode = p["mode"].valueAsText
        mode = default_mode() if mode in (None, "", AUTOMATIC) else mode
        if mode == MOSAIC and _is_basic():
            raise Gem15Error("Mosaic datasets need a Standard or Advanced licence.")
        m = active_map()
        folder = output_folder()
        sr = p["sr"].value

        def dem_for(prod, band=None):
            """(path, layer name, label for file names) of a mosaic or VRT."""
            ptiles = tiles if prod == product else find_cells(bbox, prod)
            if not ptiles:
                raise Gem15Error(f"No GEM-15 {prod.upper()} tiles cover this view.")
            if mode == MOSAIC:
                md = add_to_mosaic(ptiles, bbox, prod, ensure_connection(), default_gdb(),
                                   None, sr)
                name = os.path.basename(md)
                return md, name, name
            path, where = write_vrt_for_extent(bbox, ptiles, prod, band, folder, sr, log=_log)
            return path, dem_layer_name(ptiles, prod, band, where), \
                os.path.splitext(os.path.basename(path))[0]

        dem, dem_name, label = dem_for(product)
        outputs = [dem]
        files = []
        if keys:
            az, alt, z = _sun_values(p)
            radius = lrm_radius_value(p["lrm_radius"].valueAsText)
            files = terrain_layer_files(dem, label, keys, folder, radius, alt, lrm_method(),
                                        az, z)
        if CANOPY_LABEL in chosen:
            _log("Canopy check: streaming the DSM too (about 5 times the data of the DTM).")
            dsm, _, _ = dem_for("dsm", "dsm")
            dtm = dem if product == "dtm" else dem_for("dtm")[0]
            path = os.path.join(folder, f"{safe_name(label)}_canopy.lyrx")
            files.append(canopy_layer_file(dsm, dtm, layer_label(dem_name, "canopy"), path))
            outputs.append(dsm)
        for f in files:
            _log("Wrote " + f)
        if m is not None and files:
            add_layer_files(m, files, f"GEM-15 terrain: {dem_name}", dem, dem_name)
        elif m is not None:
            add_dem_layer(m, dem, dem_name)
        if EDGES_LABEL in chosen:
            outputs.append(add_tile_edges(m, tiles, product))
        if m is None:
            _log("No active map (running outside Pro?); returning the paths only.")
        _lrm_note(keys)
        _caveat()
        p["out_paths"].values = outputs + files


class SwathProfile:
    def __init__(self):
        self.label = "Swath Profile"
        self.description = (
            "Summarise the DEM in a band along a line drawn across a scarp: the lowest, "
            "quartile, median and highest heights every 15 m. With far-field distance "
            "ranges on each side, also estimate the scarp's height. Writes a CSV and a "
            "PNG plot.")
        self.canRunInBackground = False

    def getParameterInfo(self):
        line = _param("line", "Profile Line", "GPFeatureRecordSetLayer")
        line.value = _line_template()
        dem = _param("in_raster", "Input DEM", DEM_TYPES)
        width = _range(_param("width", "Swath Width (m)", "GPDouble",
                              value=300.0), 15.0, 5000.0)
        folder = _param("out_folder", "Output Folder", "DEFolder", required=False)
        name = _param("out_name", "Output Name", "GPString", value="swath_profile")
        bounds = []
        for key, label in (("upper_from", "Upper Side From (m along the line)"),
                           ("upper_to", "Upper Side To (m)"),
                           ("lower_from", "Lower Side From (m)"),
                           ("lower_to", "Lower Side To (m)")):
            bp = _param(key, label, "GPDouble", required=False)
            bp.category = SCARP
            bounds.append(bp)
        out_csv = _derived("out_csv", "Profile Table (CSV)", "DEFile")
        out_png = _derived("out_png", "Profile Plot (PNG)", "DEFile")
        lyr = gem15_layer(active_map())
        if lyr is not None:
            dem.value = lyr
        home = home_folder()
        if home:
            folder.value = output_folder(os.path.join(home, OUTPUT_SUBFOLDER, "profiles"))
        return [line, dem, width, folder, name] + bounds + [out_csv, out_png]

    def isLicensed(self):
        return True

    def updateParameters(self, params):
        return

    def updateMessages(self, params):
        p = _by_name(params)
        given = [p[k].value is not None for k in ("upper_from", "upper_to", "lower_from",
                                                  "lower_to")]
        if any(given) and not all(given):
            p["lower_to"].setErrorMessage("Give all four distances for the scarp height, "
                                          "or none.")
        if p["in_raster"].value is not None:
            try:
                sr = arcpy.Describe(p["in_raster"].valueAsText).spatialReference
                if sr.type == "Geographic":
                    p["in_raster"].setErrorMessage("This DEM is in degrees; use a projected "
                                                   "(metre) GEM-15 layer or clip.")
            except Exception:
                pass

    @_reports_errors
    def execute(self, params, messages):
        import numpy as np
        from gem15core import profile
        p = _by_name(params)
        desc = arcpy.Describe(p["in_raster"].valueAsText)
        raster = arcpy.Raster(desc.catalogPath)
        sr = raster.spatialReference
        if sr.type == "Geographic":
            raise Gem15Error("The DEM is in degrees; use a projected (metre) layer.")
        line_xy = profile_line_xy(p["line"].valueAsText, sr)
        cell = float(raster.meanCellWidth)
        ext = raster.extent
        gt = (ext.XMin, cell, 0.0, ext.YMax, 0.0, -float(raster.meanCellHeight))
        width = float(p["width"].value)
        win = profile.window_for(line_xy, width, gt, raster.width, raster.height)
        wgt = profile.window_geotransform(gt, win)
        corner = arcpy.Point(wgt[0], wgt[3] + win[3] * wgt[5])
        arr = arcpy.RasterToNumPyArray(raster, corner, win[2], win[3], nodata_to_value=np.nan)
        arr = np.asarray(arr, dtype=float)
        if arr.ndim == 3:
            arr = arr[0]                               # first band (DSM: "dsm")
        sw = profile.sample_swath(arr, wgt, line_xy, width, step_m=cell)
        fit = None
        vals = [p[k].value for k in ("upper_from", "upper_to", "lower_from", "lower_to")]
        if all(v is not None for v in vals):
            fit = profile.scarp_height(sw, (vals[0], vals[1]), (vals[2], vals[3]))
            for line in fit.describe():
                _log(line)
        folder = output_folder(p["out_folder"].valueAsText)
        stem = os.path.join(folder, safe_name(p["out_name"].valueAsText or "swath_profile"))
        csv = profile.to_csv(sw, stem + ".csv", fit) or stem + ".csv"
        png = profile.plot_png(sw, stem + ".png", fit,
                               title=f"Swath profile, {width:g} m wide ({MAP_CREDIT})")
        _log(f"{sw.length_m:,.0f} m long, {len(sw.distance)} steps of {cell:g} m. "
             f"Wrote {csv}" + (f" and {png}" if png else " (matplotlib missing: no plot)"))
        _caveat()
        p["out_csv"].value = csv
        if png:
            p["out_png"].value = png


def _line_template():
    """An empty polyline feature class, so Profile Line can be drawn on the map."""
    try:
        fc = os.path.join("memory", "gem15_profile_line")
        if not arcpy.Exists(fc):
            arcpy.management.CreateFeatureclass("memory", "gem15_profile_line", "POLYLINE",
                                                spatial_reference=arcpy.SpatialReference(4326))
        return fc
    except Exception:
        return None


def profile_line_xy(line_source, sr):
    """Vertices of the first line in a layer or feature set, in sr's units."""
    with arcpy.da.SearchCursor(line_source, ["SHAPE@"]) as cur:
        for (geom,) in cur:
            if geom is None:
                continue
            if geom.type != "polyline":
                raise Gem15Error("Profile Line must be a line.")
            g = geom.projectAs(sr)
            part = g.getPart(0)
            pts = [(pt.X, pt.Y) for pt in part if pt is not None]
            if len(pts) < 2:
                raise Gem15Error("The profile line needs at least two points.")
            return pts
    raise Gem15Error("Draw a line across the scarp (or choose a layer with one).")


class ConnectToGEM15:
    def __init__(self):
        self.label = "Connect to GEM-15"
        self.description = ("Create gem15.acs, an anonymous read-only connection to the "
                            "public GEM-15 bucket on Google Cloud Storage. The other tools "
                            "make it themselves when needed.")
        self.canRunInBackground = False
        self.category = STEP_BY_STEP

    def getParameterInfo(self):
        folder = _param("out_folder", "Connection File Location", "DEFolder")
        name = _param("out_name", "Connection File Name", "GPString", value=DEFAULT_ACS_NAME)
        replace = _param("replace", "Replace Existing File", "GPBoolean",
                         required=False, value=False)
        clear = _param("clear_cache", "Clear Saved Tile Catalogue", "GPBoolean",
                       required=False, value=False)
        out = _derived("out_acs", "Connection file", "DEFile")
        home = home_folder()
        if home:
            folder.value = home
        return [folder, name, replace, clear, out]

    def isLicensed(self):
        return True

    def updateParameters(self, params):
        return

    def updateMessages(self, params):
        return                         # an existing file is simply reused: not a warning

    @_reports_errors
    def execute(self, params, messages):
        p = _by_name(params)
        if p["clear_cache"].value:
            _log(f"Cleared {stac.clear_cache()} saved catalogue document(s); they are "
                 "fetched again when next needed.")
        path, created = create_connection(p["out_folder"].valueAsText,
                                          p["out_name"].valueAsText, bool(p["replace"].value))
        _log(("Created " if created else "Kept existing ") + path)
        if connection_works(path):
            _log("Connection checked: a known tile opens.")
        else:
            arcpy.AddWarning("Could not open a known tile through the connection. Check the "
                             "internet connection, or a proxy or firewall blocking "
                             "storage.googleapis.com.")
        p["out_acs"].value = path


class AddGEM15ForExtent:
    MODES = [MOSAIC, LAYERS, VRT]

    def __init__(self):
        self.label = "Add GEM-15 for Extent"
        self.description = ("Find the GEM-15 tiles covering an extent (blank = the current "
                            "view) and add them as one mosaic dataset referenced from the "
                            "cloud, as separate layers, or as a VRT file that streams them "
                            "over https.")
        self.canRunInBackground = False  # layers are added to the active map
        self.category = STEP_BY_STEP

    def getParameterInfo(self):
        ext = _param("extent", "Extent", "GPExtent", required=False)
        prod = _choice("product", "Product", ["dtm", "dsm"], "dtm")
        mode = _choice("mode", "Output Type", self.MODES, default_mode())
        acs = _param("acs", "Cloud Storage Connection (.acs)", "DEFile", required=False,
                     category=OPTIONS)
        acs.filter.list = ["acs"]
        gdb = _param("gdb", "Geodatabase for Mosaic Dataset", "DEWorkspace", required=False,
                     category=OPTIONS)
        name = _param("md_name", "Mosaic Dataset Name", "GPString", required=False,
                      category=OPTIONS)
        sr = _param("sr", "Coordinate System", "GPSpatialReference", required=False,
                    category=OPTIONS)
        folder = _param("vrt_folder", "Folder for VRT File", "DEFolder", required=False,
                        category=OPTIONS)
        band = _choice("band", "DSM Band", DSM_BANDS, "dsm", required=False)
        out_md = _derived("out_md", "Output mosaic dataset", "DEMosaicDataset")
        out_paths = _derived("out_paths", "Output raster paths", "GPString", multi=True)
        edges = _param("tile_edges", "Add Tile Edges", "GPBoolean", required=False,
                       value=True, category=OPTIONS)
        aprx = _project()
        if aprx is not None:              # sensible defaults from the open project
            try:
                gdb.value = aprx.defaultGeodatabase
                cand = os.path.join(aprx.homeFolder, DEFAULT_ACS_NAME)
                if os.path.exists(cand):
                    acs.value = cand
                folder.value = output_folder()
            except Exception:
                pass
        return [ext, prod, mode, acs, gdb, name, sr, folder, band, out_md, out_paths, edges]

    def isLicensed(self):
        return True

    def updateParameters(self, params):
        p = _by_name(params)
        mode, product = p["mode"].valueAsText, p["product"].valueAsText
        p["acs"].enabled = mode in (MOSAIC, LAYERS)
        p["gdb"].enabled = p["md_name"].enabled = mode == MOSAIC
        p["sr"].enabled = mode in (MOSAIC, VRT)
        p["vrt_folder"].enabled = mode == VRT
        p["band"].enabled = mode == VRT and product == "dsm"
        # A blank mosaic name is filled in at run time from the product and the
        # coordinate system (auto_mosaic_name): the CRS needs the catalogue.

    def updateMessages(self, params):
        p = _by_name(params)
        mode = p["mode"].valueAsText
        if mode == MOSAIC and _is_basic():
            p["mode"].setErrorMessage("Mosaic datasets need a Standard or Advanced licence; "
                                      "choose 'VRT file' (one layer) or 'Separate layers'.")
        problem = sr_problem(p["sr"].value) if mode in (MOSAIC, VRT) else None
        if problem and mode == VRT:
            p["sr"].setErrorMessage(problem)                  # would give a 1 px raster
        elif problem:
            p["sr"].setWarningMessage(problem + " (A mosaic dataset will still draw, but "
                                      "hillshade and slope will be wrong.)")

    @_reports_errors
    def execute(self, params, messages):
        p = _by_name(params)
        product, mode = p["product"].valueAsText, p["mode"].valueAsText
        bbox = extent_or_view(p["extent"].value)
        _log("Extent (lon/lat W, S, E, N): " + ", ".join(f"{v:.4f}" for v in bbox)
             + ("  (crosses 180 degrees)" if gext.crosses_antimeridian(bbox) else ""))
        tiles = find_cells(bbox, product)
        if not tiles:
            raise Gem15Error(NO_TILES)
        _log(f"{len(tiles)} cell(s): " + ", ".join(t.item_id for t in tiles))
        if len(tiles) > WARN_CELLS:
            arcpy.AddWarning(f"{len(tiles)} cells is a large area; display will be slow "
                             "when zoomed out.")
        m = active_map()
        if p["tile_edges"].value is not False:
            add_tile_edges(m, tiles, product)
        if mode == MOSAIC:
            # Pro adds this derived output to the map itself; postExecute styles it.
            p["out_md"].value = add_to_mosaic(
                tiles, bbox, product, ensure_connection(p["acs"].valueAsText),
                p["gdb"].valueAsText or default_gdb(), p["md_name"].valueAsText, p["sr"].value)
            _caveat()
            return
        if mode == LAYERS:
            rasters = acs_paths(ensure_connection(p["acs"].valueAsText), tiles, product)
            names = [f"GEM-15 {product_label(product)} {t.cell_id}" for t in tiles]
        else:
            band = (p["band"].valueAsText or "dsm") if product == "dsm" else None
            path, where = write_vrt_for_extent(bbox, tiles, product, band,
                                               output_folder(p["vrt_folder"].valueAsText),
                                               p["sr"].value, log=_log)
            rasters, names = [path], [dem_layer_name(tiles, product, band, where)]
        if m is not None:
            for path, name in zip(rasters, names):
                add_dem_layer(m, path, name)
        else:
            _log("No active map (running outside Pro?); returning the paths only.")
        _caveat()
        p["out_paths"].values = rasters

    def postExecute(self, params):
        """After Pro has added the mosaic dataset to the map: DRA on, and no
        second copy when the tool is run again for the same mosaic."""
        p = _by_name(params)
        md = p["out_md"].valueAsText
        m = active_map()
        if not md or m is None:
            return
        same = []
        for lyr in m.listLayers():
            try:
                if (lyr.supports("DATASOURCE") and _norm(lyr.dataSource) == _norm(md)
                        and "\\" not in lyr.longName.replace(lyr.name, "", 1)):
                    same.append(lyr)
            except Exception:
                continue
        for extra in same[1:]:
            try:
                m.removeLayer(extra)
            except Exception:
                pass
        if same:
            set_dra(same[0])


def acs_paths(acs, tiles, product):
    """Paths of the tiles' COGs inside the .acs connection (A1)."""
    rasters, kept = cloud_paths(acs, [t.asset(product).href for t in tiles], arcpy.Exists)
    if not arcpy.Exists(rasters[0]):
        arcpy.AddWarning(f"Could not confirm {rasters[0]} exists; check the .acs "
                         "bucket/folder settings. Continuing anyway.")
    if kept:
        _log("Connection has no 'gem-15' folder set; added it to paths.")
    return rasters


def add_to_mosaic(tiles, bbox, product, acs, gdb, name=None, sr=None):
    """Create or extend a mosaic dataset referencing the tiles through the .acs.
    name blank: GEM15_<product>_<CRS>, the CRS being `sr` or the automatic
    choice, so repeat runs in one zone share a mosaic (A6).
    sr None: an existing mosaic keeps its CRS; a new one gets the automatic
    choice (A6). Raises Gem15Error if the tiles cannot go in (A6)."""
    if not name:
        if sr is None:
            sr, choice = auto_sr(bbox, tiles)
            _log("Coordinate system (automatic): " + choice.describe())
        name = auto_mosaic_name(product, sr)
        _log(f"Mosaic dataset name (automatic): {name}")
    md = os.path.join(gdb, name)
    exists = arcpy.Exists(md)
    if exists:
        md_sr = arcpy.Describe(md).spatialReference
        _log(f"{name} exists (coordinate system {md_sr.name}); adding new tiles only.")
    elif sr is None:
        md_sr, choice = auto_sr(bbox, tiles)
        _log("Coordinate system (automatic): " + choice.describe())
    else:
        md_sr = sr
    epsgs = [t.epsg for t in tiles]
    bad = zone_mismatch(sr_utm_zone(md_sr), epsgs)
    if bad:
        try:
            suggest = f"'{auto_mosaic_name(product, auto_sr(bbox, tiles)[0])}'"
        except Gem15Error:
            suggest = "a mosaic named after this extent's coordinate system"
        raise Gem15Error(
            f"'{name}' is in UTM zone {sr_utm_zone(md_sr)}, but these tiles are in zone "
            f"{', '.join(str(z) for z in bad)}, too far away for ArcGIS to add to it. "
            f"Clear 'Mosaic Dataset Name' to use or create {suggest}, or type a new name "
            "and choose a coordinate system that covers both areas, such as "
            "GDA2020 / GA LCC (EPSG 7845) for Australia.")
    rasters = acs_paths(acs, tiles, product)
    if not exists:
        arcpy.management.CreateMosaicDataset(
            gdb, name, md_sr, 1 if product == "dtm" else len(DSM_BANDS), "32_BIT_FLOAT")
    arcpy.management.AddRastersToMosaicDataset(
        md, "Raster Dataset", rasters,
        update_cellsize_ranges="UPDATE_CELL_SIZES",
        update_boundary="UPDATE_BOUNDARY",
        update_overviews="NO_OVERVIEWS",
        duplicate_items_action="EXCLUDE_DUPLICATES",
        build_pyramids="NO_PYRAMIDS",
        calculate_statistics="NO_STATISTICS",
        build_thumbnails="NO_THUMBNAILS",
        estimate_statistics="ESTIMATE_STATISTICS")
    return md


class TerrainStack:
    def __init__(self):
        self.label = "Terrain Stack"
        self.description = (
            "Add live layers over a GEM-15 DEM (mosaic dataset, layer or VRT): hillshade, "
            "slope in degrees and a local relief model (DEM minus its mean over a circle), "
            "with fixed colour ranges. Computed as you pan: nothing is downloaded beyond "
            "what is drawn. Layer files (.lyrx) are also saved.")
        self.canRunInBackground = False  # layers are added to the active map
        self.category = STEP_BY_STEP

    def getParameterInfo(self):
        src = _param("in_raster", "Input DEM", DEM_TYPES)
        which = _choice("derivatives", "Layers", DERIV_LABELS, DERIV_LABELS, multi=True)
        radius = _range(_param("lrm_radius", "LRM Radius (m)", "GPDouble",
                               value=terrain.LRM_RADIUS_M), terrain.LRM_RADIUS_MIN_M, 5000.0)
        alt = _range(_param("altitude", "Sun Altitude (degrees)", "GPDouble",
                            value=terrain.HILLSHADE_ALTITUDE), 1.0, 90.0)
        alt.category = HILLSHADE
        folder = _param("out_folder", "Folder for Layer Files (.lyrx)", "DEFolder",
                        required=False, category=OPTIONS)
        out = _derived("out_layers", "Layer files", "DEFile", multi=True)
        sun = [q for q in _sun_params() if q.name != "altitude"]
        lyr = gem15_layer(active_map())
        if lyr is not None:
            src.value = lyr
        if home_folder():
            folder.value = output_folder()   # must exist: GP validates input folders
        return [src, which, radius, alt, folder, out] + sun

    def isLicensed(self):
        return True

    def updateParameters(self, params):
        p = _by_name(params)
        keys = deriv_keys(p["derivatives"].values)
        p["lrm_radius"].enabled = "lrm" in keys
        _sun_enable(p, "hillshade" in keys)

    def updateMessages(self, params):
        p = _by_name(params)
        _check_terrain_params(p)
        _sun_messages(p)
        if p["in_raster"].value is not None:
            try:
                sr = arcpy.Describe(p["in_raster"].valueAsText).spatialReference
                if sr.type == "Geographic":
                    p["in_raster"].setWarningMessage(
                        "This DEM is in degrees, so hillshade, slope and the LRM radius come "
                        "out wrong. Use a projected (metre) mosaic or VRT.")
            except Exception:
                pass

    @_reports_errors
    def execute(self, params, messages):
        p = _by_name(params)
        desc = arcpy.Describe(p["in_raster"].valueAsText)
        src = desc.catalogPath
        label = getattr(desc, "name", None) or os.path.basename(src)
        label = os.path.splitext(label)[0] if label.lower().endswith((".vrt", ".tif")) else label
        keys = deriv_keys(p["derivatives"].values)
        radius = float(p["lrm_radius"].value)
        az, alt, z = _sun_values(p)
        folder = output_folder(p["out_folder"].valueAsText)
        method = lrm_method()
        if "lrm" in keys and method != "focal":
            arcpy.AddWarning("No Spatial Analyst or Image Analyst licence: the LRM uses a "
                             "convolution, which leaves NoData within the radius of any "
                             "NoData pixel.")
        files = terrain_layer_files(src, label, keys, folder, radius, alt, method, az, z)
        for f in files:
            _log("Wrote " + f)
        m = active_map()
        if m is not None:
            add_layer_files(m, files, f"GEM-15 terrain: {label}")
        else:
            _log("No active map (running outside Pro?); add the layer files yourself.")
        _lrm_note(keys)
        p["out_layers"].values = files


def _check_terrain_params(p):
    if "lrm_radius" in p and p["lrm_radius"].value is not None \
            and float(p["lrm_radius"].value) < terrain.LRM_RADIUS_MIN_M:
        p["lrm_radius"].setErrorMessage(f"Use at least {terrain.LRM_RADIUS_MIN_M:g} m "
                                        "(2 pixels).")
    if "altitude" in p and p["altitude"].value is not None \
            and not 0 < float(p["altitude"].value) <= 90:
        p["altitude"].setErrorMessage("Sun altitude must be between 0 and 90 degrees.")


class ClipForAnalysis:
    def __init__(self):
        self.label = "Clip for Analysis"
        self.description = (
            "Download the GEM-15 pixels for an extent (blank = the current view) into a "
            "local GeoTIFF, on the tiles' native UTM grid when they share one zone, or in a "
            "coordinate system you choose. The download is estimated first. Optionally also "
            "write hillshade, slope and local relief model GeoTIFFs.")
        self.canRunInBackground = False
        self.category = STEP_BY_STEP

    def getParameterInfo(self):
        ext = _param("extent", "Extent", "GPExtent", required=False)
        prod = _choice("product", "Product", ["dtm", "dsm"], "dtm")
        band = _choice("band", "DSM Band", DSM_BANDS, "dsm", required=False)
        out = _param("out_tif", "Output GeoTIFF", "DERasterDataset", direction="Output")
        sr = _param("sr", "Coordinate System", "GPSpatialReference", required=False,
                    category=OPTIONS)
        derivs = "Terrain derivatives"
        which = _choice("derivatives", "Also Write", DERIV_LABELS, None, required=False,
                        multi=True)
        which.category = derivs
        radius = _range(_param("lrm_radius", "LRM Radius (m)", "GPDouble", required=False,
                               value=terrain.LRM_RADIUS_M, category=derivs),
                        terrain.LRM_RADIUS_MIN_M, 5000.0)
        alt = _range(_param("altitude", "Sun Altitude (degrees)", "GPDouble", required=False,
                            value=terrain.HILLSHADE_ALTITUDE, category=derivs), 1.0, 90.0)
        max_mb = _param("max_mb", "Download Limit (MB)", "GPDouble", required=False,
                        value=DEFAULT_MAX_MB, category=OPTIONS)
        outs = _derived("out_derivatives", "Derivative GeoTIFFs", "DERasterDataset",
                        multi=True)
        sun = [q for q in _sun_params(derivs) if q.name != "altitude"]
        if home_folder():
            out.value = os.path.join(output_folder(), "gem15_dtm_clip.tif")
        return [ext, prod, band, out, sr, which, radius, alt, max_mb, outs] + sun

    def isLicensed(self):
        return True

    def updateParameters(self, params):
        p = _by_name(params)
        product = p["product"].valueAsText or "dtm"
        p["band"].enabled = product == "dsm"
        keys = deriv_keys(p["derivatives"].values)
        p["lrm_radius"].enabled = "lrm" in keys
        _sun_enable(p, "hillshade" in keys)
        # The output name follows the product and is never an existing file,
        # until the user types their own.
        out = p["out_tif"]
        if not out.altered and out.valueAsText:
            folder = os.path.dirname(out.valueAsText)
            band = p["band"].valueAsText if product == "dsm" else None
            stem = f"gem15_{product}{'' if band in (None, 'dsm') else '_' + band}_clip.tif"
            try:
                out.value = arcpy.CreateUniqueName(stem, folder)
            except Exception:
                out.value = os.path.join(folder, stem)

    def updateMessages(self, params):
        p = _by_name(params)
        _check_terrain_params(p)
        _sun_messages(p)
        out = p["out_tif"].valueAsText
        if out and (not out.lower().endswith((".tif", ".tiff"))
                    or ".gdb" in os.path.dirname(out).lower()):
            p["out_tif"].setErrorMessage("Give a .tif file in a folder (not a geodatabase).")
        problem = sr_problem(p["sr"].value)
        if problem:
            p["sr"].setErrorMessage(problem)
        if p["extent"].value is not None and p["product"].valueAsText:
            try:
                mb = download.rough_mb(extent_to_lonlat(p["extent"].value),
                                       p["product"].valueAsText)
            except Exception:
                return
            limit = clip_limit(p["max_mb"].value)
            if mb > NEAR_LIMIT * limit:        # warn only when it matters
                over = mb > limit
                p["extent"].setWarningMessage(
                    f"Roughly {mb:,.0f} MB to download, "
                    + ("over your limit, so the tool will stop unless you raise it."
                       if over else f"close to your limit of {limit:,.0f} MB."))

    @_reports_errors
    def execute(self, params, messages):
        p = _by_name(params)
        product = p["product"].valueAsText
        band = (p["band"].valueAsText or "dsm") if product == "dsm" else None
        out_tif = p["out_tif"].valueAsText
        keys = deriv_keys(p["derivatives"].values)
        bbox = extent_or_view(p["extent"].value)
        _log("Extent (lon/lat W, S, E, N): " + ", ".join(f"{v:.4f}" for v in bbox))
        limit = clip_limit(p["max_mb"].value)
        rough = download.rough_mb(bbox, product)
        if rough > 2 * limit:                  # clearly too big: stop before any request
            raise Gem15Error(f"Roughly {rough:,.0f} MB to download, more than your limit of "
                             f"{limit:,.0f} MB. Use a smaller extent, or raise the limit.")
        tiles = find_cells(bbox, product)
        if not tiles:
            raise Gem15Error(NO_TILES)
        est = clip_estimate(bbox, tiles, product)
        note = f"Download: {est.describe()}."
        (arcpy.AddWarning if est.mb > NEAR_LIMIT * limit else _log)(note)
        if est.mb > limit:
            raise Gem15Error(f"That is more than your limit of {limit:,.0f} MB. Use a "
                             "smaller extent, or raise the limit.")
        clip_to_tif(bbox, tiles, product, band, out_tif, p["sr"].value, log=_log,
                    callback=_gdal_progress("Downloading and writing the GeoTIFF..."))
        compute_statistics(out_tif)
        _log("Wrote " + out_tif)
        if keys:
            az, alt, z = _sun_values(p)
            outs = save_derivatives(out_tif, keys,
                                    float(p["lrm_radius"].value or terrain.LRM_RADIUS_M),
                                    alt, None, az, z)
            for f in outs:
                _log("Wrote " + f)
            p["out_derivatives"].values = outs
        _caveat()
