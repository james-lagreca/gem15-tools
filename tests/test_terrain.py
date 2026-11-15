"""Terrain defaults (pure Python)."""
import math

from gem15core import terrain


def test_radius_cells():
    assert terrain.radius_cells(300) == 20
    assert terrain.radius_cells(1) == 2          # never smaller than 2 pixels
    assert terrain.radius_cells(100, pixel_m=1) == 100


def test_circle_offsets():
    offs = terrain.circle_offsets(300)
    r = 20
    assert (0, 0) in offs and (r, 0) in offs and (0, -r) in offs and (r, r) not in offs
    assert abs(len(offs) - math.pi * r * r) < 2 * math.pi * r
