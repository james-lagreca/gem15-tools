# SPDX-License-Identifier: GPL-2.0-or-later
"""Trial of the live LRM (VRT KernelFilteredSource): accuracy against numpy,
what QGIS reads zoomed in and out, and speed. Results are recorded in
qgis/gem15/lrm_vrt.py (L1-L4)."""
import math
import time

import pytest

np = pytest.importorskip("numpy")

R_M = 300.0
PX = 15.0
SIZE = (700, 800)                       # rows, cols


@pytest.fixture(scope="module")
def lrm_files(tmp_path_factory, qgis_app):
    """A synthetic 15 m UTM DEM with ridges, noise and a void, its core-written
    mosaic VRT and the LRM VRT over it."""
    from osgeo import gdal, osr
    from gem15 import lrm_vrt
    from gem15.gem15core import stac, vrt

    d = tmp_path_factory.mktemp("lrm")
    rows, cols = SIZE
    y, x = np.mgrid[0:rows, 0:cols]
    rng = np.random.default_rng(42)
    dem = (400 + 0.08 * x - 0.03 * y + 25 * np.sin(x / 37.0) * np.cos(y / 53.0)
           + 3 * (x > 400) + rng.normal(0, 0.4, SIZE))       # a 3 m "scarp" at col 400
    dem[300:330, 500:520] = np.nan
    dem = dem.astype("float32")
    left, top = 300000.0, 5900010.0
    tif = d / "dem.tif"
    ds = gdal.GetDriverByName("GTiff").Create(str(tif), cols, rows, 1, gdal.GDT_Float32,
                                              ["TILED=YES", "BLOCKXSIZE=512", "BLOCKYSIZE=512"])
    ds.SetGeoTransform((left, PX, 0, top, 0, -PX))
    s = osr.SpatialReference()
    s.ImportFromEPSG(32755)
    ds.SetProjection(s.ExportToWkt())
    ds.GetRasterBand(1).SetNoDataValue(float("nan"))
    ds.GetRasterBand(1).WriteArray(dem)
    ds.BuildOverviews("AVERAGE", [2, 4])
    ds = None
    asset = stac.Asset(href=tif.as_uri(), epsg=32755, shape=(rows, cols),
                       transform=(PX, 0.0, left, 0.0, -PX, top), bands=("dtm",))
    tile = stac.Tile("gem-15_w144sm38", "w144sm38", "m40_p140", (144, -38, 145, -37),
                     32755, "", {"dtm": asset})
    base = d / "base.vrt"
    vrt.write_vrt([tile], "dtm", base)
    lrm = lrm_vrt.write_lrm_vrt(str(base), str(d / "lrm.vrt"), R_M)
    return {"dem": dem.astype("float64"), "base": str(base), "lrm": lrm}


def _reference(dem, r):
    """numpy LRM with GDAL's conventions: edge pixels repeated, and NaN
    anywhere in the (2r+1)^2 square gives NaN (lrm_vrt L1, L4)."""
    from gem15.gem15core import terrain
    offs = terrain.circle_offsets(r * PX, PX)
    rows, cols = dem.shape
    pad = np.pad(dem, r, mode="edge")
    total = np.zeros_like(dem)
    for dx, dy in offs:
        total += pad[r + dy:r + dy + rows, r + dx:r + dx + cols]
    ref = dem - total / len(offs)
    nan = np.pad(np.isnan(dem), r, mode="edge").astype(np.int32)
    c = np.pad(nan.cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    k = 2 * r + 1
    square = c[k:, k:] - c[:-k, k:] - c[k:, :-k] + c[:-k, :-k]
    ref[square > 0] = np.nan
    return ref


def _block(layer, rect, w, h):
    b = layer.dataProvider().block(1, rect, w, h)
    return np.frombuffer(bytes(b.data()), dtype=np.float32).reshape(h, w).astype("float64")


def test_lrm_vrt_matches_numpy(lrm_files, gdal_exceptions_off):
    from osgeo import gdal
    r = int(round(R_M / PX))
    ds = gdal.Open(lrm_files["lrm"])
    got = ds.GetRasterBand(1).ReadAsArray().astype("float64")
    ds = None
    ref = _reference(lrm_files["dem"], r)
    np.testing.assert_array_equal(np.isnan(got), np.isnan(ref))          # L4: square NaN halo
    ok = np.isfinite(ref)
    assert np.max(np.abs(got[ok] - ref[ok])) < 1e-4                      # L1
    # The 3 m step shows as about +/-1.5 m either side of it; flat-ish ground is near 0.
    assert np.nanmedian(np.abs(got)) < 3


def test_qgis_reads_lrm_when_zoomed_in(lrm_files, qgis_app, gdal_exceptions_off):
    """QGIS asks GDAL for native pixels at 1:1 and when zoomed in, so the kernel runs."""
    from osgeo import gdal
    from qgis.core import QgsRasterLayer, QgsRectangle
    layer = QgsRasterLayer(lrm_files["lrm"], "lrm", "gdal")
    assert layer.isValid()
    ds = gdal.Open(lrm_files["lrm"])
    full = ds.GetRasterBand(1).ReadAsArray().astype("float64")
    ds = None
    ext = layer.extent()
    rows, cols = SIZE
    # 1:1, a 200 x 200 px window at the raster's own grid.
    x0, y0 = ext.xMinimum() + 100 * PX, ext.yMaximum() - 300 * PX
    win = QgsRectangle(x0, y0, x0 + 200 * PX, y0 + 200 * PX)
    one = _block(layer, win, 200, 200)
    np.testing.assert_allclose(one, full[100:300, 100:300], atol=1e-4, equal_nan=True)
    # Zoomed in 2.5x: QGIS reads native pixels and upsamples (nearest) itself.
    zin = _block(layer, win, 500, 500)
    np.testing.assert_allclose(zin[::5, ::5], full[100:300:2, 100:300:2], atol=1e-4, equal_nan=True)


def test_qgis_zoomed_out_is_not_lrm(lrm_files, qgis_app, gdal_exceptions_off):
    """Documents lrm_vrt L3: below full resolution GDAL skips the kernel, so the
    layer must only be drawn at large scales (settings P3). If this starts to
    fail, a newer GDAL filters reduced-resolution reads and P3 could go."""
    from qgis.core import QgsRasterLayer
    layer = QgsRasterLayer(lrm_files["lrm"], "lrm", "gdal")
    rows, cols = SIZE
    out = _block(layer, layer.extent(), cols // 3, rows // 3)
    assert np.nanmedian(np.abs(out)) > 100, "zoomed-out read now looks like an LRM"


def test_lrm_speed(lrm_files, qgis_app, gdal_exceptions_off, record_property):
    """Speed of a native-resolution read through QGIS (lrm_vrt L2). The bound is
    loose: it only catches a kernel that has become unusably slow."""
    from qgis.core import QgsRasterLayer
    layer = QgsRasterLayer(lrm_files["lrm"], "lrm", "gdal")
    rows, cols = SIZE
    t = time.perf_counter()
    _block(layer, layer.extent(), cols, rows)
    dt = time.perf_counter() - t
    mpx_s = rows * cols / dt / 1e6
    record_property("lrm_mpx_per_s", round(mpx_s, 3))
    print(f"live LRM, {R_M:g} m radius: {mpx_s:.2f} Mpx/s; a 1920 x 1080 view at 1:25,000 "
          f"(96 dpi) reads {math.ceil(1920 * 6.6 / 15) * math.ceil(1080 * 6.6 / 15) / 1e6:.2f} Mpx")
    assert mpx_s > 0.05
