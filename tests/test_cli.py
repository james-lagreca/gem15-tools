"""gdalops.build_vrt and the gem15_vrt.py command line, on synthetic tiles
straddling UTM 59S/60S (needs GDAL and numpy)."""
import importlib.util
import os

import pytest

np = pytest.importorskip("numpy")
gdal = pytest.importorskip("osgeo.gdal")
from osgeo import osr  # noqa: E402

from conftest import ROOT, surface  # noqa: E402
from gem15core import Gem15Error, gdalops, stac  # noqa: E402

CROSS = (173.85, -41.78, 174.15, -41.72)     # spans zones 59 and 60
SINGLE = (173.85, -41.78, 173.95, -41.72)    # zone 59 only
PTS = [(173.86, -41.74), (173.99, -41.75), (174.01, -41.73), (174.12, -41.76)]


def build(synthetic, bbox, out, **kw):
    return gdalops.build_vrt(bbox, str(out), collection_url=synthetic["collection_url"],
                             fetch=stac.Fetcher(cache_dir=None), log=None, **kw)


def sample(path, lon, lat, band=1, with_centre=False):
    ds = gdal.Open(str(path))
    sr = osr.SpatialReference(wkt=ds.GetProjection())
    sr.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    x, y = osr.CoordinateTransformation(gdalops.srs("EPSG:4326"), sr).TransformPoint(lon, lat)[:2]
    gt = ds.GetGeoTransform()
    col, row = int((x - gt[0]) / gt[1]), int((y - gt[3]) / gt[5])
    v = float(ds.GetRasterBand(band).ReadAsArray(col, row, 1, 1)[0, 0])
    if not with_centre:
        return v
    xc, yc = gt[0] + (col + 0.5) * gt[1], gt[3] + (row + 0.5) * gt[5]
    lo, la = osr.CoordinateTransformation(sr, gdalops.srs("EPSG:4326")).TransformPoint(xc, yc)[:2]
    return v, lo, la


def residual(path, lon, lat, band=1):
    v, lo, la = sample(path, lon, lat, band, with_centre=True)
    return v - surface(lo, la)


def epsg_of(path):
    sr = osr.SpatialReference(wkt=gdal.Open(str(path)).GetProjection())
    return sr.GetAuthorityCode(None)


def test_cross_zone_auto_crs_is_nztm(synthetic, tmp_path):
    vrt = build(synthetic, CROSS, tmp_path / "auto.vrt")
    assert epsg_of(vrt) == "2193"
    res = [residual(vrt, lo, la) for lo, la in PTS]
    # Half a pixel of the synthetic gradient is ~9 m; a correct bilinear warp
    # of a near-linear surface should be far closer than that.
    assert all(np.isfinite(res)) and max(abs(r) for r in res) < 1.5, res
    assert np.isfinite(sample(vrt, 173.99, -41.75))       # NaN in tile B does not cover A


def test_cross_zone_explicit_crs(synthetic, tmp_path):
    vrt = build(synthetic, CROSS, tmp_path / "mga.vrt", dst_crs="EPSG:2193")
    assert epsg_of(vrt) == "2193"
    assert max(abs(residual(vrt, lo, la)) for lo, la in PTS) < 1.5


def test_single_zone_native_exact(synthetic, tmp_path):
    vrt = build(synthetic, SINGLE, tmp_path / "native.vrt")
    src = synthetic["tiles_dir"] / "w173sm42_dtm.tif"
    for lo, la in [(173.86, -41.74), (173.93, -41.77)]:
        assert sample(vrt, lo, la) == sample(src, lo, la)
    gt = gdal.Open(vrt).GetGeoTransform()
    assert gt[1] == 15 and gt[0] % 15 == 0 and gt[3] % 15 == 0
    # Asking for the tiles' own zone explicitly also keeps the native grid.
    same = build(synthetic, SINGLE, tmp_path / "same.vrt", dst_crs="EPSG:32759")
    assert sample(same, 173.86, -41.74) == sample(src, 173.86, -41.74)


def test_dsm_band(synthetic, tmp_path):
    vrt = build(synthetic, SINGLE, tmp_path / "std.vrt", product="dsm", band_name="dsm_std")
    assert abs(residual(vrt, 173.9, -41.75) - 4000) < 1e-2
    assert gdal.Open(vrt).RasterCount == 1


def test_clip_and_per_zone(synthetic, tmp_path):
    vrt = build(synthetic, CROSS, tmp_path / "auto.vrt")
    tif = gdalops.clip_to_geotiff(vrt, str(tmp_path / "clip.tif"), log=None)
    # The same pixel read on screen and in a clip of the same VRT (G3). With
    # GDAL's default approximation it differed by ~0.9 m on this surface that
    # climbs 18 m per pixel; with the exact transformer < 0.1 m; with the
    # kernel scale also fixed (XSCALE/YSCALE=1) not at all.
    assert sample(tif, 174.01, -41.73) == sample(vrt, 174.01, -41.73)
    zp = build(synthetic, CROSS, tmp_path / "pz.vrt", per_zone=True)
    assert [epsg_of(p) for p in zp] == ["32759", "32760"]
    src_b = synthetic["tiles_dir"] / "w174sm42_dtm.tif"
    assert sample(zp[1], 174.1, -41.75) == sample(src_b, 174.1, -41.75)


def test_no_coverage(synthetic, tmp_path):
    with pytest.raises(Gem15Error, match="No GEM-15 tiles"):
        build(synthetic, (172.1, -41.9, 172.2, -41.8), tmp_path / "none.vrt")


def _cli():
    spec = importlib.util.spec_from_file_location("gem15_vrt", ROOT / "cli" / "gem15_vrt.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_cli(synthetic, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GEM15_CACHE_DIR", str(tmp_path / "cache"))
    cli = _cli()
    out = tmp_path / "cli.vrt"
    rc = cli.main(["--bbox", *map(str, CROSS), "--out", str(out),
                   "--clip", str(tmp_path / "cli.tif"), "--stac", synthetic["collection_url"]])
    assert rc == 0 and out.exists() and (tmp_path / "cli.tif").exists()
    assert "New Zealand" in capsys.readouterr().out
    rc = cli.main(["--bbox", "172.1", "-41.9", "172.2", "-41.8", "--out", str(tmp_path / "x.vrt"),
                   "--stac", synthetic["collection_url"]])
    assert rc == 2 and "No GEM-15 tiles" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli.main(["--bbox", *map(str, CROSS), "--out", "x.vrt", "--per-zone", "--clip", "y.tif"])
    assert not os.path.exists("x.vrt")
