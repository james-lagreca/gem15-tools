# SPDX-License-Identifier: GPL-2.0-or-later
"""
How much a clip will download, worked out from catalogue metadata alone.

GDAL fetches whole 512 x 512 px COG blocks, so the estimate counts the blocks
of every tile that the clip area touches. Tiles overlap and every
overlapping tile is read, which the count includes.

Measured figures (flagged; checked 2026-11-15 on live tiles):
 D1. A full-resolution DTM block is about 0.9 MB (DEFLATE gains little on
     float32 elevation).
 D2. DSM tiles store their 5 bands pixel-interleaved, so a block of any one
     DSM band costs about 5 times as much, ~4.5 MB.
The estimate is rough: rugged land compresses least, sea and flat ground
better, so expect the real download to be somewhat smaller.

This could move into gem15core for the ArcGIS clip tool: it needs no QGIS.
"""

import math

from .gem15core import COG_BLOCK_PX

MB_PER_BLOCK = {"dtm": 0.9, "dsm": 4.5}      # D1, D2


def blocks_touched(asset, bounds):
    """Number of COG blocks of `asset` that intersect bounds (xmin, ymin,
    xmax, ymax) in the asset's own CRS (metres); 0 if they do not meet."""
    left, bottom, right, top = asset.footprint()
    xmin, ymin = max(bounds[0], left), max(bounds[1], bottom)
    xmax, ymax = min(bounds[2], right), min(bounds[3], top)
    if xmax <= xmin or ymax <= ymin:
        return 0
    px = abs(asset.transform[0])
    rows, cols = asset.shape
    c0, c1 = int((xmin - left) // px), int(math.ceil((xmax - left) / px))
    r0, r1 = int((top - ymax) // px), int(math.ceil((top - ymin) / px))
    c1, r1 = min(c1, cols), min(r1, rows)
    b = COG_BLOCK_PX
    return (((c1 - 1) // b) - (c0 // b) + 1) * (((r1 - 1) // b) - (r0 // b) + 1)


def download_mb(tiles, product, bounds_for_epsg):
    """Estimated download (MB) to clip `tiles` to an area.

    bounds_for_epsg : callable epsg -> (xmin, ymin, xmax, ymax) of the clip
                      area in that UTM zone (e.g. gdalops.aoi_bounds_in).
    """
    cache, blocks = {}, 0
    for t in tiles:
        a = t.asset(product)
        if a.epsg not in cache:
            cache[a.epsg] = bounds_for_epsg(a.epsg)
        blocks += blocks_touched(a, cache[a.epsg])
    return blocks * MB_PER_BLOCK[product]
