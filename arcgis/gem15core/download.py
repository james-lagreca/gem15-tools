# SPDX-License-Identifier: GPL-2.0-or-later
"""
How much a clip will download, worked out before anything is read.

GDAL reads a COG in whole internal blocks (512 x 512 px), so reading an area
at full resolution downloads about (blocks touched) x (size of one block).
`estimate` counts the blocks from the catalogue's tile grids alone;
`rough_mb` needs only the extent, for a warning before a tool runs.

Assumptions (flagged):
 D1. One full-resolution DTM block is about 0.9 MB compressed (measured on a
     Victorian tile, 2026-11-15). Smooth terrain compresses better, so flat
     areas will usually come in under the estimate.
 D2. The DSM COGs are pixel-interleaved (all 5 bands in every block), so one
     DSM band costs about 5 times the DTM.
 D3. Where tiles overlap, GDAL reads the blocks of every tile there (the VRT
     needs them all to fill NaN gaps), so overlaps count once per tile.
 D4. COG headers and overview levels are ignored (well under 1 MB per tile).
"""

import math
from dataclasses import dataclass

from . import COG_BLOCK_PX, PIXEL_M, Gem15Error, extent

BLOCK_MB = {"dtm": 0.9, "dsm": 4.5}          # D1, D2
EARTH_RADIUS_M = 6371008.8                   # mean radius (IUGG)


def block_mb(product):
    if product not in BLOCK_MB:
        raise Gem15Error(f"product must be one of {sorted(BLOCK_MB)}, not {product!r}")
    return BLOCK_MB[product]


def area_km2(bbox):
    """Area of a lon/lat box on a sphere, in km^2 (W > E crosses 180)."""
    w, s, e, n = extent.check(bbox)
    dlon = math.radians(extent.width_deg((w, s, e, n)))
    band = abs(math.sin(math.radians(n)) - math.sin(math.radians(s)))
    return EARTH_RADIUS_M ** 2 * dlon * band / 1e6


def rough_mb(bbox, product="dtm"):
    """Download from the area alone: no catalogue lookup, so fine for tool
    validation. Ignores overlaps (D3) and part-blocks at the edges, so the
    figure from `estimate` is usually somewhat larger."""
    block_km2 = (COG_BLOCK_PX * PIXEL_M / 1000.0) ** 2      # 7.68 km square
    return area_km2(bbox) / block_km2 * block_mb(product)


def blocks_touched(asset, bounds):
    """How many of an asset's 512 px blocks overlap bounds (xmin, ymin, xmax,
    ymax), given in the asset's own CRS."""
    left, bottom, right, top = asset.footprint()
    xmin, ymin, xmax, ymax = bounds
    x0, x1 = max(xmin, left), min(xmax, right)
    y0, y1 = max(ymin, bottom), min(ymax, top)
    if x1 <= x0 or y1 <= y0:
        return 0
    step = COG_BLOCK_PX * abs(asset.transform[0])            # block width in metres
    eps = 1e-6                                               # edges on a block line
    c0, c1 = math.floor((x0 - left) / step + eps), math.ceil((x1 - left) / step - eps)
    r0, r1 = math.floor((top - y1) / step + eps), math.ceil((top - y0) / step - eps)
    return max(0, c1 - c0) * max(0, r1 - r0)


@dataclass
class Estimate:
    blocks: int
    mb: float
    tiles: int        # tiles with at least one block read

    def describe(self):
        return (f"about {self.mb:,.0f} MB ({self.blocks} block(s) of "
                f"{COG_BLOCK_PX} x {COG_BLOCK_PX} px from {self.tiles} tile(s))")


def estimate(tiles, product, bounds_for):
    """Blocks and MB a full-resolution read of an area will download.

    bounds_for : {epsg: (xmin, ymin, xmax, ymax)} or a callable epsg -> bounds,
                 the area in each tile CRS (gdalops.aoi_bounds_in gives these).
    """
    get = bounds_for if callable(bounds_for) else bounds_for.__getitem__
    blocks = used = 0
    for t in tiles:
        a = t.asset(product)
        b = blocks_touched(a, get(a.epsg))
        blocks += b
        used += b > 0
    return Estimate(blocks, blocks * block_mb(product), used)
