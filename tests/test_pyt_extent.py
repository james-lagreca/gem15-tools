"""extent_to_lonlat in the .pyt, with a minimal fake arcpy that records what it is given."""
import types

import pytest

from test_pyt import load_pyt


def fake_arcpy(calls):
    class SR:
        def __init__(self, code=0):
            self.factoryCode = code
            calls.setdefault("sr", []).append(code)

    class Pt:
        def __init__(self, x, y):
            self.X, self.Y = x, y

    class Ext:
        def __init__(self, a, b, c, d):
            self.XMin, self.YMin, self.XMax, self.YMax = a, b, c, d

    class Poly:
        def __init__(self, arr, sr):
            self.pts = list(arr)
            calls["ring"] = len(self.pts)

        def projectAs(self, sr):  # fake "projection": divide by 1e5 so corners can be checked
            xs = [p.X / 1e5 for p in self.pts]
            ys = [p.Y / 1e5 - 100 for p in self.pts]
            return types.SimpleNamespace(extent=Ext(min(xs), min(ys), max(xs), max(ys)))

    fake = types.ModuleType("arcpy")
    fake.SpatialReference, fake.Point, fake.Array, fake.Polygon = SR, Pt, list, Poly
    return fake


def test_projected_extent(monkeypatch):
    calls = {}
    t = load_pyt(monkeypatch, fake_arcpy(calls))
    gp = types.SimpleNamespace(XMin=14478435.0, YMin=6134965.0, XMax=14941481.0, YMax=6455654.0,
                               spatialReference=types.SimpleNamespace(factoryCode=32755))
    bbox = t.extent_to_lonlat(gp)
    assert calls["sr"] == [32755, 4326]           # rebuilt from the code, then WGS 84
    assert calls["ring"] == 41                    # densified closed ring
    assert bbox[0] == pytest.approx(144.78435 - 0.005)
    assert bbox[3] == pytest.approx(-35.44346 + 0.005)


def test_lonlat_extent_passes_through(monkeypatch):
    t = load_pyt(monkeypatch, fake_arcpy({}))
    ll = types.SimpleNamespace(XMin=144.7, YMin=-38.6, XMax=149.4, YMax=-35.4,
                               spatialReference=types.SimpleNamespace(factoryCode=4326))
    assert t.extent_to_lonlat(ll)[0] == pytest.approx(144.7 - 0.005)
