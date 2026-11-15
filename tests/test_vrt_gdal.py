"""The pure-Python VRT, opened by GDAL on synthetic tiles: it must match
gdal.BuildVRT pixel for pixel, fill overlaps correctly and expose overviews."""
import pytest

np = pytest.importorskip("numpy")
gdal = pytest.importorskip("osgeo.gdal")

from conftest import make_tile, surface  # noqa: E402
from gem15core import gdalops, paths, stac, vrt  # noqa: E402

AOI = (173.85, -41.78, 173.95, -41.72)          # inside tile A (zone 59) only


def tiles_for(synthetic, bbox, product="dtm"):
    return stac.find_tiles(bbox, product, collection_url=synthetic["collection_url"],
                           fetch=stac.Fetcher(cache_dir=None))


def read(path):
    ds = gdal.Open(str(path))
    return ds, ds.GetRasterBand(1).ReadAsArray()


def test_matches_gdal_buildvrt(synthetic, tmp_path):
    tiles = tiles_for(synthetic, AOI)
    assert [t.cell_id for t in tiles] == ["w173sm42"]
    bounds = gdalops.aoi_bounds_in("EPSG:32759", AOI)
    ours = vrt.write_vrt(tiles, "dtm", tmp_path / "ours.vrt", bounds=bounds).path
    ref = str(tmp_path / "ref.vrt")
    gdal.BuildVRT(ref, [paths.gdal_path(t.asset("dtm").href) for t in tiles],
                  options=gdal.BuildVRTOptions(outputBounds=bounds, xRes=15, yRes=15,
                                               resolution="user", srcNodata="nan",
                                               VRTNodata="nan"))
    (d1, a1), (d2, a2) = read(ours), read(ref)
    assert d1.GetGeoTransform() == d2.GetGeoTransform()
    np.testing.assert_array_equal(a1, a2)
    assert np.isfinite(a1).all()
    assert d1.GetRasterBand(1).GetDescription() == "GEM-15 dtm (m, EGM96)"
    assert d1.GetMetadataItem("ATTRIBUTION") == "This dataset is produced by Google"
    # Reduced-resolution reads (zoomed out) must agree too; they go to the
    # sources' overviews (V5; the byte count is checked in test_live).
    small = dict(buf_xsize=a1.shape[1] // 4, buf_ysize=a1.shape[0] // 4)
    np.testing.assert_array_equal(d1.GetRasterBand(1).ReadAsArray(**small),
                                  d2.GetRasterBand(1).ReadAsArray(**small))


def test_dsm_band_selection(synthetic, tmp_path):
    tiles = tiles_for(synthetic, AOI, "dsm")
    p = vrt.write_vrt(tiles, "dsm", tmp_path / "std.vrt", band_name="dsm_std").path
    _, arr = read(p)
    src = gdal.Open(paths.gdal_path(tiles[0].asset("dsm").href))
    np.testing.assert_array_equal(arr, src.GetRasterBand(5).ReadAsArray())


def test_overlap_nan_does_not_cover_valid(tmp_path):
    """Two tiles in one zone: the later tile wins where valid; where it is NaN
    the earlier tile shows through (V2)."""
    box = (173.80, -41.80, 173.90, -41.70)
    shape_a, tr_a = make_tile(tmp_path / "a.tif", 32759, box, 1, overviews=False)
    shape_c, tr_c = make_tile(tmp_path / "c.tif", 32759, box, 1, nan_west_of=173.85,
                              offset=100.0, overviews=False)

    def t(name, shape, tr):
        a = stac.Asset((tmp_path / f"{name}.tif").as_uri(), 32759, tuple(shape),
                       tuple(tr), ("dtm",))
        return stac.Tile("gem-15_" + name, name, "g", box, 32759, "", {"dtm": a})

    p = vrt.write_vrt([t("a", shape_a, tr_a), t("c", shape_c, tr_c)], "dtm",
                      tmp_path / "o.vrt").path
    ds, arr = read(p)
    gt = ds.GetGeoTransform()
    from osgeo import osr
    ct = osr.CoordinateTransformation(gdalops.srs("EPSG:4326"), gdalops.srs("EPSG:32759"))

    def value(lon, lat):
        x, y = ct.TransformPoint(lon, lat)[:2]
        return float(arr[int((y - gt[3]) / gt[5]), int((x - gt[0]) / gt[1])])

    # Within half a pixel the synthetic surface changes < 16 m, well short of
    # the 100 m offset between the two tiles.
    west, east = value(173.82, -41.75), value(173.88, -41.75)
    assert abs(west - surface(173.82, -41.75)) < 30            # tile a (c is NaN here)
    assert abs(east - 100 - surface(173.88, -41.75)) < 30      # tile c wins
