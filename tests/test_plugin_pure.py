# SPDX-License-Identifier: GPL-2.0-or-later
"""QGIS plugin modules that need no QGIS: LRM kernel and VRT, download
estimate, view planning, metadata and Qt6-safety checks. Run in any Python
(ArcGIS Pro's or QGIS's). The plugin is imported as the package `gem15`
with its vendored core, as QGIS imports it."""
import configparser
import math
import pathlib
import re
import sys
import xml.etree.ElementTree as ET

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "qgis" / "gem15"
sys.path.insert(0, str(PLUGIN.parent))

from gem15 import estimate, live, lrm_vrt  # noqa: E402
from gem15.gem15core import Gem15Error, stac, terrain, vrt  # noqa: E402


# ---------------------------------------------------------------- LRM kernel
def test_lrm_kernel_is_zero_sum_circle():
    size, coefs = lrm_vrt.kernel(300, 15)
    r = terrain.radius_cells(300, 15)
    n = len(terrain.circle_offsets(300, 15))
    assert size == 2 * r + 1 == 41 and len(coefs) == size * size
    assert abs(sum(coefs)) < 1e-12
    assert coefs[r * size + r] == pytest.approx(1 - 1 / n)
    assert sum(1 for c in coefs if c) == n
    grid = [coefs[i * size:(i + 1) * size] for i in range(size)]
    assert grid == grid[::-1] and all(row == row[::-1] for row in grid)   # symmetric
    assert grid[0][0] == 0.0 and grid[0][r] != 0.0                       # circle, not square


def _fake_tile(cell, epsg=32755, left=300000.0, top=6000000.0, rows=1024, cols=1024,
               href="file:///nonexistent/t.tif"):
    a = stac.Asset(href=href, epsg=epsg, shape=(rows, cols),
                   transform=(15.0, 0.0, left, 0.0, -15.0, top), bands=("dtm",))
    from gem15.gem15core import cells
    return stac.Tile(item_id="gem-15_" + cell, cell_id=cell, group_id="",
                     bbox=cells.parse_cell(cell), epsg=epsg, item_url="", assets={"dtm": a})


def test_lrm_vrt_copies_grid_and_references_base(tmp_path):
    base = tmp_path / "base.vrt"
    info = vrt.write_vrt([_fake_tile("w144sm38")], "dtm", base)
    out = lrm_vrt.write_lrm_vrt(str(base), str(tmp_path / "lrm.vrt"), 150)
    root, b = ET.parse(out).getroot(), ET.parse(base).getroot()
    assert (root.get("rasterXSize"), root.get("rasterYSize")) == (str(info.cols), str(info.rows))
    assert root.find("GeoTransform").text == b.find("GeoTransform").text
    assert root.find("SRS").text == b.find("SRS").text
    src = root.find("VRTRasterBand/KernelFilteredSource")
    fn = src.find("SourceFilename")
    assert fn.text == "base.vrt" and fn.get("relativeToVRT") == "1"
    k = src.find("Kernel")
    assert k.get("normalized") == "0" and k.find("Size").text == "21"
    assert len(k.find("Coefs").text.split()) == 21 * 21
    md = {m.get("key"): m.text for m in root.findall("Metadata/MDI")}
    assert md["LRM_RADIUS_M"] == "150" and "Google" in md["ATTRIBUTION"]


# ----------------------------------------------------------- download size
def test_blocks_touched():
    a = _fake_tile("w144sm38", left=0.0, top=0.0, rows=8192, cols=8192).asset("dtm")
    blk = 512 * 15.0
    assert estimate.blocks_touched(a, (0, -blk, blk, 0)) == 1                 # one block exactly
    assert estimate.blocks_touched(a, (blk - 1, -blk - 1, blk + 1, -blk + 1)) == 4
    assert estimate.blocks_touched(a, (-100, -100, -50, -50)) == 0           # outside
    assert estimate.blocks_touched(a, (-1e6, -1e6, 1e6, 1e6)) == 256         # whole tile
    tiles = [_fake_tile("w144sm38", left=0.0, top=0.0)]
    assert estimate.download_mb(tiles, "dtm", lambda epsg: (0, -blk, blk, 0)) == pytest.approx(0.9)


# ------------------------------------------------------------- view plans
def test_tiles_bbox_across_antimeridian():
    tiles = [_fake_tile("w179sm17", epsg=32760), _fake_tile("wm180sm17", epsg=32601)]
    assert live.tiles_bbox(tiles) == (179.0, -17.0, -179.0, -16.0)


def test_plan_single_zone_and_reuse(synthetic, tmp_path):
    url = synthetic["collection_url"]
    bbox = (173.85, -41.78, 173.95, -41.72)                     # one cell, UTM 59S
    plan = live.plan_layers(bbox, "dtm", str(tmp_path), collection_url=url)
    assert [t.cell_id for t in plan.tiles] == ["w173sm42"]
    (spec,) = plan.layers
    assert spec.style == "hillshade" and spec.path.endswith("_epsg32759.vrt")
    assert "UTM 59S" in spec.name and plan.group is None
    mtime = pathlib.Path(spec.path).stat().st_mtime_ns
    again = live.plan_layers(bbox, "dtm", str(tmp_path), collection_url=url)
    assert again.layers[0].path == spec.path
    assert pathlib.Path(spec.path).stat().st_mtime_ns == mtime     # reused, not rewritten (L3)

    lrm = live.plan_layers(bbox, "lrm", str(tmp_path), collection_url=url)
    (lspec,) = lrm.layers
    assert lspec.style == "lrm" and "_lrm_r300" in lspec.path
    assert ET.parse(lspec.path).getroot().find("VRTRasterBand/KernelFilteredSource") is not None


def test_plan_lrm_is_per_zone(synthetic, tmp_path):
    plan = live.plan_layers((173.9, -41.78, 174.1, -41.72), "lrm", str(tmp_path),
                            collection_url=synthetic["collection_url"])
    assert len(plan.layers) == 2 and plan.group and "2 UTM zones" in plan.group
    assert {p.path.split("_epsg")[1][:5] for p in plan.layers} == {"32759", "32760"}


def test_plan_no_coverage(synthetic, tmp_path):
    with pytest.raises(Gem15Error, match="No GEM-15 tiles"):
        live.plan_layers((175.5, -41.8, 175.6, -41.7), "dtm", str(tmp_path),
                         collection_url=synthetic["collection_url"])


# ------------------------------------------------ clip-based LRM (numpy)
def test_circle_mean_matches_brute_force():
    np = pytest.importorskip("numpy")
    pytest.importorskip("osgeo")
    from gem15 import derivatives
    rng = np.random.default_rng(0)
    r = 4
    a = rng.normal(100, 5, (30, 25))
    a[10:13, 8:12] = np.nan
    block = np.vstack([np.full((r, 25), np.nan), a, np.full((r, 25), np.nan)])
    got = derivatives.circle_mean(block, r)
    offs = [(dx, dy) for dy in range(-r, r + 1) for dx in range(-r, r + 1)
            if math.hypot(dx, dy) <= r + 1e-9]
    pad = np.pad(a, r, constant_values=np.nan)
    stack = np.stack([pad[r + dy:r + dy + 30, r + dx:r + dx + 25] for dx, dy in offs])
    want = np.nanmean(stack, axis=0)
    np.testing.assert_allclose(got, want, rtol=0, atol=1e-9)
    assert len(offs) == sum(2 * w + 1 for w in derivatives.run_lengths(r))


# ------------------------------------------------------- plugin packaging
def test_metadata():
    cp = configparser.ConfigParser()
    cp.read(PLUGIN / "metadata.txt", encoding="utf-8")
    g = cp["general"]
    assert g["qgisMinimumVersion"] == "3.28" and g["qgisMaximumVersion"] == "4.99"
    assert g["license"] == "GPL-2.0-or-later"
    assert "This dataset is produced by Google" in g["about"]
    assert g["hasProcessingProvider"] == "yes"
    assert (PLUGIN / g["icon"]).is_file()
    assert "@" not in g.get("email", ""), "no personal email in the public metadata"
    assert re.fullmatch(r"\d+\.\d+\.\d+", g["version"])


def _plugin_sources():
    return [p for p in PLUGIN.rglob("*.py") if "gem15core" not in p.parts]


def test_qt_access_is_qt6_safe():
    """All Qt through qgis.PyQt, scoped enum names, no Qt5-only calls."""
    bad = {
        r"\bfrom PyQt[56]\b|\bimport PyQt[56]\b": "import Qt via qgis.PyQt",
        (r"\bQt\.(Align(?!mentFlag)|Checked|Unchecked|Horizontal|Vertical|Key_|LeftButton|"
         r"WindowModal|ItemIs|DisplayRole|UserRole)"): "unscoped Qt enum",
        r"\.exec_\(": "exec_() is gone in Qt6",
        r"\bQVariant\b": "QVariant is not in PyQt6",
        r"QtWidgets import [^\n]*\bQAction\b": "QAction lives in QtGui in Qt6 (use the try/except import)",
    }
    problems = []
    for path in _plugin_sources():
        text = path.read_text(encoding="utf-8")
        for pattern, why in bad.items():
            for m in re.finditer(pattern, text):
                line = text[:m.start()].count("\n") + 1
                if "except ImportError" in text.splitlines()[line - 2] if line > 1 else False:
                    continue                                  # the Qt5 fallback import
                problems.append(f"{path.name}:{line}: {why}")
    assert not problems, problems
