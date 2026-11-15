"""GEM15Tools.pyt with the real arcpy against the live, public bucket.

Skipped unless GEM15_LIVE=1 and arcpy is present. Anonymous, read-only
requests only; a run downloads a few MB (small areas, a few COG blocks).
"""
import os

import pytest

arcpy = pytest.importorskip("arcpy")
np = pytest.importorskip("numpy")
gdal = pytest.importorskip("osgeo.gdal")

import test_arcpy_tools as offline  # noqa: E402
from gem15core import extent as gext  # noqa: E402
from gem15core import gdalops, vrt  # noqa: E402

pytestmark = [pytest.mark.live, pytest.mark.arcpy]
pyt, tb = offline.pyt, offline.tb          # the same module-scoped fixtures
lonlat_extent = offline.lonlat_extent

VIC = (144.10, -37.52, 144.14, -37.49)          # UTM 55S
NZ = (173.97, -41.75, 174.03, -41.72)           # across 174 E: UTM 59S and 60S
CENTRAL = (131.9, -25.1, 132.1, -24.9)          # UTM 52S and 53S
ZONE52 = (129.90, -25.60, 129.95, -25.55)       # UTM 52S only (failed in Pro, 2026-11-15)


@pytest.fixture(scope="module")
def acs(tb, tmp_path_factory):
    res = tb.ConnectToGEM15(str(tmp_path_factory.mktemp("acs")), "gem15.acs")
    path = res.getOutput(0)
    assert os.path.exists(path) and "Connection checked" in res.getMessages(0)
    return path


@pytest.fixture(scope="module")
def gdb(tmp_path_factory):
    folder = str(tmp_path_factory.mktemp("gdb"))
    return arcpy.management.CreateFileGDB(folder, "live.gdb").getOutput(0)


def count(md):
    return int(arcpy.management.GetCount(md).getOutput(0))


def test_connect_reuses_then_replaces(tb, acs):
    res = tb.ConnectToGEM15(os.path.dirname(acs), "gem15")       # ".acs" added
    assert res.getOutput(0) == acs and "Kept existing" in res.getMessages(0)
    res = tb.ConnectToGEM15(os.path.dirname(acs), "gem15.acs", True)
    assert "Created" in res.getMessages(0) and "Connection checked" in res.getMessages(0)


def test_mosaic_victoria_with_live_terrain(tb, pyt, acs, gdb):
    res = tb.AddGEM15ForExtent(lonlat_extent(VIC), "dtm", "Mosaic dataset", acs, gdb)
    md = res.getOutput(0)
    assert "automatic" in res.getMessages(0)
    assert os.path.basename(md) == "GEM15_dtm_UTM55S"                   # blank name: from CRS
    assert arcpy.Describe(md).spatialReference.factoryCode == 32755    # tiles' own zone
    n = count(md)
    assert n >= 1
    tb.AddGEM15ForExtent(lonlat_extent(VIC), "dtm", "Mosaic dataset", acs, gdb)
    assert count(md) == n                                               # duplicates skipped
    # The live derivatives read pixels from the cloud through the mosaic.
    x0, _, _, y1 = gdalops.aoi_bounds_in("EPSG:32755", VIC)
    ranges = {"hillshade": (0, 255), "slope": (0, 90), "lrm": (-200, 200)}
    for key, r in pyt.terrain_rasters(md, list(ranges)).items():
        a = np.squeeze(r.read(origin_coordinate=(x0 + 600, y1 - 600), ncols=32, nrows=32))
        a = a.astype(float)
        a[a > 1e30] = np.nan
        lo, hi = ranges[key]
        assert np.isfinite(a).mean() > 0.9 and lo <= np.nanmin(a) and np.nanmax(a) <= hi, key


def test_mosaic_across_zones_gets_nztm(tb, acs, gdb):
    res = tb.AddGEM15ForExtent(lonlat_extent(NZ), "dtm", "Mosaic dataset", acs, gdb,
                               "GEM15_nz")
    md = res.getOutput(0)
    assert arcpy.Describe(md).spatialReference.factoryCode == 2193
    assert count(md) >= 2                                   # tiles from both zones went in


def test_utm_mosaic_refuses_distant_zones(tb, acs, gdb):
    tb.AddGEM15ForExtent(lonlat_extent(VIC), "dtm", "Mosaic dataset", acs, gdb, "GEM15_vic")
    # The message names the mosaic a blank name would use (52S + 53S -> GA LCC).
    with pytest.raises(arcpy.ExecuteError, match=r"UTM zone 55[^\n]*'GEM15_dtm_GA_LCC'"):
        tb.AddGEM15ForExtent(lonlat_extent(CENTRAL), "dtm", "Mosaic dataset", acs, gdb,
                             "GEM15_vic")


def test_blank_name_gives_each_zone_its_own_mosaic(tb, acs, gdb):
    """What failed in Pro: with one fixed default name, a second area in a
    distant zone was refused. Blank names now follow the coordinate system."""
    vic = tb.AddGEM15ForExtent(lonlat_extent(VIC), "dtm", "Mosaic dataset", acs, gdb)
    z52 = tb.AddGEM15ForExtent(lonlat_extent(ZONE52), "dtm", "Mosaic dataset", acs, gdb)
    assert os.path.basename(vic.getOutput(0)) == "GEM15_dtm_UTM55S"
    assert os.path.basename(z52.getOutput(0)) == "GEM15_dtm_UTM52S"
    assert arcpy.Describe(z52.getOutput(0)).spatialReference.factoryCode == 32752
    assert count(z52.getOutput(0)) >= 1


def test_separate_layers_return_paths_headless(tb, acs):
    res = tb.AddGEM15ForExtent(lonlat_extent(VIC), "dtm", "Separate layers", acs)
    paths = res.getOutput(1).split(";")
    assert paths and all(p.endswith("mosaic.tif") and arcpy.Exists(p) for p in paths)


def test_vrt_with_portable_sources(tb, tmp_path, monkeypatch):
    res = tb.AddGEM15ForExtent(lonlat_extent(VIC), "dtm", "VRT file", None, None, None,
                               None, str(tmp_path))
    path = res.getOutput(1)
    with open(path, encoding="utf-8") as fh:
        assert "/vsicurl?list_dir=no&amp;empty_dir=yes&amp;url=https%3A%2F%2F" in fh.read()
    a = arcpy.RasterToNumPyArray(arcpy.Raster(path), nodata_to_value=np.nan)
    assert np.isfinite(a).mean() > 0.9
    # osgeo's GDAL reads the "/vsicurl?" form only without a process-wide
    # CPL_VSIL_CURL_ALLOWED_EXTENSIONS (paths P2); other tests set it.
    old = gdal.GetConfigOption("CPL_VSIL_CURL_ALLOWED_EXTENSIONS")
    gdal.SetConfigOption("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", None)
    try:
        np.testing.assert_array_equal(a, gdal.Open(path).ReadAsArray())
    finally:
        gdal.SetConfigOption("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", old)


def test_vrt_across_zones(tb, tmp_path):
    """Warped to NZTM; plain /vsicurl/ sources so GDAL's warp can open them."""
    res = tb.AddGEM15ForExtent(lonlat_extent(NZ), "dtm", "VRT file", None, None, None,
                               None, str(tmp_path))
    path = res.getOutput(1)
    assert arcpy.Describe(path).spatialReference.factoryCode == 2193
    a = arcpy.RasterToNumPyArray(arcpy.Raster(path), nodata_to_value=np.nan)
    assert np.isfinite(a).mean() > 0.9                       # not silently empty
    np.testing.assert_array_equal(a, gdal.Open(path).ReadAsArray())


def test_clip_matches_streamed_pixels(tb, pyt, tmp_path):
    out = str(tmp_path / "vic.tif")
    res = tb.ClipForAnalysis(lonlat_extent(VIC), "dtm", None, out)
    assert "Download: about" in res.getMessages(0)
    ds = gdal.Open(out)
    gt, nx, ny = ds.GetGeoTransform(), ds.RasterXSize, ds.RasterYSize
    bounds = (gt[0], gt[3] + gt[5] * ny, gt[0] + gt[1] * nx, gt[3])
    tiles = pyt.find_cells(gext.pad(VIC, pyt.PAD_DEG), "dtm")
    gdalops.configure_remote()
    ref = vrt.write_vrt(tiles, "dtm", tmp_path / "ref.vrt", bounds=bounds)
    clip = ds.GetRasterBand(1).ReadAsArray()
    np.testing.assert_array_equal(clip, gdal.Open(ref.path).ReadAsArray())
    assert arcpy.Describe(out).spatialReference.factoryCode == 32755
