# SPDX-License-Identifier: GPL-2.0-or-later
"""
Default settings for the terrain layers used in fault mapping, shared so the
ArcGIS and QGIS tools draw the same thing.

Choices (flagged; tune for your landforms):
 T1. Hillshade: multidirectional by default, sun altitude 30 degrees. A low
     sun picks out low scarps; multidirectional avoids hiding scarps parallel
     to one light direction, but gives some fault strikes only about a third
     of the contrast of a sun shining straight across them (a 5 degree scarp
     face: 7 % against 24 % relative contrast, computed 2026-11-15). So a
     single sun is offered too, and "across a fault trend", which lights the
     scarp at right angles from the side nearer the north-west
     (sun_azimuth_across), so relief does not look inverted. Lower suns
     (about 20 degrees) suit plains, 30-35 degrees steep country.
 T2. Local relief model (LRM) = DTM minus its mean over a circle of radius
     LRM_RADIUS_M: a simple local relief, the first step of Hesse (2010)'s
     LRM (which goes on to fit a purged trend surface). Rule of thumb, not a
     calibrated value: a radius of roughly 2 to 5 times the width of the
     landforms you are after: LRM_PRESETS_M. Live, the work grows with the
     square of the radius (1000 m is about 10 times 300 m). Near water, data
     gaps and the edges of a clip the mean covers only part of the circle, so
     the LRM is biased there (about 2.5 m on a 1 degree slope at 300 m): the
     QGIS live LRM leaves that band blank; clips and ArcGIS's focal mean do
     not, so ignore the LRM within one radius of them.
 T3. Slope in degrees (Horn 1981, as GDAL and ArcGIS compute it).
 T4. z factor 1, because x, y and z are all metres. Exaggerating (2-3 on
     Australian plains) brings out subtle scarps but also the DTM's noise.
 T5. Fixed display ranges, not stretched to the view (DRA): hillshade 0-255,
     slope 0-SLOPE_DISPLAY_MAX_DEG, LRM +/- LRM_DISPLAY_RANGE_M (a step of
     height H shows as about +/- H/2, so steps over ~5 m reach the end
     colours). A view-dependent stretch made the same scarp look strong in
     one view and weak in the next, and stretched noise on flat plains to
     full contrast. Raw elevation keeps DRA, since its range varies.
 T6. Live LRM layers are drawn only at 1:LRM_MAX_SCALE and closer. Zoomed
     out, GDAL applies a VRT kernel filter only to full-resolution reads, and
     ArcGIS may compute raster functions at screen resolution, which would
     widen a neighbourhood given in cells. At 1:50,000 a screen pixel is
     13.2 m at 96 dpi, finer than the 15 m grid.
 T7. Canopy check: where DSM minus DTM exceeds CANOPY_THRESHOLD_M, the model
     removed trees or buildings and the DTM is its guess at the ground.
     Shrubs under the threshold, crops, and canopy the model missed are not
     flagged and can still bias the DTM.

References: Horn, B. K. P. (1981), Hill shading and the reflectance map,
Proc. IEEE 69, 14-47. Mark, R. K. (1992), Multidirectional, oblique-weighted,
shaded-relief image of the Island of Hawaii, USGS Open-File Report 92-422.
Hesse, R. (2010), LiDAR-derived Local Relief Models: a new tool for
archaeological prospection, Archaeological
Prospection 17, 67-72.
"""

import math

from . import PIXEL_M

HILLSHADE_ALTITUDE = 30.0
HILLSHADE_AZIMUTH = 315.0     # single sun default
HILLSHADE_Z_FACTOR = 1.0
ILLUMINATIONS = ("multidirectional", "single", "across_trend")

LRM_RADIUS_M = 300.0
LRM_RADIUS_MIN_M = 2 * PIXEL_M
LRM_PRESETS_M = (150.0, 300.0, 1000.0)
LRM_PRESET_NOTES = {150.0: "narrow scarps, offset channels",
                    300.0: "scarps tens of metres wide (default)",
                    1000.0: "broad scarps and fold scarps; slow when live"}

SLOPE_DISPLAY_MAX_DEG = 30.0
LRM_DISPLAY_RANGE_M = 2.5
LRM_MAX_SCALE = 50000.0
CANOPY_THRESHOLD_M = 3.0


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


def _angle_between(a, b):
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


def sun_azimuth_across(trend_deg):
    """Sun azimuth at right angles to a fault trend (degrees clockwise from
    north, either direction along the fault), on the side nearer 315 degrees
    so that relief is not seen inverted (T1). E.g. trend 0 (N-S) -> 270."""
    t = float(trend_deg) % 180.0
    a, b = (t + 90.0) % 360.0, (t + 270.0) % 360.0
    return a if _angle_between(a, 315.0) <= _angle_between(b, 315.0) else b


def sun_azimuth(illumination, azimuth=HILLSHADE_AZIMUTH, trend=None):
    """The single-sun azimuth for an illumination choice, or None for multidirectional."""
    if illumination == "multidirectional":
        return None
    if illumination == "across_trend":
        if trend is None:
            raise ValueError("Give the fault trend (degrees) to light across it.")
        return sun_azimuth_across(trend)
    return float(azimuth) % 360.0


def hillshade_label(illumination, altitude=HILLSHADE_ALTITUDE, azimuth=None, z=1.0):
    """Short description for layer names, e.g. 'sun 270 deg at 30 deg, z 2'."""
    how = ("multidirectional" if azimuth is None else f"sun {azimuth:g} deg")
    zz = f", z {z:g}" if abs(float(z) - 1.0) > 1e-9 else ""
    return f"{how}, altitude {altitude:g} deg{zz}"
