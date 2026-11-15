"""Lon/lat box helpers, including the antimeridian (pure Python)."""
import pytest

from gem15core import Gem15Error, extent

FIJI = (177.0, -19.0, -178.0, -16.0)   # crosses 180


def test_check():
    assert extent.check([144, -38, 145, -37]) == (144.0, -38.0, 145.0, -37.0)
    assert extent.check(FIJI) == FIJI
    for bad in [(1, 2, 3), (0, 10, 1, 5), (0, 0, 0, 1), (200, 0, 201, 1), ("a", 0, 1, 1)]:
        with pytest.raises(Gem15Error):
            extent.check(bad)


def test_split_width_centre():
    assert extent.split_antimeridian(FIJI) == [(177.0, -19.0, 180.0, -16.0),
                                              (-180.0, -19.0, -178.0, -16.0)]
    assert extent.split_antimeridian((1, 2, 3, 4)) == [(1, 2, 3, 4)]
    assert extent.width_deg(FIJI) == 5.0
    assert extent.centre(FIJI) == (179.5, -17.5)
    assert extent.centre((178, 0, -176, 1)) == (-179.0, 0.5)


def test_intersects():
    assert extent.intersects((172, -42, 173, -41), (172.6, -41.95, 173.2, -41.65))
    assert not extent.intersects((172, -42, 173, -41), (173, -42, 174, -41))   # edge only
    assert extent.intersects(FIJI, (179, -18, 180, -17))
    assert extent.intersects(FIJI, (-179, -18, -178.5, -17))
    assert not extent.intersects(FIJI, (0, -18, 1, -17))


def test_pad():
    assert extent.pad((144.7, -38.6, 149.4, -35.4), 0.005) == pytest.approx(
        (144.695, -38.605, 149.405, -35.395))
    assert extent.pad((179.999, 0, 179.9995, 1), 0.01) == pytest.approx(
        (179.989, -0.01, -179.9905, 1.01))                                    # wraps
    assert extent.pad((-180, -90, 180, 90), 1) == (-180.0, -90.0, 180.0, 90.0)


def test_from_lonlat_points():
    pts = [(144.7, -38.6), (149.4, -35.4), (147, -37)]
    assert extent.from_lonlat_points(pts) == (144.7, -38.6, 149.4, -35.4)
    pts = [(179.0, -18.0), (-179.0, -16.0), (179.5, -17.0)]
    assert extent.from_lonlat_points(pts) == (179.0, -18.0, -179.0, -16.0)  # crosses
    with pytest.raises(Gem15Error):
        extent.from_lonlat_points([])


def test_densified_ring():
    ring = extent.densified_ring(0, 0, 10, 20, 11)
    assert len(ring) == 41 and ring[0] == ring[-1] == (0, 0)
    assert (10, 20) in ring and (10, 0) in ring and (0, 20) in ring
