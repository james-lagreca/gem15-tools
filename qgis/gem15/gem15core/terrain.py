# SPDX-License-Identifier: GPL-2.0-or-later
"""
Default settings for the terrain layers used in fault mapping, shared so the
ArcGIS and QGIS tools draw the same thing.

Choices (flagged; tune for your landforms):
 T1. Hillshade: multidirectional, sun altitude 30 degrees. A low sun picks out
     low scarps; multidirectional avoids hiding scarps parallel to one
     light direction. z factor 1, because x, y and z are all metres.
 T2. Local relief model (LRM) = DTM minus its mean over a circle of radius
     LRM_RADIUS_M. A radius of roughly 2 to 5 times the width of the
     landforms you are after works; 300 m suits scarps tens of metres wide.
 T3. Slope in degrees.
"""

import math

from . import PIXEL_M

HILLSHADE_ALTITUDE = 30.0
HILLSHADE_AZIMUTH = 315.0     # used only when not multidirectional
HILLSHADE_Z_FACTOR = 1.0
LRM_RADIUS_M = 300.0
LRM_RADIUS_MIN_M = 2 * PIXEL_M


def radius_cells(radius_m=LRM_RADIUS_M, pixel_m=PIXEL_M):
    """Neighbourhood radius in whole pixels (at least 2), e.g. 300 m -> 20."""
    return max(2, int(round(radius_m / pixel_m)))


def circle_offsets(radius_m=LRM_RADIUS_M, pixel_m=PIXEL_M):
    """(dx, dy) pixel offsets inside a circle of the radius, centre included.

    Used to build focal-mean kernels where the software has no circular
    neighbourhood of its own.
    """
    r = radius_cells(radius_m, pixel_m)
    return [(dx, dy) for dy in range(-r, r + 1) for dx in range(-r, r + 1)
            if math.hypot(dx, dy) <= r + 1e-9]
