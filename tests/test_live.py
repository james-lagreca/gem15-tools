# SPDX-License-Identifier: GPL-2.0-or-later
"""Checks against the live, public GEM-15 bucket (anonymous, read-only).

Skipped unless GEM15_LIVE=1. Each run fetches a few small JSON documents and,
with GDAL, a few COG headers and blocks (a few MB at most).
"""
import pytest

from gem15core import cells, crs, stac

pytestmark = pytest.mark.live

VICTORIA = (144.0, -37.6, 144.3, -37.4)           # UTM 55S
NZ_CROSS = (173.8, -41.8, 174.2, -41.6)           # UTM 59S / 60S
ARIZONA = (-111.5, 30.2, -110.5, 30.8)            # UTM 12N
CENTRAL_AUS = (131.5, -25.5, 132.5, -24.5)        # UTM 52S / 53S


@pytest.fixture(scope="module")
def fetch(tmp_path_factory):
    return stac.Fetcher(cache_dir=str(tmp_path_factory.mktemp("cache")))


@pytest.mark.parametrize("bbox, epsgs", [
    (VICTORIA, {32755}), (NZ_CROSS, {32759, 32760}), (ARIZONA, {32612}),
    (CENTRAL_AUS, {32752, 32753})])
def test_tiles_found_with_expected_zones(bbox, epsgs, fetch):
    tiles = stac.find_tiles(bbox, "dtm", fetch=fetch)
    assert tiles and {t.epsg for t in tiles} == epsgs
    for t in tiles:
        a = t.asset("dtm")
        assert a.shape[0] >= 8192 and a.shape[1] >= 8192   # sizes vary (8192 x 16384 seen)
        assert a.transform[0] == 15.0 and a.transform[4] == -15.0
        assert a.transform[2] % 15 == 0 and a.transform[5] % 15 == 0
        assert cells.parse_cell(t.cell_id) == t.bbox                      # names match bboxes
        assert cells.cell_name(t.bbox[0] + 0.5, t.bbox[1] + 0.5) == t.cell_id
        assert t.asset("dsm").bands[-1] == "dsm_std"


def test_auto_crs_on_live_tiles(fetch):
    au = stac.find_tiles(CENTRAL_AUS, "dtm", fetch=fetch)
    assert crs.auto_crs(CENTRAL_AUS, [t.epsg for t in au]).epsg == 7845
    nz = stac.find_tiles(NZ_CROSS, "dtm", fetch=fetch)
    assert crs.auto_crs(NZ_CROSS, [t.epsg for t in nz]).epsg == 2193


def test_vrt_streams_same_pixels_as_cog(fetch, tmp_path):
    np = pytest.importorskip("numpy")
    gdal = pytest.importorskip("osgeo.gdal")
    from gem15core import gdalops, paths, vrt

    gdalops.configure_remote()
    bbox = (144.10, -37.52, 144.14, -37.49)
    tiles = stac.find_tiles(bbox, "dtm", fetch=fetch)
    info = vrt.write_vrt(tiles, "dtm", tmp_path / "vic.vrt",
                         bounds=gdalops.aoi_bounds_in(f"EPSG:{tiles[0].epsg}", bbox))
    ds = gdal.Open(info.path)
    win = ds.GetRasterBand(1).ReadAsArray(0, 0, 16, 16)
    assert np.isfinite(win).all() and (-100 < win).all() and (win < 2500).all()
    # The same pixels read straight from the (last-listed, winning) COG, whose
    # size and grid must match the catalogue.
    a = tiles[-1].asset("dtm")
    cog = gdal.Open(paths.gdal_path(a.href))
    assert (cog.RasterYSize, cog.RasterXSize) == a.shape
    assert cog.GetGeoTransform() == a.geotransform()
    assert cog.GetRasterBand(1).GetBlockSize() == [512, 512]
    gt, cgt = ds.GetGeoTransform(), cog.GetGeoTransform()
    col, row = int(round((gt[0] - cgt[0]) / 15)), int(round((cgt[3] - gt[3]) / 15))
    np.testing.assert_array_equal(win, cog.GetRasterBand(1).ReadAsArray(col, row, 16, 16))


def test_zoomed_out_reads_use_cog_overviews(fetch, tmp_path):
    """Reading a whole tile at low resolution through the VRT must not pull
    the full-resolution data (V5)."""
    import json
    gdal = pytest.importorskip("osgeo.gdal")
    from gem15core import gdalops, vrt

    gdalops.configure_remote()
    gdal.SetConfigOption("CPL_VSIL_NETWORK_STATS_ENABLED", "YES")
    tiles = stac.find_tiles((144.10, -37.52, 144.14, -37.49), "dtm", fetch=fetch)
    ds = gdal.Open(vrt.write_vrt(tiles[:1], "dtm", tmp_path / "whole.vrt").path)
    gdal.NetworkStatsReset()
    ds.GetRasterBand(1).ReadAsArray(buf_xsize=256, buf_ysize=256)
    stats = json.loads(gdal.NetworkStatsGetAsSerializedJSON())
    mb = stats.get("methods", {}).get("GET", {}).get("downloaded_bytes", 0) / 1e6
    assert mb < 5, f"downloaded {mb:.1f} MB for a 256 x 256 overview read"
