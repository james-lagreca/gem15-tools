# SPDX-License-Identifier: GPL-2.0-or-later
"""Zoomed-out reads of a warped, multi-zone VRT must use the COG overviews
(vrt V6). Synthetic tiles either side of the UTM 59S/60S boundary are served
by a local HTTP server with range requests, and the bytes sent are counted.
No internet access."""
import http.server
import os
import re
import threading

import pytest

np = pytest.importorskip("numpy")
gdal = pytest.importorskip("osgeo.gdal")


from gem15core import cells, gdalops, stac  # noqa: E402


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    """(base URL, folder, byte counter) for a range-capable local server."""
    root = tmp_path_factory.mktemp("web")
    sent = {"bytes": 0}

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_HEAD(self):
            self.do_GET(head=True)

        def do_GET(self, head=False):
            path = root / self.path.lstrip("/").split("?")[0]
            if not path.is_file():
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            data = path.read_bytes()
            rng = self.headers.get("Range")
            if rng:
                a, b = re.match(r"bytes=(\d+)-(\d*)", rng).groups()
                a, b = int(a), min(int(b) if b else len(data) - 1, len(data) - 1)
                body = data[a:b + 1]
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {a}-{b}/{len(data)}")
            else:
                body = data
                self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            if not head:
                self.wfile.write(body)
                sent["bytes"] += len(body)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/", root, sent
    srv.shutdown()


N = 2048          # px; COG-like: DEFLATE, 512 px blocks, overviews 2, 4, 8 (as the real tiles)
# cell: (EPSG, lon/lat box the tile starts from), either side of 174 E. Two tiles
# in zone 59: GDAL gives a VRT of one source implicit overviews anyway, so the
# problem (V6) only shows with several sources in a zone, as is usual.
BOXES = {
    "w172sm42": (32759, (173.26, -41.90, 173.66, -41.60)),
    "w173sm42": (32759, (173.62, -41.90, 174.02, -41.60)),
    "w174sm42": (32760, (173.98, -41.90, 174.40, -41.60)),
}


def _cog_like(path, epsg, box):
    """A 2048 px float32 tile on the 15 m grid with noisy heights (so DEFLATE
    cannot shrink it to nothing). Returns (shape, affine transform)."""
    from osgeo import osr
    xmin, _, _, ymax = gdalops.aoi_bounds_in(f"EPSG:{epsg}", box)
    ds = gdal.GetDriverByName("GTiff").Create(
        str(path), N, N, 1, gdal.GDT_Float32,
        ["TILED=YES", "BLOCKXSIZE=512", "BLOCKYSIZE=512", "COMPRESS=DEFLATE"])
    ds.SetGeoTransform((xmin, 15, 0, ymax, 0, -15))
    s = osr.SpatialReference()
    s.ImportFromEPSG(epsg)
    ds.SetProjection(s.ExportToWkt())
    b = ds.GetRasterBand(1)
    b.SetNoDataValue(float("nan"))
    b.WriteArray((500 + np.random.default_rng(epsg).normal(0, 20, (N, N))).astype("float32"))
    ds.BuildOverviews("AVERAGE", [2, 4, 8])
    ds = None
    return (N, N), (15.0, 0.0, xmin, 0.0, -15.0, ymax)


def _served_tiles(base, root):
    """The BOXES tiles on the local server (written once), as catalogue Tiles."""
    tiles = []
    for cell, (epsg, box) in BOXES.items():
        tif = root / f"{cell}.tif"
        if tif.exists():
            xmin, _, _, ymax = gdalops.aoi_bounds_in(f"EPSG:{epsg}", box)
            shape, tr = (N, N), (15.0, 0.0, xmin, 0.0, -15.0, ymax)
        else:
            shape, tr = _cog_like(tif, epsg, box)
        a = stac.Asset(base + tif.name, epsg, shape, tr, ("dtm",))
        tiles.append(stac.Tile("gem-15_" + cell, cell, "m50_p170", cells.parse_cell(cell),
                               epsg, "", {"dtm": a}))
    return tiles


def test_warped_zoomed_out_read_uses_overviews(served, tmp_path):
    base, root, sent = served
    tiles = _served_tiles(base, root)
    full = sum(os.path.getsize(root / f"{c}.tif") for c in BOXES)
    # No configure_remote() here: with GDAL_DISABLE_READDIR_ON_OPEN, GDAL 3.9.2
    # fetches whole files from this simple test server (not from Google's: see
    # test_live), which would hide what this test is about.
    out = gdalops.build_vrt((173.35, -41.85, 174.30, -41.65), str(tmp_path / "w.vrt"),
                            dst_crs="EPSG:2193", tiles=tiles, log=None)
    ds = gdal.Open(out)
    band = ds.GetRasterBand(1)
    assert band.GetOverviewCount() > 0, "warped VRT exposes no overviews (V6)"
    cols, rows = ds.RasterXSize, ds.RasterYSize
    ds = None
    gdal.VSICurlClearCache()
    sent["bytes"] = 0
    ds = gdal.Open(out)
    arr = ds.GetRasterBand(1).ReadAsArray(buf_xsize=cols // 16, buf_ysize=rows // 16)
    ds = None
    assert np.isfinite(arr).mean() > 0.5
    # Without the overview list this read sent ~50 % of the files; with it ~1 %.
    assert sent["bytes"] < 0.1 * full, (
        f"1/16 read sent {sent['bytes'] / 1e6:.1f} MB of {full / 1e6:.1f} MB: overviews unused")


def test_each_zone_warp_covers_only_its_tiles(served, tmp_path):
    """gdalops G5: a zone's warp stops at its own tiles, so a read elsewhere
    does not run it; pixels match the old whole-area warps. With portable=True
    the zone VRTs carry the GDAL options in their paths (for ArcGIS Pro)."""
    from gem15core import vrt
    base, root, _ = served
    tiles = _served_tiles(base, root)
    bbox, dst = (173.35, -41.85, 174.30, -41.65), "EPSG:2193"
    out = gdalops.build_vrt(bbox, str(tmp_path / "new.vrt"), dst_crs=dst, tiles=tiles,
                            log=None, portable=True)
    top = gdal.Open(out)
    for z in (32759, 32760):
        w = gdal.Open(str(tmp_path / f"new_epsg{z}_to_EPSG_2193.vrt"))
        assert w.RasterXSize < 0.8 * top.RasterXSize, z            # not the whole area
        with open(tmp_path / f"new_epsg{z}.vrt", encoding="utf-8") as fh:
            assert "/vsicurl?list_dir=no&amp;empty_dir=yes&amp;url=http" in fh.read()
    # The old way: every zone warped over the whole area.
    bounds = gdalops.aoi_bounds_in(dst, bbox)
    old = []
    for z in (32759, 32760):
        zt = [t for t in tiles if t.epsg == z]
        zv = vrt.write_vrt(zt, "dtm", tmp_path / f"old_{z}.vrt",
                           bounds=gdalops.aoi_bounds_in(f"EPSG:{z}", bbox)).path
        old.append(str(tmp_path / f"old_{z}_w.vrt"))
        gdal.Warp(old[-1], zv, options=gdal.WarpOptions(
            format="VRT", dstSRS=dst, outputBounds=bounds, xRes=15, yRes=15,
            targetAlignedPixels=True, resampleAlg="bilinear", srcNodata="nan",
            dstNodata="nan", errorThreshold=0, warpOptions=list(gdalops.WARP_OPTIONS)))
    gdal.BuildVRT(str(tmp_path / "old.vrt"), old, options=gdal.BuildVRTOptions(
        srcNodata="nan", VRTNodata="nan"))
    ref = gdal.Open(str(tmp_path / "old.vrt"))
    assert ref.GetGeoTransform() == top.GetGeoTransform()
    assert (ref.RasterXSize, ref.RasterYSize) == (top.RasterXSize, top.RasterYSize)
    # Window across the zone boundary (174 E). The "/vsicurl?" form opens only
    # without a process-wide CPL_VSIL_CURL_ALLOWED_EXTENSIONS (paths P2).
    from osgeo import osr
    x = osr.CoordinateTransformation(gdalops.srs("EPSG:4326"),
                                     gdalops.srs(dst)).TransformPoint(174.0, -41.75)[0]
    col = int((x - top.GetGeoTransform()[0]) / 15)
    win = (col - 400, 0, 800, top.RasterYSize)
    old_opt = gdal.GetConfigOption("CPL_VSIL_CURL_ALLOWED_EXTENSIONS")
    gdal.SetConfigOption("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", None)
    try:
        a = top.GetRasterBand(1).ReadAsArray(*win)
        b = ref.GetRasterBand(1).ReadAsArray(*win)
    finally:
        gdal.SetConfigOption("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", old_opt)
    assert np.isfinite(a).mean() > 0.5
    np.testing.assert_array_equal(a, b)


@pytest.mark.live
def test_live_warped_zoomed_out_read_is_small(tmp_path):
    """The same on the real bucket with the real remote options: three cells,
    two of them in UTM 59S, warped to NZTM2000 and read whole at 256 px."""
    import json
    bbox = (172.5, -41.8, 174.2, -41.6)
    gdalops.configure_remote()
    out = gdalops.build_vrt(bbox, str(tmp_path / "nz.vrt"), log=None,
                            fetch=stac.Fetcher(cache_dir=str(tmp_path / "cache")))
    gdal.SetConfigOption("CPL_VSIL_NETWORK_STATS_ENABLED", "YES")
    gdal.NetworkStatsReset()
    ds = gdal.Open(out)
    arr = ds.GetRasterBand(1).ReadAsArray(buf_xsize=256, buf_ysize=64)
    ds = None
    stats = json.loads(gdal.NetworkStatsGetAsSerializedJSON())
    mb = stats.get("methods", {}).get("GET", {}).get("downloaded_bytes", 0) / 1e6
    assert np.isfinite(arr).mean() > 0.5
    print(f"warped 3-tile VRT, 256 px read: {mb:.2f} MB")
    assert mb < 10, f"downloaded {mb:.1f} MB for a 256 px read of a warped 3-tile VRT"
