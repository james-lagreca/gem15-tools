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
 G3. Warps use the exact coordinate transformer (error threshold 0) and a
     resampling kernel scale fixed at 1 (XSCALE/YSCALE, WARP_OPTIONS). GDAL's
     defaults approximate the transformer to 1/8 pixel and work the kernel
     scale out per processing chunk, so the same pixel came out up to 0.8 m
     different on screen and in a clip of the same VRT (and still < 0.1 m
     with the exact transformer alone). With both, a clip and the screen
     agree exactly (checked 2026-11-15, GDAL 3.9.2 and 3.6.4/QGIS).
 G4. Set process-wide, GDAL_DISABLE_READDIR_ON_OPEN=EMPTY_DIR stops GDAL
     finding side-car files (.ovr, .aux.xml, world files) of every other
     raster, and CPL_VSIL_CURL_ALLOWED_EXTENSIONS=.tif hides any other remote
     file type. Inside QGIS that would change the user's other layers for the
     whole session. So by default the options are set only for paths in the
     GEM-15 bucket (GDAL >= 3.6, SetPathSpecificOption). Checked 2026-11-15
     on GDAL 3.6.4 and 3.9.2: scoped READDIR still removes all 15 side-car
     probes per remote tile and leaves a local .ovr visible.
     GDAL_HTTP_USERAGENT is honoured only process-wide, so scoped mode sends
     GDAL's default (no User-Agent header): still no user details.
 G5. In a warped VRT each zone's native VRT and warp cover only that zone's
     tiles (within the area), not the whole area. GDAL then runs a zone's
     warper only where it has data. Before, every read ran all of them:
     over 7 x 4 degrees of SA/Victoria (3 zones), the two empty warpers took
     60 % of a warm full-resolution 1600 x 1000 px read (checked 2026-11-15,
     GDAL 3.9.2). The exact transformer (G3) keeps pixel values unchanged.
 G6. This module never changes gdal.UseExceptions(): it is one flag for the
     whole process (all of ArcGIS Pro or QGIS, every thread), and other code
     relies on it being off. Every GDAL result is checked here instead, so
     the functions behave the same with exceptions on or off.
 G7. Remote reads give up sooner than GDAL's defaults (GDAL_HTTP_TIMEOUT 60 s,
     5 retries), for the bucket only. ArcGIS Pro's own gdalrc sets 480 s and
     100 retries process-wide, so a stalled connection looked like a hang.
     The system's proxy (as Python finds it) is passed to GDAL for the bucket
     too, unless GDAL already has one: curl does not read Windows' proxy.
"""

import os
import urllib.request

from osgeo import gdal, osr

from . import BUCKET_URL, PIXEL_M, USER_AGENT, Gem15Error, crs, extent, paths, stac, vrt

# G6: leave exceptions as they are (off unless something turned them on), but
# say so explicitly, which only silences GDAL 3.9's FutureWarning about it.
# (GDAL 3.6 keeps these settings as a stack and can refuse; that is harmless.)
for _mod in (osr, gdal):
    try:
        if not _mod.GetUseExceptions():
            _mod.DontUseExceptions()
    except RuntimeError:
        pass

REMOTE_GDAL_CONFIG = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",   # do not list the bucket "folder"
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif",
    "GDAL_HTTP_MULTIRANGE": "YES",
    "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
    "GDAL_HTTP_USERAGENT": USER_AGENT,             # generic; no user details
    "VSI_CACHE": "TRUE",
    "GDAL_HTTP_TIMEOUT": "60",                     # G7
    "GDAL_HTTP_CONNECTTIMEOUT": "20",
    "GDAL_HTTP_MAX_RETRY": "5",
    "GDAL_HTTP_RETRY_DELAY": "1",
}


# G3: 15 m to 15 m, so the resampling kernel's scale is fixed at 1 instead of
# being worked out per processing chunk (which made values chunk-dependent).
WARP_OPTIONS = ("XSCALE=1", "YSCALE=1")

# Options that would change how GDAL opens anything else if set process-wide (G4, G7).
_BUCKET_ONLY = ("GDAL_DISABLE_READDIR_ON_OPEN", "CPL_VSIL_CURL_ALLOWED_EXTENSIONS",
                "GDAL_HTTP_TIMEOUT", "GDAL_HTTP_CONNECTTIMEOUT", "GDAL_HTTP_MAX_RETRY",
                "GDAL_HTTP_RETRY_DELAY")


def system_proxy():
    """'host:port' of the system's https (else http) proxy as Python sees it
    (registry on Windows, environment elsewhere), or None."""
    try:
        proxies = urllib.request.getproxies()
    except Exception:                              # odd registry contents
        return None
    url = proxies.get("https") or proxies.get("http")
    if not url:
        return None
    return url.split("://", 1)[-1].rstrip("/") or None   # curl: plain = HTTP proxy


def configure_remote(scoped=True, prefix=None, proxy=None):
    """Apply REMOTE_GDAL_CONFIG (G1, G7).

    scoped : True (default) limits the options to paths starting with
             `prefix` where GDAL allows it (G4); with an older GDAL only the
             options that are harmless process-wide are set. False sets all
             of them process-wide (fine for a stand-alone script).
    prefix : default "/vsicurl/" + the bucket URL (tests pass a local server).
    proxy  : "host:port" (e.g. from QGIS's settings); default: the system's
             proxy, if GDAL has none of its own (G7).
    Returns "scoped" or "global", saying how the options were applied.
    """
    prefix = prefix or "/vsicurl/" + BUCKET_URL
    options = dict(REMOTE_GDAL_CONFIG)
    if not gdal.GetConfigOption("GDAL_HTTP_PROXY"):
        proxy = proxy or system_proxy()
        if proxy:
            options["GDAL_HTTP_PROXY"] = proxy
    set_for_path = (getattr(gdal, "SetPathSpecificOption", None)      # GDAL >= 3.6
                    or getattr(gdal, "SetCredential", None))           # GDAL 3.5 name
    if scoped and set_for_path:
        for k, v in options.items():
            set_for_path(prefix, k, v)
        return "scoped"
    for k, v in options.items():
        if scoped and k in _BUCKET_ONLY:
            continue                     # costs extra requests, but leaves other data alone
        gdal.SetConfigOption(k, v)
    return "global"


def _failed(what, err=None):
    """Gem15Error naming `what`, with GDAL's last message (G6)."""
    msg = gdal.GetLastErrorMsg() or (str(err) if err else "") or "no message from GDAL"
    return Gem15Error(f"{what} failed: {msg}")


def _run(what, fn, *args, **kwargs):
    """Call a GDAL function; raise Gem15Error if it raised or returned None (G6).
    GDAL's own printing of errors is muted meanwhile (the handler stack is
    per thread, so QGIS's logging elsewhere is untouched); the message is
    still read with GetLastErrorMsg and reported in the Gem15Error."""
    gdal.ErrorReset()
    gdal.PushErrorHandler("CPLQuietErrorHandler")
    try:
        result = fn(*args, **kwargs)
        if result is None:                         # exceptions off
            raise _failed(what)
    except RuntimeError as e:                      # exceptions on
        raise _failed(what, e) from e
    finally:
        gdal.PopErrorHandler()
    return result


def srs(user_input):
    """osr.SpatialReference from 'EPSG:xxxx', a PROJ string or WKT, x = easting/longitude."""
    s = osr.SpatialReference()
    try:
        err = s.SetFromUserInput(user_input)
    except RuntimeError as e:                      # exceptions on (G6)
        raise Gem15Error(f"Unknown coordinate system: {str(user_input)[:80]!r} ({e})") from e
    if err:                                        # exceptions off: an OGR error code
        raise Gem15Error(f"Unknown coordinate system: {str(user_input)[:80]!r}")
    s.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return s


def check_metric_crs(crs_input):
    """Raise Gem15Error unless the CRS is projected in metres. Pixels are 15 m:
    in degrees a 15-unit pixel covers the globe (a 1 x 1 px raster), and in
    feet x/y and height would disagree for hillshade and slope."""
    s = srs(crs_input)
    name = s.GetName() or str(crs_input)[:60]
    if s.IsGeographic() or not s.IsProjected():
        raise Gem15Error(f"'{name}' uses degrees. Choose a projected coordinate system in "
                         "metres, or leave it blank for the automatic choice.")
    if abs(s.GetLinearUnits() - 1.0) > 1e-9:
        raise Gem15Error(f"'{name}' is in {s.GetLinearUnitsName() or 'non-metre units'}; "
                         "GEM-15 heights are in metres. Choose a projected coordinate "
                         "system in metres, or leave it blank for the automatic choice.")


def _transform(src, dst):
    ct = osr.CreateCoordinateTransformation(srs(src), srs(dst))
    if ct is None:
        raise Gem15Error(f"Cannot transform between {src!r} and {dst!r}.")
    return ct


def _project_ring(ct, ring):
    """Project points, skipping any PROJ cannot handle; at least 3 must succeed."""
    xy = []
    for x, y in ring:
        try:
            p = ct.TransformPoint(x, y)[:2]
        except RuntimeError:                       # exceptions on (G6)
            continue
        if all(v == v and abs(v) != float("inf") for v in p):
            xy.append(p)
    if len(xy) < 3:
        raise Gem15Error("Could not project this area into the chosen coordinate system.")
    return xy


def aoi_bounds_in(crs_input, bbox, densify=21, snap=PIXEL_M):
    """Project a lon/lat bbox into a CRS; return (xmin, ymin, xmax, ymax)
    grown outwards to multiples of `snap` metres. Handles the antimeridian."""
    ct = _transform("EPSG:4326", crs_input)
    w, s, e, n = bbox
    if w > e:
        e += 360                       # PROJ wraps longitude about the central meridian
    xy = _project_ring(ct, extent.densified_ring(w, s, e, n, densify))
    xs, ys = [p[0] for p in xy], [p[1] for p in xy]
    return vrt.snap_bounds((min(xs), min(ys), max(xs), max(ys)), snap)


def bounds_between(bounds, src_crs, dst_crs, densify=21, snap=PIXEL_M):
    """Project (xmin, ymin, xmax, ymax) from one projected CRS to another along
    its densified outline; return the enclosing bounds snapped outwards."""
    ct = _transform(src_crs, dst_crs)
    x0, y0, x1, y1 = bounds
    xy = _project_ring(ct, extent.densified_ring(x0, y0, x1, y1, densify))
    xs, ys = [p[0] for p in xy], [p[1] for p in xy]
    return vrt.snap_bounds((min(xs), min(ys), max(xs), max(ys)), snap)


def _intersect(a, b):
    """Overlap of two (xmin, ymin, xmax, ymax) boxes, or None."""
    box = (max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))
    return box if box[0] < box[2] and box[1] < box[3] else None


def zone_bounds(epsg, ztiles, bbox, product="dtm"):
    """The area (lon/lat bbox) within one zone's tiles, in that zone's CRS (G5);
    None if they do not meet."""
    fps = [t.asset(product).footprint() for t in ztiles]
    union = (min(f[0] for f in fps), min(f[1] for f in fps),
             max(f[2] for f in fps), max(f[3] for f in fps))
    return _intersect(aoi_bounds_in(f"EPSG:{epsg}", bbox), union)


def build_vrt(bbox, out_vrt, product="dtm", band_name=None, dst_crs=None,
              resample="bilinear", per_zone=False, tiles=None, fetch=None,
              collection_url=None, log=print, portable=False):
    """Build a VRT over the GEM-15 tiles covering bbox (W, S, E, N degrees).

    dst_crs  : None or "auto" -> automatic choice (crs.auto_crs): native grid in
               one UTM zone, else a warp. Or any CRS GDAL understands
               ("EPSG:2193", a PROJ string, WKT); then the output is always warped
               unless it equals the single zone of the tiles.
    per_zone : write one native-grid VRT per UTM zone (<stem>_epsg<code>.vrt),
               no resampling; returns the list of paths.
    tiles    : tiles already found (skips the catalogue lookup).
    log      : callable for progress messages (None for silence).
    portable : write tile paths with their GDAL options in them
               (paths.portable_gdal_path), for VRTs that another program or a
               later session opens without our GDAL settings (ArcGIS Pro).
               Leave False for VRTs read in this process: see that function.
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
    path_for = paths.portable_gdal_path if portable else paths.gdal_path

    def native(epsg, ztiles, dest, bounds=None):
        info = vrt.write_vrt(ztiles, product, dest, band_name,
                             bounds=bounds or aoi_bounds_in(f"EPSG:{epsg}", bbox),
                             path_for=path_for)
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
        check_metric_crs(dst)          # degrees or feet would give a 1 x 1 px raster
        if len(zones) == 1 and srs(dst).IsSame(srs(f"EPSG:{next(iter(zones))}")):
            native(next(iter(zones)), tiles, out_vrt)
            return out_vrt

    # G2: native mosaic per zone, then a warped VRT per zone, then one mosaic.
    # G5: each zone's VRT and warp cover only that zone's tiles in the area.
    bounds = aoi_bounds_in(dst, bbox)
    tag = "".join(ch if ch.isalnum() else "_" for ch in dst)[:40].strip("_")
    warped = []
    for z, zt in zones.items():
        zb = zone_bounds(z, zt, bbox, product)
        wb = zb and _intersect(bounds_between(zb, f"EPSG:{z}", dst), bounds)
        if wb is None:
            log(f"EPSG:{z}: its tiles do not reach the area; skipped.")
            continue
        zone_vrt = native(z, zt, f"{stem}_epsg{z}.vrt", zb).path
        wv = f"{stem}_epsg{z}_to_{tag}.vrt"
        _run(f"Warping EPSG:{z}", gdal.Warp, wv, zone_vrt, options=gdal.WarpOptions(
            format="VRT", dstSRS=dst, outputBounds=wb,
            xRes=PIXEL_M, yRes=PIXEL_M, targetAlignedPixels=True,
            resampleAlg=resample, srcNodata="nan", dstNodata="nan",
            errorThreshold=0, warpOptions=list(WARP_OPTIONS)))    # G3
        warped.append(wv)
    if not warped:
        raise Gem15Error(f"No GEM-15 tiles reach {bbox}.")
    _run("Building the VRT", gdal.BuildVRT, out_vrt, warped, options=gdal.BuildVRTOptions(
        outputBounds=bounds, srcNodata="nan", VRTNodata="nan"))
    ds = _run(f"Opening {out_vrt}", gdal.Open, out_vrt, gdal.GA_Update)
    ds.GetRasterBand(1).SetDescription(f"GEM-15 {band_name or product} (m, EGM96)")
    cols, rows = ds.RasterXSize, ds.RasterYSize
    ds = None
    log(f"Wrote {out_vrt} ({cols} x {rows} px, warped, {resample})")
    return out_vrt


def clip_to_geotiff(src, out_tif, log=print, callback=None):
    """Copy a (VRT) raster to a compressed local GeoTIFF; downloads only its area.

    callback : optional GDAL progress function (fraction, message, data) that
               returns 0 to cancel; then the partial file is removed and
               Gem15Error("Cancelled.") raised.
    """
    log = log or (lambda *_: None)
    ds = _run(f"Opening {src}", gdal.Open, src)
    mb = ds.RasterXSize * ds.RasterYSize * 4 / 1e6
    log(f"Clipping {ds.RasterXSize} x {ds.RasterYSize} px "
        f"(~{mb:.0f} MB as uncompressed float32) -> {out_tif}")
    cancelled = []

    def progress(frac, msg, data):
        ok = callback(frac, msg, data) if callback else 1
        if not ok:
            cancelled.append(True)
        return ok

    try:
        written = _run("Writing the GeoTIFF", gdal.Translate, out_tif, ds,
                       options=gdal.TranslateOptions(format="GTiff", callback=progress,
                                                     creationOptions=[
                           "COMPRESS=DEFLATE", "PREDICTOR=3", "TILED=YES", "BIGTIFF=IF_SAFER"]))
        written.FlushCache()
        del written                                # close, so the file is complete
    except Gem15Error:
        ds = None
        try:
            gdal.GetDriverByName("GTiff").Delete(out_tif)   # no half-written file
        except Exception:
            pass
        if cancelled:
            raise Gem15Error("Cancelled.")
        raise
    return out_tif
