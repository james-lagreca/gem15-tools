# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""
GEM15Tools.pyt - ArcGIS Pro Python toolbox for Google GEM-15 (15 m) elevation.

Tools (run them in this order the first time):
  1. Connect to GEM-15
       Makes gem15.acs, an anonymous, read-only cloud storage connection to
       the public bucket. Needed for mosaic datasets and separate layers.
  2. Add GEM-15 For Extent
       Finds the GEM-15 1 x 1 degree tiles covering an extent (for example
       the current map view) in the public STAC catalogue, then adds them as
         - Mosaic dataset: one seamless layer that REFERENCES the cloud COGs
           through the .acs; nothing is downloaded. Needs a Standard or
           Advanced licence. Left blank, the mosaic's name follows its
           coordinate system (GEM15_dtm_UTM55S, GEM15_dtm_GA_LCC, ...), so
           running again in the same zone adds tiles to it, and an area in
           another zone gets its own mosaic. Tiles already present are skipped.
         - Separate layers: each tile as its own layer (any licence).
         - VRT file: a small GDAL VRT that streams the COGs over https
           (any licence, no .acs). See A8.
  3. Terrain Stack (live)
       Raster-function layers on any DEM layer, mosaic or VRT, computed on
       the fly as you pan: multidirectional hillshade, slope in degrees and a
       local relief model (LRM = DEM minus its mean over a circle), all with
       DRA (stretch from the pixels in view).
  4. Clip for Analysis
       Writes a local GeoTIFF of an extent, on the tiles' native UTM grid
       when they share one zone (or in a CRS you choose), after estimating
       the download. Optionally writes hillshade, slope and LRM GeoTIFFs too.

Install: Catalog pane > Toolboxes > right-click > Add Toolbox > this file.
Keep the "gem15core" folder next to this file: the toolbox imports it.
ArcGIS Pro keeps imported modules for the whole session, so restart Pro
after updating the toolbox folder.

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
     mean), which would read every full-resolution block in view. The live
     LRM is therefore drawn only at 1:LIVE_LRM_MIN_SCALE and closer.
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

from gem15core import BUCKET, BUCKET_URL, DATA_FOLDER, DSM_BANDS, Gem15Error  # noqa: E402
from gem15core import crs as gcrs  # noqa: E402
from gem15core import download  # noqa: E402
from gem15core import extent as gext  # noqa: E402
from gem15core import paths as gpaths  # noqa: E402
from gem15core import stac, terrain, vrt  # noqa: E402

DEFAULT_ACS_NAME = "gem15.acs"  # looked for in the project home folder
WARN_CELLS = 60                 # warn above this many 1-degree cells
PAD_DEG = 0.005                 # pad the extent slightly (~500 m) after projecting
COLLECTION_ENV = "GEM15_COLLECTION_URL"  # catalogue override (tests or a mirror)
PROBE_TILE = BUCKET_URL + DATA_FOLDER + "/m40_p140/w144sm38/dtm/zall/mosaic.tif"  # A7
LIVE_LRM_MIN_SCALE = 100000     # A9: live LRM not drawn when zoomed out beyond 1:100,000
DEFAULT_MAX_MB = 1000.0         # Clip for Analysis stops above this download unless raised

MOSAIC, LAYERS, VRT = "Mosaic dataset", "Separate layers", "VRT file"

# Terrain derivatives: key, label in the tools, in drawing order (bottom first).
DERIVATIVES = (("hillshade", "Hillshade (multidirectional)"),
               ("slope", "Slope (degrees)"),
               ("lrm", "Local relief model"))
DERIV_LABELS = [label for _, label in DERIVATIVES]


# --------------------------------------------------------------------------
# Catalogue
# --------------------------------------------------------------------------
def find_cells(bbox, product, fetch=None, collection_url=None):
    """Tiles (gem15core.stac.Tile) with a `product` asset whose cell overlaps bbox."""
    kw = {"fetch": fetch}
    collection_url = collection_url or os.environ.get(COLLECTION_ENV)
    if collection_url:
        kw["collection_url"] = collection_url
    return stac.find_tiles(bbox, product, **kw)


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
    """Add rasters to a map as layers with DRA; returns the layers."""
    layers = []
    for path, name in zip(raster_paths, names):
        lyr = m.addDataFromPath(path)
        lyr.name = name
        set_dra(lyr)
        layers.append(lyr)
    return layers


def add_layer_files(m, lyrx_paths, group_name):
    """Add layer files to a new group layer, the first at the bottom."""
    group = m.createGroupLayer(group_name)
    for path in lyrx_paths:
        m.addLayerToGroup(group, arcpy.mp.LayerFile(path), "TOP")
    return group


# --------------------------------------------------------------------------
# VRT route (A8)
# --------------------------------------------------------------------------
def vrt_stem(bbox, band):
    """File stem from the extent, e.g. gem15_dtm_144p100_m37p520_144p140_m37p490."""
    def f(v):
        return f"{v:.3f}".replace("-", "m").replace(".", "p")
    return "gem15_" + band + "_" + "_".join(f(v) for v in bbox)


def write_vrt_for_extent(bbox, tiles, product, band, folder, sr=None, log=None):
    """Write a VRT over the tiles (A8).

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
    out = os.path.join(folder, vrt_stem(bbox, band or product) + ".vrt")
    dst = sr_to_gdal(sr) if sr is not None else None
    zones = stac.group_by_epsg(tiles)
    if len(zones) == 1:
        epsg = next(iter(zones))
        if dst is None or gdalops.srs(dst).IsSame(gdalops.srs(f"EPSG:{epsg}")):
            info = vrt.write_vrt(tiles, product, out, band,
                                 bounds=gdalops.aoi_bounds_in(f"EPSG:{epsg}", bbox),
                                 path_for=gpaths.portable_gdal_path)
            if log:
                log(f"Wrote {info.path} ({info.cols} x {info.rows} px, native EPSG:{epsg}, "
                    "nothing resampled)")
            return info.path
    return gdalops.build_vrt(bbox, out, product, band, dst_crs=dst, tiles=tiles, log=log,
                             portable=True)


# --------------------------------------------------------------------------
# Terrain derivatives (A9-A11; settings from gem15core.terrain)
# --------------------------------------------------------------------------
def deriv_keys(labels):
    """Tool labels (or keys) -> keys in drawing order."""
    chosen = {str(v).strip("'\"") for v in (labels or [])}
    return [k for k, label in DERIVATIVES if k in chosen or label in chosen]


def layer_label(name, key, radius_m=terrain.LRM_RADIUS_M,
                altitude=terrain.HILLSHADE_ALTITUDE):
    if key == "hillshade":
        return f"{name} hillshade (multidirectional, sun {altitude:g} deg)"
    if key == "slope":
        return f"{name} slope (degrees)"
    return f"{name} LRM ({radius_m:g} m)"


def file_suffix(key, radius_m=terrain.LRM_RADIUS_M):
    return f"lrm{radius_m:g}m" if key == "lrm" else key


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


def hillshade(dem, azimuth, altitude=terrain.HILLSHADE_ALTITUDE):
    """Traditional (single-sun) hillshade as a float raster function, 0-255."""
    return arcpy.ia.Float(arcpy.ia.Hillshade(
        dem, azimuth=float(azimuth), altitude=float(altitude),
        z_factor=float(terrain.HILLSHADE_Z_FACTOR), slope_type=1, hillshade_type=0))


def multidirectional_hillshade(dem, altitude=terrain.HILLSHADE_ALTITUDE):
    """Multidirectional hillshade that honours the sun altitude (A12).

    0.5 * sum of cos^2(aspect - az) * hillshade(az) over the four azimuths,
    which with w225 + w315 = w270 + w360 = 1 reduces to
    0.25 * (sum of the four + sin(2a) (h225 - h315) + cos(2a) (h360 - h270)).
    """
    h = {az: hillshade(dem, az, altitude) for az in MD_AZIMUTHS}
    two_a = arcpy.ia.Times(arcpy.ia.Aspect(dem), math.pi / 90.0)   # 2 x aspect, radians
    total = h[225.0] + h[270.0] + h[315.0] + h[360.0]
    return 0.25 * (total + arcpy.ia.Sin(two_a) * (h[225.0] - h[315.0])
                   + arcpy.ia.Cos(two_a) * (h[360.0] - h[270.0]))


def terrain_rasters(src, keys, radius_m=terrain.LRM_RADIUS_M,
                    altitude=terrain.HILLSHADE_ALTITUDE, method=None):
    """{key: arcpy.Raster} of lazy raster functions on the DEM (nothing is read).

    hillshade : multidirectional, sun altitude `altitude`, z factor 1 (T1, A12)
    slope     : degrees, z factor 1 (T3)
    lrm       : DEM minus its mean over a circle of radius_m (T2, A10)
    """
    dem = dem_band(src)
    out = {}
    if "hillshade" in keys:
        out["hillshade"] = multidirectional_hillshade(dem, altitude)
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


def style_layer_json(layer_def, key):
    """Style one CIMRasterLayer definition (a dict from a .lyrx) in place.

    All : DRA (statistics from the pixels in view).
    hillshade : grey, 0.5 % clip.
    slope : white (flat) to black (steep), 0.5 % clip; added switched off,
            since it would hide the hillshade below it.
    lrm : blue (low) - white - red (high), 2.5 standard deviations, 40 %
          transparent so a hillshade below shows through, drawn only at
          1:LIVE_LRM_MIN_SCALE and closer (A9).
    """
    col = layer_def.setdefault("colorizer", {"type": "CIMRasterStretchColorizer"})
    col["statsType"] = "AreaOfView"
    if key in ("hillshade", "slope"):
        col["stretchType"] = "PercentMinimumMaximum"
        col["minPercent"] = col["maxPercent"] = 0.5
    if key == "slope":
        col["colorRamp"] = _ramp(_rgb(255, 255, 255), _rgb(0, 0, 0))
        col.pop("colorScheme", None)
        layer_def["visibility"] = False
    if key == "lrm":
        col["stretchType"] = "StandardDeviations"
        col["standardDeviationParam"] = 2.5
        col["colorRamp"] = _ramp(_rgb(33, 102, 172), _rgb(247, 247, 247), _rgb(178, 24, 43))
        col.pop("colorScheme", None)
        layer_def["transparency"] = 40
        layer_def["minScale"] = LIVE_LRM_MIN_SCALE
    return layer_def


def save_layer_file(raster, name, path, key):
    """Write a .lyrx for a (function) raster, styled for `key`. The layer
    stays live: the file holds the function chain, not pixels."""
    tmp = "gem15_" + uuid.uuid4().hex[:12]
    with arcpy.EnvManager(addOutputsToMap=False):
        lyr = arcpy.management.MakeRasterLayer(raster, tmp).getOutput(0)
        if os.path.exists(path):
            os.remove(path)
        arcpy.management.SaveToLayerFile(lyr, path, "ABSOLUTE")
        arcpy.management.Delete(tmp)
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    for d in doc.get("layerDefinitions", []):
        style_layer_json(d, key)
        d["name"] = d["description"] = name
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
    return path


def terrain_layer_files(src, label, keys, folder, radius_m=terrain.LRM_RADIUS_M,
                        altitude=terrain.HILLSHADE_ALTITUDE, method=None):
    """Write one live .lyrx per derivative into folder; returns their paths,
    bottom layer first."""
    os.makedirs(folder, exist_ok=True)
    rasters = terrain_rasters(src, keys, radius_m, altitude, method)
    out = []
    for key in deriv_keys(keys):
        path = os.path.join(folder, f"{safe_name(label)}_{file_suffix(key, radius_m)}.lyrx")
        out.append(save_layer_file(rasters[key], layer_label(label, key, radius_m, altitude),
                                   path, key))
    return out


def save_derivatives(dem_tif, keys, radius_m=terrain.LRM_RADIUS_M,
                     altitude=terrain.HILLSHADE_ALTITUDE, method=None):
    """Write derivative GeoTIFFs next to a local DEM: <stem>_hillshade.tif,
    <stem>_slope.tif, <stem>_lrm300m.tif. Returns their paths."""
    rasters = terrain_rasters(dem_tif, keys, radius_m, altitude, method)
    stem = os.path.splitext(dem_tif)[0]
    out = []
    with arcpy.EnvManager(compression="LZW", addOutputsToMap=False):
        for key in deriv_keys(keys):
            path = f"{stem}_{file_suffix(key, radius_m)}.tif"
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


def clip_to_tif(bbox, tiles, product, band, out_tif, sr=None, log=None):
    """Write a compressed GeoTIFF of the extent: the tiles' native UTM grid
    if they share one zone, else `sr` or the automatic CRS (A6)."""
    from gem15core import gdalops                      # needs GDAL (osgeo)
    folder = os.path.dirname(os.path.abspath(out_tif))
    os.makedirs(folder, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="gem15_", ignore_cleanup_errors=True) as tmp:
        src = gdalops.build_vrt(bbox, os.path.join(tmp, "clip.vrt"), product, band,
                                dst_crs=sr_to_gdal(sr) if sr is not None else None,
                                tiles=tiles, log=log)
        gdalops.clip_to_geotiff(src, out_tif, log=log)
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


# --------------------------------------------------------------------------
# Toolbox
# --------------------------------------------------------------------------
class Toolbox:
    def __init__(self):
        self.label = "GEM-15 Tools"
        self.alias = "gem15"
        self.tools = [ConnectToGEM15, AddGEM15ForExtent, TerrainStack, ClipForAnalysis]


class ConnectToGEM15:
    def __init__(self):
        self.label = "Connect to GEM-15"
        self.description = ("Create gem15.acs, an anonymous read-only connection to the "
                            "public GEM-15 bucket on Google Cloud Storage.")
        self.canRunInBackground = False

    def getParameterInfo(self):
        folder = _param("out_folder", "Folder for the connection file", "DEFolder")
        name = _param("out_name", "Connection file name", "GPString", value=DEFAULT_ACS_NAME)
        replace = _param("replace", "Replace an existing file", "GPBoolean",
                         required=False, value=False)
        out = _derived("out_acs", "Connection file", "DEFile")
        home = home_folder()
        if home:
            folder.value = home
        return [folder, name, replace, out]

    def isLicensed(self):
        return True

    def updateParameters(self, params):
        return

    def updateMessages(self, params):
        p = _by_name(params)
        folder, name = p["out_folder"].valueAsText, p["out_name"].valueAsText
        if folder and name:
            path = os.path.join(folder, name if name.lower().endswith(".acs") else name + ".acs")
            if os.path.exists(path) and not p["replace"].value:
                p["out_name"].setWarningMessage("This file exists and will be reused; tick "
                                                "'Replace' to make it again.")

    def execute(self, params, messages):
        p = _by_name(params)
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
        self.label = "Add GEM-15 For Extent"
        self.description = ("Find the GEM-15 tiles covering an extent and add them as one "
                            "mosaic dataset (referenced from the cloud), as separate layers, "
                            "or as a VRT file that streams them over https.")
        self.canRunInBackground = False  # layers are added to the active map

    def getParameterInfo(self):
        ext = _param("extent", "Extent", "GPExtent")
        prod = _choice("product", "Product", ["dtm", "dsm"], "dtm")
        mode = _choice("mode", "Output", self.MODES, LAYERS if _is_basic() else MOSAIC)
        acs = _param("acs", "GEM-15 cloud storage connection (.acs)", "DEFile", required=False)
        acs.filter.list = ["acs"]
        gdb = _param("gdb", "Geodatabase for mosaic dataset", "DEWorkspace", required=False)
        name = _param("md_name", "Mosaic dataset name (blank = automatic)", "GPString",
                      required=False)
        sr = _param("sr", "Coordinate system (blank = automatic)", "GPSpatialReference",
                    required=False)
        folder = _param("vrt_folder", "Folder for VRT file", "DEFolder", required=False)
        band = _choice("band", "DSM band (VRT)", DSM_BANDS, "dsm", required=False)
        out_md = _derived("out_md", "Output mosaic dataset", "DEMosaicDataset")
        out_paths = _derived("out_paths", "Output raster paths", "GPString", multi=True)
        aprx = _project()
        if aprx is not None:              # sensible defaults from the open project
            try:
                gdb.value = aprx.defaultGeodatabase
                folder.value = aprx.homeFolder
                cand = os.path.join(aprx.homeFolder, DEFAULT_ACS_NAME)
                if os.path.exists(cand):
                    acs.value = cand
            except Exception:
                pass
        return [ext, prod, mode, acs, gdb, name, sr, folder, band, out_md, out_paths]

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
                                      "choose 'Separate layers' or 'VRT file'.")
        needed = {MOSAIC: ("acs", "gdb"), LAYERS: ("acs",), VRT: ("vrt_folder",)}
        for key in needed.get(mode, ()):
            if not p[key].value:
                p[key].setErrorMessage(f"Required for '{mode}'. Make the .acs with "
                                       "'Connect to GEM-15'." if key == "acs"
                                       else f"Required for '{mode}'.")
        sr = p["sr"].value
        if mode in (MOSAIC, VRT) and sr is not None and getattr(sr, "type", "") == "Geographic":
            p["sr"].setWarningMessage(
                "Geographic (degree) coordinates make hillshade and slope z-scaling wrong. "
                "Leave blank for the automatic projected CRS, ideally the tiles' own UTM "
                "zone, so nothing is resampled.")

    def execute(self, params, messages):
        p = _by_name(params)
        product, mode = p["product"].valueAsText, p["mode"].valueAsText
        try:
            bbox = extent_to_lonlat(p["extent"].value)
        except ValueError as e:
            arcpy.AddError(str(e))
            return
        _log("Extent (lon/lat W, S, E, N): " + ", ".join(f"{v:.4f}" for v in bbox)
             + ("  (crosses 180 degrees)" if gext.crosses_antimeridian(bbox) else ""))
        try:
            tiles = find_cells(bbox, product)
            if not tiles:
                raise Gem15Error("No GEM-15 cells intersect this extent (check coverage).")
            _log(f"{len(tiles)} cell(s): " + ", ".join(t.item_id for t in tiles))
            if len(tiles) > WARN_CELLS:
                arcpy.AddWarning(f"{len(tiles)} cells is a large area; display will be slow "
                                 "when zoomed out.")
            if mode == MOSAIC:
                p["out_md"].value = add_to_mosaic(
                    tiles, bbox, product, p["acs"].valueAsText, p["gdb"].valueAsText,
                    p["md_name"].valueAsText, p["sr"].value)
                _log("Tip: turn on DRA on the layer's symbology if the stretch looks flat.")
                return
            m = active_map()
            if mode == LAYERS:
                rasters = acs_paths(p["acs"].valueAsText, tiles, product)
                names = [f"{t.item_id} {product}" for t in tiles]
            else:
                band = (p["band"].valueAsText or "dsm") if product == "dsm" else None
                rasters = [write_vrt_for_extent(bbox, tiles, product, band,
                                                p["vrt_folder"].valueAsText, p["sr"].value,
                                                log=_log)]
                names = [os.path.splitext(os.path.basename(rasters[0]))[0]]
        except Gem15Error as e:
            arcpy.AddError(str(e))
            return
        if m is not None:
            add_rasters_to_map(m, rasters, names)
        else:
            _log("No active map (running outside Pro?); returning the paths only.")
        p["out_paths"].values = rasters


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
            f"Clear 'Mosaic dataset name' to use or create {suggest}, or type a new name "
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
        self.label = "Terrain Stack (live)"
        self.description = (
            "Add live raster-function layers over a GEM-15 DEM (mosaic dataset, layer or "
            "VRT): multidirectional hillshade, slope in degrees and a local relief model "
            "(DEM minus its mean over a circle), all with DRA. Nothing is downloaded "
            "beyond what is drawn. Layer files (.lyrx) are also saved.")
        self.canRunInBackground = False  # layers are added to the active map

    def getParameterInfo(self):
        src = _param("in_raster", "Input DEM (layer, mosaic dataset or raster)",
                     ["GPRasterLayer", "GPMosaicLayer", "DERasterDataset", "DEMosaicDataset"])
        which = _choice("derivatives", "Layers", DERIV_LABELS, DERIV_LABELS, multi=True)
        radius = _param("lrm_radius", "LRM radius (m)", "GPDouble",
                        value=terrain.LRM_RADIUS_M)
        alt = _param("altitude", "Hillshade sun altitude (degrees)", "GPDouble",
                     value=terrain.HILLSHADE_ALTITUDE)
        folder = _param("out_folder", "Folder for layer files (.lyrx)", "DEFolder",
                        required=False)
        out = _derived("out_layers", "Layer files", "DEFile", multi=True)
        home = home_folder()
        if home:
            folder.value = home           # must exist: GP validates input folders
        return [src, which, radius, alt, folder, out]

    def isLicensed(self):
        return True

    def updateParameters(self, params):
        p = _by_name(params)
        p["lrm_radius"].enabled = "lrm" in deriv_keys(p["derivatives"].values)
        p["altitude"].enabled = "hillshade" in deriv_keys(p["derivatives"].values)

    def updateMessages(self, params):
        p = _by_name(params)
        _check_terrain_params(p)
        if p["in_raster"].value is not None:
            try:
                sr = arcpy.Describe(p["in_raster"].valueAsText).spatialReference
                if sr.type == "Geographic":
                    p["in_raster"].setWarningMessage(
                        "This DEM is in degrees, so hillshade, slope and the LRM radius come "
                        "out wrong. Use a projected (metre) mosaic or VRT.")
            except Exception:
                pass

    def execute(self, params, messages):
        p = _by_name(params)
        desc = arcpy.Describe(p["in_raster"].valueAsText)
        src = desc.catalogPath
        label = getattr(desc, "name", None) or os.path.basename(src)
        label = os.path.splitext(label)[0] if label.lower().endswith((".vrt", ".tif")) else label
        keys = deriv_keys(p["derivatives"].values)
        radius, alt = float(p["lrm_radius"].value), float(p["altitude"].value)
        folder = p["out_folder"].valueAsText or home_folder() or arcpy.env.scratchFolder
        method = lrm_method()
        if "lrm" in keys and method != "focal":
            arcpy.AddWarning("No Spatial Analyst or Image Analyst licence: the LRM uses a "
                             "convolution, which leaves NoData within the radius of any "
                             "NoData pixel.")
        files = terrain_layer_files(src, label, keys, folder, radius, alt, method)
        for f in files:
            _log("Wrote " + f)
        if "lrm" in keys:
            _log(f"The live LRM is drawn only at 1:{LIVE_LRM_MIN_SCALE:,} and closer.")
        m = active_map()
        if m is not None:
            add_layer_files(m, files, f"GEM-15 terrain: {label}")
        else:
            _log("No active map (running outside Pro?); add the layer files yourself.")
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
            "Download the GEM-15 pixels for an extent into a local GeoTIFF, on the tiles' "
            "native UTM grid when they share one zone, or in a coordinate system you choose. "
            "The download is estimated first. Optionally also write hillshade, slope and "
            "local relief model GeoTIFFs.")
        self.canRunInBackground = False

    def getParameterInfo(self):
        ext = _param("extent", "Extent", "GPExtent")
        prod = _choice("product", "Product", ["dtm", "dsm"], "dtm")
        band = _choice("band", "DSM band", DSM_BANDS, "dsm", required=False)
        out = _param("out_tif", "Output GeoTIFF", "DERasterDataset", direction="Output")
        sr = _param("sr", "Coordinate system (blank = native UTM grid, or automatic)",
                    "GPSpatialReference", required=False)
        which = _choice("derivatives", "Also write", DERIV_LABELS, None, required=False,
                        multi=True)
        radius = _param("lrm_radius", "LRM radius (m)", "GPDouble", required=False,
                        value=terrain.LRM_RADIUS_M)
        alt = _param("altitude", "Hillshade sun altitude (degrees)", "GPDouble",
                     required=False, value=terrain.HILLSHADE_ALTITUDE)
        max_mb = _param("max_mb", "Stop if the download would exceed (MB)", "GPDouble",
                        value=DEFAULT_MAX_MB)
        outs = _derived("out_derivatives", "Derivative GeoTIFFs", "DERasterDataset",
                        multi=True)
        home = home_folder()
        if home:
            out.value = os.path.join(home, "gem15_dtm_clip.tif")
        return [ext, prod, band, out, sr, which, radius, alt, max_mb, outs]

    def isLicensed(self):
        return True

    def updateParameters(self, params):
        p = _by_name(params)
        p["band"].enabled = p["product"].valueAsText == "dsm"
        keys = deriv_keys(p["derivatives"].values)
        p["lrm_radius"].enabled = "lrm" in keys
        p["altitude"].enabled = "hillshade" in keys

    def updateMessages(self, params):
        p = _by_name(params)
        _check_terrain_params(p)
        out = p["out_tif"].valueAsText
        if out and (not out.lower().endswith((".tif", ".tiff"))
                    or ".gdb" in os.path.dirname(out).lower()):
            p["out_tif"].setErrorMessage("Give a .tif file in a folder (not a geodatabase).")
        if p["extent"].value is not None and p["product"].valueAsText:
            try:
                mb = download.rough_mb(extent_to_lonlat(p["extent"].value),
                                       p["product"].valueAsText)
            except Exception:
                return
            limit = float(p["max_mb"].value or DEFAULT_MAX_MB)
            msg = (f"Roughly {mb:,.0f} MB to download (from the extent's area; the tool "
                   "works out a closer figure from the tiles before reading anything).")
            if mb > limit:
                msg += " That is over your limit, so the tool will stop unless you raise it."
            p["extent"].setWarningMessage(msg)

    def execute(self, params, messages):
        p = _by_name(params)
        product = p["product"].valueAsText
        band = (p["band"].valueAsText or "dsm") if product == "dsm" else None
        out_tif = p["out_tif"].valueAsText
        keys = deriv_keys(p["derivatives"].values)
        try:
            bbox = extent_to_lonlat(p["extent"].value)
        except ValueError as e:
            arcpy.AddError(str(e))
            return
        _log("Extent (lon/lat W, S, E, N): " + ", ".join(f"{v:.4f}" for v in bbox))
        try:
            tiles = find_cells(bbox, product)
            if not tiles:
                raise Gem15Error("No GEM-15 cells intersect this extent (check coverage).")
            est = clip_estimate(bbox, tiles, product)
            limit = float(p["max_mb"].value or DEFAULT_MAX_MB)
            arcpy.AddWarning(f"Download: {est.describe()} (gem15core.download D1-D4).")
            if est.mb > limit:
                raise Gem15Error(f"That is more than your limit of {limit:,.0f} MB. Use a "
                                 "smaller extent, or raise the limit.")
            clip_to_tif(bbox, tiles, product, band, out_tif, p["sr"].value, log=_log)
        except Gem15Error as e:
            arcpy.AddError(str(e))
            return
        compute_statistics(out_tif)
        _log("Wrote " + out_tif)
        if keys:
            outs = save_derivatives(out_tif, keys,
                                    float(p["lrm_radius"].value or terrain.LRM_RADIUS_M),
                                    float(p["altitude"].value or terrain.HILLSHADE_ALTITUDE))
            for f in outs:
                _log("Wrote " + f)
            p["out_derivatives"].values = outs
