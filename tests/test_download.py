# SPDX-License-Identifier: GPL-2.0-or-later
"""Download estimates (pure Python)."""
import math

import pytest

from gem15core import Gem15Error, download, stac


def asset(x0=300000.0, top=6000000.0, rows=8192, cols=8192):
    return stac.Asset("https://example/x.tif", 32755, (rows, cols),
                      (15.0, 0.0, x0, 0.0, -15.0, top), ("dtm",))


def tile(a):
    return stac.Tile("gem-15_w144sm38", "w144sm38", "m40_p140", (144, -38, 145, -37),
                     a.epsg, "", {"dtm": a, "dsm": a})


def test_area_km2():
    # 1 x 1 degree at the equator ~ 111.2 km x 110.6 km
    assert download.area_km2((0, 0, 1, 1)) == pytest.approx(12364, rel=0.002)
    # same area either side of 180 as the equivalent box elsewhere
    assert download.area_km2((179.5, -17, -179.5, -16)) == pytest.approx(
        download.area_km2((10, -17, 11, -16)))


def test_rough_mb_scales_with_product():
    dtm = download.rough_mb((144.0, -37.6, 144.3, -37.4), "dtm")
    assert dtm == pytest.approx(download.area_km2((144.0, -37.6, 144.3, -37.4))
                                / 7.68 ** 2 * 0.9)
    assert download.rough_mb((144.0, -37.6, 144.3, -37.4), "dsm") == pytest.approx(5 * dtm)
    with pytest.raises(Gem15Error):
        download.rough_mb((0, 0, 1, 1), "dem")


def test_blocks_touched():
    a = asset()
    step = 512 * 15.0                       # 7680 m per block
    left, top = 300000.0, 6000000.0
    # inside one block
    assert download.blocks_touched(a, (left + 10, top - 100, left + 20, top - 10)) == 1
    # exactly one block, edges on block lines
    assert download.blocks_touched(a, (left, top - step, left + step, top)) == 1
    # just over a block line in x -> 2 blocks
    assert download.blocks_touched(a, (left, top - step, left + step + 15, top)) == 2
    # the whole tile: 16 x 16 blocks; larger bounds are clipped to the tile
    assert download.blocks_touched(a, (0, 0, 1e7, 1e7)) == 256
    # outside
    assert download.blocks_touched(a, (0, 0, 10, 10)) == 0


def test_estimate_sums_tiles_and_counts_overlaps():
    a, b = asset(), asset(x0=300000.0 + 7680 * 15)        # b starts 15 blocks east of a
    tiles = [tile(a), tile(b)]
    bounds = (300000.0 + 7680 * 15, 6000000.0 - 7680, 300000.0 + 7680 * 16, 6000000.0)
    est = download.estimate(tiles, "dtm", {32755: bounds})
    assert (est.blocks, est.tiles) == (2, 2)               # one block in each tile (D3)
    assert est.mb == pytest.approx(1.8)
    assert "1.8" not in est.describe() and "about 2 MB" in est.describe()
    assert download.estimate(tiles, "dsm", lambda epsg: bounds).mb == pytest.approx(9.0)


def test_rough_and_exact_agree_roughly():
    """For an area inside one tile, rough and block-count estimates are close."""
    lat = -37.5
    km_per_deg_lon = 111.195 * math.cos(math.radians(lat))
    a = asset(x0=0.0, top=100000.0, rows=8192, cols=8192)
    side = 7680 * 4                                        # 4 x 4 blocks
    est = download.estimate([tile(a)], "dtm", {32755: (0.0, 100000.0 - side, side, 100000.0)})
    dlon, dlat = side / 1000 / km_per_deg_lon, side / 1000 / 111.195
    rough = download.rough_mb((144.0, lat, 144.0 + dlon, lat + dlat), "dtm")
    assert est.blocks == 16 and rough == pytest.approx(est.mb, rel=0.02)
