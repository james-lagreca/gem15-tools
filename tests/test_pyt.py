# SPDX-License-Identifier: GPL-2.0-or-later
"""GEM15Tools.pyt helpers, loaded with a stub arcpy (no ArcGIS needed)."""
import importlib.machinery
import importlib.util
import os
import sys
import types

import pytest

from conftest import ROOT
from test_stac import FakeFetch, make_docs

PYT = ROOT / "arcgis" / "GEM15Tools.pyt"


def load_pyt(monkeypatch, fake=None):
    """Import the .pyt with `fake` standing in for arcpy (restored afterwards)."""
    monkeypatch.setitem(sys.modules, "arcpy", fake or types.ModuleType("arcpy"))
    # Use the toolbox's own vendored gem15core, as ArcGIS Pro would.
    monkeypatch.setattr(sys, "path", [str(PYT.parent)] + [
        p for p in sys.path if os.path.abspath(p) != str(ROOT / "core")])
    for name in [m for m in sys.modules if m == "gem15core" or m.startswith("gem15core.")]:
        monkeypatch.delitem(sys.modules, name)
    loader = importlib.machinery.SourceFileLoader("gem15pyt", str(PYT))
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("gem15pyt", loader))
    loader.exec_module(mod)
    return mod


def test_uses_vendored_core(monkeypatch):
    t = load_pyt(monkeypatch)
    assert os.path.dirname(t.stac.__file__) == str(PYT.parent / "gem15core")


def _exec_pyt():
    """Run the .pyt again as Pro does on a refresh, without clearing modules."""
    loader = importlib.machinery.SourceFileLoader("gem15pyt_again", str(PYT))
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("gem15pyt_again", loader))
    loader.exec_module(mod)
    return mod


def test_core_reimported_only_when_changed(monkeypatch):
    """An unchanged core is kept (re-importing it would give Gem15Error a new
    identity and break `except` clauses); a changed one is re-imported."""
    t1 = load_pyt(monkeypatch)
    core = sys.modules["gem15core"]
    assert core._toolbox_fingerprint
    t2 = _exec_pyt()
    assert sys.modules["gem15core"] is core and t2.Gem15Error is t1.Gem15Error
    core._toolbox_fingerprint = "an older version"            # as after an update
    t3 = _exec_pyt()
    assert sys.modules["gem15core"] is not core and t3.Gem15Error is not t1.Gem15Error


def test_find_cells_and_paths(monkeypatch):
    t = load_pyt(monkeypatch)
    tiles = t.find_cells((172.6, -41.95, 173.2, -41.65), "dtm", fetch=FakeFetch(make_docs()))
    assert [x.item_id for x in tiles] == ["gem-15_w172sm42", "gem-15_w173sm42"]
    acs = r"C:\proj\gem15.acs"
    existing = {os.path.join(acs, "m50_p170", "w172sm42", "dtm", "zall", "mosaic.tif")}
    paths, added = t.cloud_paths(acs, [x.asset("dtm").href for x in tiles],
                                 lambda p: p in existing)
    assert not added and paths[1].endswith(os.path.join("w173sm42", "dtm", "zall", "mosaic.tif"))


def test_zone_checks(monkeypatch):
    t = load_pyt(monkeypatch)
    sn = types.SimpleNamespace
    assert t.sr_utm_zone(sn(name="WGS_1984_UTM_Zone_55S")) == 55
    assert t.sr_utm_zone(sn(name="GDA_1994_MGA_Zone_54")) == 54       # MGA = UTM zones
    assert t.sr_utm_zone(sn(name="GDA2020_GA_LCC")) is None
    assert t.zone_mismatch(55, [32752, 32753]) == [52, 53]     # central Australia
    assert t.zone_mismatch(55, [32754, 32755, 32756]) == []    # neighbours tolerated
    assert t.zone_mismatch(60, [32701]) == []                  # wraps at 180
    assert t.zone_mismatch(None, [32752]) == []                # LCC mosaic: never blocked
    assert t.zone_mismatch(55, [7845]) == [7845]               # not UTM: refuse


def test_auto_mosaic_names(monkeypatch):
    """A blank mosaic name becomes GEM15_<product>_<CRS>, a valid geodatabase name."""
    t = load_pyt(monkeypatch)
    sn = types.SimpleNamespace
    assert t.auto_mosaic_name("dtm", sn(name="WGS_1984_UTM_Zone_52S", factoryCode=32752)) \
        == "GEM15_dtm_UTM52S"
    assert t.auto_mosaic_name("dsm", sn(name="WGS_1984_UTM_Zone_11N", factoryCode=32611)) \
        == "GEM15_dsm_UTM11N"
    assert t.crs_tag("GDA2020_GA_LCC", 7845) == "GA_LCC"
    assert t.crs_tag("NZGD_2000_New_Zealand_Transverse_Mercator", 2193) == "NZTM"
    # gem15core's custom projections have no code; the name keeps the centre.
    assert t.crs_tag("GEM-15_custom_Transverse_Mercator_133.5E") == "TM_133_5E"
    assert t.crs_tag("GEM-15_custom_Lambert_Conformal_Conic_105.5W") == "LCC_105_5W"
    assert t.crs_tag("", 54009) == "EPSG54009" and t.crs_tag(None) == "custom"
    for name in ("GEM15_dtm_UTM52S", t.auto_mosaic_name("dtm", sn(name="A b/c (d)"))):
        assert name[0].isalpha() and all(c.isalnum() or c == "_" for c in name)


def test_tool_definitions_import(monkeypatch):
    t = load_pyt(monkeypatch)
    tb = t.Toolbox()
    assert tb.alias == "gem15"
    assert tb.tools == [t.GEM15ForCurrentView, t.SwathProfile, t.ConnectToGEM15,
                        t.AddGEM15ForExtent, t.TerrainStack, t.ClipForAnalysis]
    assert [c().label for c in tb.tools] == [
        "GEM-15 for Current View", "Swath Profile", "Connect to GEM-15",
        "Add GEM-15 for Extent", "Terrain Stack", "Clip for Analysis"]
    # The one-click tool and the profile at the top; the rest in a toolset.
    assert [getattr(c(), "category", None) for c in tb.tools] == \
        [None, None] + [t.STEP_BY_STEP] * 4


def test_derivative_choices(monkeypatch):
    t = load_pyt(monkeypatch)
    labels = t.DERIV_LABELS
    # Any order in, drawing order out (hillshade at the bottom); Pro may quote values.
    assert t.deriv_keys([labels[2], "'" + labels[0] + "'"]) == ["hillshade", "lrm"]
    assert t.deriv_keys(["slope"]) == ["slope"] and t.deriv_keys(None) == []
    assert t.layer_label("GEM15_dtm", "lrm", 250).startswith("GEM15_dtm LRM (250 m, +/-2.5 m")
    assert "multidirectional, altitude 30 deg" in t.layer_label("x", "hillshade")
    assert "sun 270 deg" in t.layer_label("x", "hillshade", azimuth=270, z=2)
    assert t.file_suffix("lrm", 300.0) == "lrm300m" and t.file_suffix("slope") == "slope"
    assert t.file_suffix("hillshade") == "hillshade"
    assert t.file_suffix("hillshade", azimuth=270, z=2) == "hillshade_az270_z2"
    assert t.product_key("DTM (bare earth)") == "dtm" and t.product_key("dsm") == "dsm"
    assert t.illumination_key("Across a fault trend") == "across_trend"
    assert t.lrm_radius_value(next(iter(t.LRM_PRESET_LABELS))) == 150.0
    assert t.lrm_radius_value("450") == 450.0 and t.lrm_radius_value(None) == 300.0
    assert t.safe_name("GEM15 dtm (x)") == "GEM15_dtm__x"


def test_circle_kernel_is_a_normalised_disc(monkeypatch):
    t = load_pyt(monkeypatch)
    k = t.circle_kernel(300, 15)
    assert len(k) == len(k[0]) == 41                       # radius 20 px
    assert sum(map(sum, k)) == pytest.approx(1.0)
    assert k[20][20] > 0 and k[0][20] > 0 and k[0][0] == 0  # centre, edge, corner
    nonzero = sum(v > 0 for row in k for v in row)
    from gem15core import terrain
    assert nonzero == len(terrain.circle_offsets(300, 15))


def test_vrt_stem(monkeypatch):
    t = load_pyt(monkeypatch)
    assert t.vrt_stem((144.1, -37.52, 144.14, -37.49), "dtm") == \
        "gem15_dtm_144p100_m37p520_144p140_m37p490"


def test_layer_styles(monkeypatch):
    t = load_pyt(monkeypatch)
    for key in ("hillshade", "slope", "lrm", "canopy"):
        d = t.style_layer_json({"colorizer": {"type": "CIMRasterStretchColorizer",
                                              "colorScheme": "Black to White"}}, key)
        col = d["colorizer"]
        assert col["statsType"] == "GlobalStats"                   # fixed, not DRA (A13)
        assert (col["customStretchMin"], col["customStretchMax"]) == t.DISPLAY_RANGES[key]
    assert t.DISPLAY_RANGES["lrm"] == (-2.5, 2.5)                   # as the QGIS plugin
    lrm = t.style_layer_json({}, "lrm")
    assert lrm["minScale"] == t.LIVE_LRM_MIN_SCALE == 50000 and lrm["transparency"] == 40
    ramp = lrm["colorizer"]["colorRamp"]
    assert ramp["type"] == "CIMMultipartColorRamp" and len(ramp["colorRamps"]) == 2
    assert t.style_layer_json({}, "slope")["visibility"] is False
    assert "visibility" not in t.style_layer_json({}, "hillshade")


def test_vendored_copies_are_current(monkeypatch):
    """Both plugin copies must match core/gem15core byte for byte."""
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    import vendor_core
    assert vendor_core.sync(check=True) == [], "run: python tools/vendor_core.py"
