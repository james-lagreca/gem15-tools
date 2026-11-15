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
 E3. A view that contains a pole (e.g. a polar stereographic map) is widened
     to reach it and to cover every longitude (gem15core.extent.include_poles).
"""

import math

from qgis.core import (QgsCoordinateReferenceSystem, QgsCoordinateTransform,
                       QgsCsException, QgsPointXY, QgsProject)

from .gem15core import Gem15Error, cells, extent

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
    bbox = extent.include_poles(bbox, *poles_inside(rect, crs, context))   # E3
    return extent.check(bbox)


def poles_inside(rect, crs, transform_context=None):
    """(north, south): whether each pole lies inside the extent (E3). A polar
    view's outline never reaches the pole, so its box would stop short."""
    if crs.isGeographic():
        return (False, False)
    context = transform_context or QgsProject.instance().transformContext()
    ct = QgsCoordinateTransform(QgsCoordinateReferenceSystem(WGS84), crs, context)
    found = []
    for lat in (90.0, -90.0):
        try:
            p = ct.transform(QgsPointXY(0.0, lat))
            found.append(math.isfinite(p.x()) and math.isfinite(p.y())
                         and rect.contains(QgsPointXY(p.x(), p.y())))
        except QgsCsException:
            found.append(False)                        # cannot be projected (e.g. Mercator)
    return tuple(found)


def cell_count(bbox):
    """Number of 1 x 1 degree cells a box overlaps (for the P1 limit in settings)."""
    return cells.count_for_bbox(bbox)
