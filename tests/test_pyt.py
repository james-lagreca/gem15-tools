"""GEM15Tools.pyt helpers, loaded with a stub arcpy (no ArcGIS needed)."""
import importlib.machinery
import importlib.util
import os
import sys
import types

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
    assert t.sr_utm_zone(sn(name="GDA2020_GA_LCC")) is None
    assert t.zone_mismatch(55, [32752, 32753]) == [52, 53]     # central Australia
    assert t.zone_mismatch(55, [32754, 32755, 32756]) == []    # neighbours tolerated
    assert t.zone_mismatch(60, [32701]) == []                  # wraps at 180
    assert t.zone_mismatch(None, [32752]) == []                # LCC mosaic: never blocked
    assert t.zone_mismatch(55, [7845]) == [7845]               # not UTM: refuse


def test_tool_definitions_import(monkeypatch):
    t = load_pyt(monkeypatch)
    tb = t.Toolbox()
    assert tb.alias == "gem15" and t.AddGEM15ForExtent in tb.tools


def test_vendored_copies_are_current(monkeypatch):
    """Both plugin copies must match core/gem15core byte for byte."""
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    import vendor_core
    assert vendor_core.sync(check=True) == [], "run: python tools/vendor_core.py"
