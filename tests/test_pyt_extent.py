"""extent_to_lonlat in the .pyt, with a minimal fake arcpy that records what it is given.

The fake "projection" is linear: lon = x / 1e5, lat = y / 1e5 - 100, with
longitudes wrapped into -180..180 as a real projection would return them.
"""
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

    class Multipoint:
        def __init__(self, arr, sr):
            self.pts = list(arr)
            calls["ring"] = len(self.pts)

        def projectAs(self, sr):
            out = [Pt(((p.X / 1e5 + 180) % 360) - 180, p.Y / 1e5 - 100) for p in self.pts]
            return types.SimpleNamespace(getPart=lambda: out)

    fake = types.ModuleType("arcpy")
    fake.SpatialReference, fake.Point, fake.Array, fake.Multipoint = SR, Pt, list, Multipoint
    return fake


def gp_extent(x0, y0, x1, y1, code):
    return types.SimpleNamespace(XMin=x0, YMin=y0, XMax=x1, YMax=y1,
                                 spatialReference=types.SimpleNamespace(factoryCode=code))


def test_projected_extent(monkeypatch):
    calls = {}
    t = load_pyt(monkeypatch, fake_arcpy(calls))
    bbox = t.extent_to_lonlat(gp_extent(14478435.0, 6134965.0, 14941481.0, 6455654.0, 32755))
    assert calls["sr"] == [32755, 4326]           # rebuilt from the code, then WGS 84
    assert calls["ring"] == 41                    # densified closed ring of points
    assert bbox[0] == pytest.approx(144.78435 - 0.005)
    assert bbox[3] == pytest.approx(-35.44346 + 0.005)


def test_projected_extent_across_antimeridian(monkeypatch):
    t = load_pyt(monkeypatch, fake_arcpy({}))
    # x 17,800,000 .. 18,300,000 -> lon 178 .. 183 = 178 E .. 177 W
    w, s, e, n = t.extent_to_lonlat(gp_extent(17800000.0, 8200000.0, 18300000.0, 8400000.0, 3460))
    assert w > e                                   # crosses 180, not the whole globe
    assert w == pytest.approx(178 - 0.005) and e == pytest.approx(-177 + 0.005)
    assert (s, n) == (pytest.approx(-18 - 0.005), pytest.approx(-16 + 0.005))


def test_lonlat_extent_passes_through(monkeypatch):
    t = load_pyt(monkeypatch, fake_arcpy({}))
    assert t.extent_to_lonlat(gp_extent(144.7, -38.6, 149.4, -35.4, 4326))[0] == \
        pytest.approx(144.7 - 0.005)


def test_lonlat_extent_past_180(monkeypatch):
    """A WGS 84 map panned across 180 reports longitudes above 180."""
    t = load_pyt(monkeypatch, fake_arcpy({}))
    w, _, e, _ = t.extent_to_lonlat(gp_extent(179.0, -17.5, 181.0, -16.0, 4326))
    assert w == pytest.approx(179 - 0.005) and e == pytest.approx(-179 + 0.005)


def test_extent_without_crs_in_metres_is_refused(monkeypatch):
    t = load_pyt(monkeypatch, fake_arcpy({}))
    ext = types.SimpleNamespace(XMin=300000.0, YMin=5800000.0, XMax=310000.0, YMax=5810000.0,
                                spatialReference=None)
    with pytest.raises(ValueError, match="not in degrees"):
        t.extent_to_lonlat(ext)
