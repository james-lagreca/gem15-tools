# SPDX-License-Identifier: GPL-2.0-or-later
"""
Map extents in any CRS -> lon/lat boxes for the catalogue lookup.

The outline of the extent is densified and each point projected, rather than
projecting the corner box, so curved edges are covered and a view across the
antimeridian gives a narrow box with W > E instead of one spanning the globe
(gem15core.extent.from_lonlat_points).

Assumptions (flagged):
 E1. Points that cannot be projected (outside the CRS's valid area, e.g. a
     world view in a polar CRS) are skipped; at least 3 must succeed.
 E2. Latitudes are clamped to +/-90 degrees; GEM-15 only covers 61 S to 84 N,
     so nothing is lost.
"""

import math

from qgis.core import (QgsCoordinateReferenceSystem, QgsCoordinateTransform,
                       QgsCsException, QgsPointXY, QgsProject)

from .gem15core import Gem15Error, extent

WGS84 = "EPSG:4326"


def lonlat_bbox(rect, crs, transform_context=None, points_per_side=21):
    """(W, S, E, N) degrees around a QgsRectangle in `crs` (W > E across 180)."""
    if rect is None or rect.isEmpty():
        raise Gem15Error("The extent is empty.")
    context = transform_context or QgsProject.instance().transformContext()
    ct = QgsCoordinateTransform(crs, QgsCoordinateReferenceSystem(WGS84), context)
    ring = extent.densified_ring(rect.xMinimum(), rect.yMinimum(),
                                 rect.xMaximum(), rect.yMaximum(), points_per_side)
    pts = []
    for x, y in ring:
        try:
            p = ct.transform(QgsPointXY(x, y))
        except QgsCsException:
            continue                                   # E1
        if math.isfinite(p.x()) and math.isfinite(p.y()):
            pts.append((p.x(), max(-90.0, min(90.0, p.y()))))   # E2
    if len(pts) < 3:
        raise Gem15Error("Could not work out the longitude/latitude of this extent "
                         f"from {crs.authid() or crs.description()}.")
    bbox = extent.from_lonlat_points(pts)
    if bbox[1] >= bbox[3] or bbox[0] == bbox[2]:
        raise Gem15Error(f"The extent is too small or degenerate: {bbox}")
    return extent.check(bbox)


def cell_count(bbox):
    """Number of 1 x 1 degree cells a box overlaps (for the P1 limit in settings)."""
    total = 0
    for w, s, e, n in extent.split_antimeridian(bbox):
        cols = math.ceil(e - 1e-9) - math.floor(w)
        rows = math.ceil(n - 1e-9) - math.floor(s)
        total += max(0, cols) * max(0, rows)
    return total
