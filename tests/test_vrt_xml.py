"""VRT writer arithmetic, checked on the XML alone (pure Python, no GDAL)."""
import xml.etree.ElementTree as ET

import pytest

from gem15core import Gem15Error, stac, vrt


def tile(cell, x0, y0, epsg=32759, rows=8192, cols=8192, a=15.0):
    asset = stac.Asset(href=f"https://example/{cell}.tif", epsg=epsg, shape=(rows, cols),
                       transform=(a, 0.0, x0, 0.0, -15.0, y0), bands=("dtm",))
    return stac.Tile(item_id="gem-15_" + cell, cell_id=cell, group_id="g", bbox=(0, 0, 1, 1),
                     epsg=epsg, item_url="https://example/item.json", assets={"dtm": asset})


def rects(path):
    root = ET.parse(path).getroot()
    out = []
    for src in root.iter("ComplexSource"):
        s, d = src.find("SrcRect").attrib, src.find("DstRect").attrib
        out.append((src.find("SourceFilename").text,
                    tuple(int(s[k]) for k in ("xOff", "yOff", "xSize", "ySize")),
                    tuple(int(d[k]) for k in ("xOff", "yOff", "xSize", "ySize"))))
    return root, out


def test_union_of_two_overlapping_tiles(tmp_path):
    a = tile("w172sm42", 414720.0, 5360640.0, rows=100, cols=100)
    b = tile("w173sm42", 414720.0 + 60 * 15, 5360640.0 - 30 * 15, rows=100, cols=100)
    info = vrt.write_vrt([a, b], "dtm", tmp_path / "u.vrt")
    assert (info.cols, info.rows, info.sources) == (160, 130, 2)
    root, r = rects(info.path)
    assert r[0] == ("/vsicurl/https://example/w172sm42.tif", (0, 0, 100, 100), (0, 0, 100, 100))
    assert r[1][1:] == ((0, 0, 100, 100), (60, 30, 100, 100))
    gt = [float(v) for v in root.find("GeoTransform").text.split(",")]
    assert gt == [414720.0, 15.0, 0.0, 5360640.0, 0.0, -15.0]
    assert root.find("VRTRasterBand/NoDataValue").text == "nan"
    assert all(s.find("NODATA").text == "nan" for s in root.iter("ComplexSource"))
    md = {m.get("key"): m.text for m in root.iter("MDI")}
    assert md["ATTRIBUTION"] == "This dataset is produced by Google"
    assert 'AUTHORITY["EPSG","32759"]' in root.find("SRS").text


def test_bounds_clip_snap_and_skip(tmp_path):
    a = tile("w172sm42", 0.0, 1500.0, rows=100, cols=100)          # x 0..1500, y 0..1500
    far = tile("w179sm42", 1.5e6, 1500.0, rows=100, cols=100)        # outside the bounds
    info = vrt.write_vrt([a, far], "dtm", tmp_path / "c.vrt",
                         bounds=(301.0, 449.0, 899.0, 1201.0))       # snapped outwards
    assert info.bounds == (300.0, 435.0, 900.0, 1215.0)
    assert (info.cols, info.rows, info.sources) == (40, 52, 1)
    _, r = rects(info.path)
    assert r[0][1:] == ((20, 19, 40, 52), (0, 0, 40, 52))
    # Bounds larger than the tile: the source lands inside, the rest is NaN.
    info = vrt.write_vrt([a], "dtm", tmp_path / "big.vrt", bounds=(-150, -150, 1650, 1650))
    _, r = rects(info.path)
    assert (info.cols, info.rows) == (120, 120) and r[0][1:] == ((0, 0, 100, 100), (10, 10, 100, 100))


def test_refuses_mixed_zones_off_grid_and_bad_band(tmp_path):
    with pytest.raises(Gem15Error, match="several UTM zones"):
        vrt.write_vrt([tile("a", 0, 0), tile("b", 0, 0, epsg=32760)], "dtm", tmp_path / "x.vrt")
    with pytest.raises(Gem15Error, match="grid"):
        vrt.write_vrt([tile("a", 7.0, 0)], "dtm", tmp_path / "x.vrt")
    with pytest.raises(Gem15Error, match="grid"):
        vrt.write_vrt([tile("a", 0, 0, a=10.0)], "dtm", tmp_path / "x.vrt")
    with pytest.raises(Gem15Error):
        vrt.write_vrt([tile("a", 0, 0)], "dtm", tmp_path / "x.vrt", band_name="dsm_std")
    with pytest.raises(Gem15Error, match="overlaps"):
        vrt.write_vrt([tile("a", 0, 1500, rows=100, cols=100)], "dtm", tmp_path / "x.vrt",
                      bounds=(1e5, 1e5, 1.1e5, 1.1e5))
