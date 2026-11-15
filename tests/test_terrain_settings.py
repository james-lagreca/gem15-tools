# SPDX-License-Identifier: GPL-2.0-or-later
"""Shared terrain settings (gem15core.terrain): sun across a fault trend,
presets and display ranges used by both tools."""
import pytest

from gem15core import crs, stac, terrain
from test_stac import FakeFetch, make_docs


@pytest.mark.parametrize("trend, sun", [
    (0, 270), (180, 270),          # N-S fault: lit from the west
    (90, 0), (270, 0),             # E-W fault: lit from the north
    (45, 315), (225, 315),         # NE-SW: from the NW
    (135, 225),                    # NW-SE: SW (45 deg from 315) beats NE (135 deg)
    (30, 300), (-30, 240),
])
def test_sun_across_trend(trend, sun):
    az = terrain.sun_azimuth_across(trend)
    assert az == pytest.approx(sun)
    d = abs((az - trend) % 180)
    assert d == pytest.approx(90)                               # at right angles


def test_sun_azimuth_choices():
    assert terrain.sun_azimuth("multidirectional") is None
    assert terrain.sun_azimuth("single", 400) == 40
    assert terrain.sun_azimuth("across_trend", trend=0) == 270
    with pytest.raises(ValueError, match="trend"):
        terrain.sun_azimuth("across_trend")
    assert terrain.hillshade_label("x", 30, None, 1) == "multidirectional, altitude 30 deg"
    assert terrain.hillshade_label("x", 20, 270, 2.5) == "sun 270 deg, altitude 20 deg, z 2.5"


def test_shared_display_settings():
    assert terrain.LRM_PRESETS_M == (150.0, 300.0, 1000.0)
    assert terrain.LRM_RADIUS_M in terrain.LRM_PRESETS_M
    assert set(terrain.LRM_PRESET_NOTES) == set(terrain.LRM_PRESETS_M)
    # A screen pixel at the LRM limit is finer than the 15 m grid at 96 dpi (T6).
    assert terrain.LRM_MAX_SCALE * 0.0254 / 96 < 15


def test_tile_helpers_shared_with_qgis():
    tiles = stac.find_tiles((172.6, -41.95, 173.2, -41.65), "dtm", fetch=FakeFetch(make_docs()))
    assert stac.tiles_bbox(tiles) == (172.0, -42.0, 174.0, -41.0)
    tag = stac.tiles_tag(tiles, "dtm", "dtm")
    assert tag.startswith("w172sm42_2t_") and tag == stac.tiles_tag(tiles[::-1], "dtm", "dtm")
    epsg, ring = stac.tile_outline(tiles[0])
    assert epsg == 32759 and ring[0] == ring[-1] and len(ring) == 4 * 20 + 1
    assert crs.zone_label(32755) == "UTM 55S" and crs.zone_label(7845) == "EPSG:7845"
