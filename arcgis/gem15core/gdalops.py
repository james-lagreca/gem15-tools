# SPDX-License-Identifier: GPL-2.0-or-later
"""
GDAL-based steps: VRTs clipped to an area, warping across UTM zones, and
local GeoTIFF clips. Needs the GDAL Python bindings (osgeo), which ship with
QGIS and with ArcGIS Pro's default Python environment.

Assumptions (flagged):
 G1. REMOTE_GDAL_CONFIG suits HTTP range reads of COGs. It applies to this
     process only (inside QGIS it also speeds up QGIS for the session).
 G2. When tiles span UTM zones, each zone is mosaicked on its native grid
     first and then warped (bilinear by default) onto a 15 m grid in the
     target CRS. Warping resamples: avoid it for quantitative work by
     choosing a single-zone area or one layer per zone.
 G3. Warps use the exact coordinate transformer (error threshold 0). GDAL's
     default approximates it to 1/8 pixel (about 2 m here), and the result
     then depends on how a read is chunked: the same pixel came out up to
     0.8 m different on screen and in a clip of the same VRT.
"""

import os

from osgeo import gdal, osr

from . import PIXEL_M, USER_AGENT, Gem15Error, crs, extent, stac, vrt

gdal.UseExceptions()

REMOTE_GDAL_CONFIG = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",   # do not list the bucket "folder"
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif",
    "GDAL_HTTP_MULTIRANGE": "YES",
    "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
    "GDAL_HTTP_USERAGENT": USER_AGENT,             # generic; no user details
    "VSI_CACHE": "TRUE",
}


def configure_remote():
    """Apply REMOTE_GDAL_CONFIG to this process (G1)."""
    for k, v in REMOTE_GDAL_CONFIG.items():
        gdal.SetConfigOption(k, v)


def srs(user_input):
    """osr.SpatialReference from 'EPSG:xxxx', a PROJ string or WKT, x = easting/longitude."""
    s = osr.SpatialReference()
    s.SetFromUserInput(user_input)
    s.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return s


def aoi_bounds_in(crs_input, bbox, densify=21, snap=PIXEL_M):
    """Project a lon/lat bbox into a CRS; return (xmin, ymin, xmax, ymax)
    grown outwards to multiples of `snap` metres. Handles the antimeridian."""
    ct = osr.CoordinateTransformation(srs("EPSG:4326"), srs(crs_input))
    w, s, e, n = bbox
    if w > e:
        e += 360                       # PROJ wraps longitude about the central meridian
    xy = [ct.TransformPoint(x, y)[:2] for x, y in extent.densified_ring(w, s, e, n, densify)]
    xs, ys = [p[0] for p in xy], [p[1] for p in xy]
    return vrt.snap_bounds((min(xs), min(ys), max(xs), max(ys)), snap)


def build_vrt(bbox, out_vrt, product="dtm", band_name=None, dst_crs=None,
              resample="bilinear", per_zone=False, tiles=None, fetch=None,
              collection_url=None, log=print):
    """Build a VRT over the GEM-15 tiles covering bbox (W, S, E, N degrees).

    dst_crs  : None or "auto" -> automatic choice (crs.auto_crs): native grid in
               one UTM zone, else a warp. Or any CRS GDAL understands
               ("EPSG:2193", a PROJ string, WKT); then the output is always warped
               unless it equals the single zone of the tiles.
    per_zone : write one native-grid VRT per UTM zone (<stem>_epsg<code>.vrt),
               no resampling; returns the list of paths.
    tiles    : tiles already found (skips the catalogue lookup).
    log      : callable for progress messages (None for silence).
    Returns the output path (or list of paths if per_zone).
    """
    log = log or (lambda *_: None)
    bbox = extent.check(bbox)
    configure_remote()
    if tiles is None:
        kw = {"fetch": fetch}
        if collection_url:
            kw["collection_url"] = collection_url
        tiles = stac.find_tiles(bbox, product, **kw)
    if not tiles:
        raise Gem15Error(f"No GEM-15 tiles cover {bbox}. Check the order (W S E N) "
                         "and that the area is on land.")
    zones = stac.group_by_epsg(tiles)
    log(f"{len(tiles)} tile(s): {', '.join(t.cell_id for t in tiles)}")
    log(f"UTM EPSG code(s): {list(zones)}")

    out_vrt = os.path.abspath(out_vrt)
    stem = os.path.splitext(out_vrt)[0]

    def native(epsg, ztiles, dest):
        info = vrt.write_vrt(ztiles, product, dest, band_name,
                             bounds=aoi_bounds_in(f"EPSG:{epsg}", bbox))
        log(f"Wrote {dest} ({info.cols} x {info.rows} px, native EPSG:{epsg})")
        return info

    if per_zone:
        return [native(z, zt, f"{stem}_epsg{z}.vrt").path for z, zt in zones.items()]

    if dst_crs in (None, "auto"):
        choice = crs.auto_crs(bbox, list(zones))
        log("CRS: " + choice.describe())
        if choice.kind == "utm":
            native(choice.epsg, tiles, out_vrt)
            return out_vrt
        dst = choice.gdal_string()
    else:
        dst = dst_crs
        if len(zones) == 1 and srs(dst).IsSame(srs(f"EPSG:{next(iter(zones))}")):
            native(next(iter(zones)), tiles, out_vrt)
            return out_vrt

    # G2: native mosaic per zone, then a warped VRT per zone, then one mosaic.
    bounds = aoi_bounds_in(dst, bbox)
    tag = "".join(ch if ch.isalnum() else "_" for ch in dst)[:40].strip("_")
    warped = []
    for z, zt in zones.items():
        zone_vrt = native(z, zt, f"{stem}_epsg{z}.vrt").path
        wv = f"{stem}_epsg{z}_to_{tag}.vrt"
        gdal.Warp(wv, zone_vrt, options=gdal.WarpOptions(
            format="VRT", dstSRS=dst, outputBounds=bounds,
            xRes=PIXEL_M, yRes=PIXEL_M, targetAlignedPixels=True,
            resampleAlg=resample, srcNodata="nan", dstNodata="nan",
            errorThreshold=0))                                   # G3
        warped.append(wv)
    gdal.BuildVRT(out_vrt, warped, options=gdal.BuildVRTOptions(
        srcNodata="nan", VRTNodata="nan"))
    ds = gdal.Open(out_vrt, gdal.GA_Update)
    ds.GetRasterBand(1).SetDescription(f"GEM-15 {band_name or product} (m, EGM96)")
    ds = None
    info = gdal.Open(out_vrt)
    log(f"Wrote {out_vrt} ({info.RasterXSize} x {info.RasterYSize} px, warped, "
        f"{resample})")
    return out_vrt


def clip_to_geotiff(src, out_tif, log=print):
    """Copy a (VRT) raster to a compressed local GeoTIFF; downloads only its area."""
    log = log or (lambda *_: None)
    ds = gdal.Open(src)
    mb = ds.RasterXSize * ds.RasterYSize * 4 / 1e6
    log(f"Clipping {ds.RasterXSize} x {ds.RasterYSize} px "
        f"(~{mb:.0f} MB as uncompressed float32) -> {out_tif}")
    gdal.Translate(out_tif, ds, options=gdal.TranslateOptions(
        format="GTiff", creationOptions=[
            "COMPRESS=DEFLATE", "PREDICTOR=3", "TILED=YES", "BIGTIFF=IF_SAFER"]))
    return out_tif
