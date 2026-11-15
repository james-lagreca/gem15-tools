# SPDX-License-Identifier: GPL-2.0-or-later
"""
Longitude/latitude bounding boxes.

A bbox is (W, S, E, N) in decimal degrees on WGS 84. A box that crosses the
antimeridian (180 degrees) is written with W > E, e.g. Fiji is roughly
(177, -19, -178, -16). Functions that need ordinary boxes split such a box in
two first (`split_antimeridian`).
"""

from . import Gem15Error


def check(bbox):
    """Return bbox as a tuple of floats, or raise Gem15Error if it is not (W, S, E, N)."""
    try:
        w, s, e, n = (float(v) for v in bbox)
    except (TypeError, ValueError):
        raise Gem15Error(f"Expected (W, S, E, N) in degrees, got {bbox!r}")
    if not (-180 <= w <= 180 and -180 <= e <= 180 and -90 <= s < n <= 90 and w != e):
        raise Gem15Error(
            f"Not a valid lon/lat box (W, S, E, N): {bbox!r}. Longitudes must be "
            "within -180..180 and latitudes within -90..90, with S < N.")
    return (w, s, e, n)


def crosses_antimeridian(bbox):
    return bbox[0] > bbox[2]


def split_antimeridian(bbox):
    """[bbox] for an ordinary box; two boxes, east and west of 180, if it crosses."""
    w, s, e, n = bbox
    if w > e:
        return [(w, s, 180.0, n), (-180.0, s, e, n)]
    return [tuple(bbox)]


def width_deg(bbox):
    """East-west width in degrees, allowing for the antimeridian."""
    w, _, e, _ = bbox
    return (e - w) % 360 or 360.0


def centre(bbox):
    """(lon, lat) of the box centre, allowing for the antimeridian."""
    w, s, e, n = bbox
    lon = w + width_deg(bbox) / 2
    if lon >= 180:
        lon -= 360
    return (lon, (s + n) / 2)


def intersects(a, b):
    """True if two boxes overlap with non-zero area (either may cross 180)."""
    for p in split_antimeridian(a):
        for q in split_antimeridian(b):
            if p[0] < q[2] and p[2] > q[0] and p[1] < q[3] and p[3] > q[1]:
                return True
    return False


def pad(bbox, deg):
    """Grow a box by `deg` on every side (latitudes clamped, longitudes wrapped)."""
    w, s, e, n = bbox
    if width_deg(bbox) + 2 * deg >= 360:
        return (-180.0, max(s - deg, -90.0), 180.0, min(n + deg, 90.0))
    w, e = w - deg, e + deg
    if w < -180:
        w += 360
    if e > 180:
        e -= 360
    return (w, max(s - deg, -90.0), e, min(n + deg, 90.0))


def from_lonlat_points(points):
    """Smallest box around (lon, lat) points, crossing the antimeridian if that
    gives a narrower box (e.g. points at 179 E and 179 W -> (179, .., -179, ..)).

    Use this on a densified, projected outline of a map extent rather than on
    its projected corner box, which would span the whole globe at 180.
    """
    pts = [(lon if -180 <= lon <= 180 else ((lon + 180) % 360) - 180, lat)
           for lon, lat in points]
    if not pts:
        raise Gem15Error("No points to build an extent from.")
    lons = [p[0] for p in pts]
    lats = [p[1] for p in pts]
    s, n = min(lats), max(lats)
    plain = (min(lons), max(lons))
    shifted = [lon % 360 for lon in lons]            # 0..360, so 180 is interior
    wrap = (min(shifted), max(shifted))
    if wrap[1] - wrap[0] < plain[1] - plain[0] - 1e-9:
        w, e = (v - 360 if v > 180 else v for v in wrap)
        return (w, s, e, n)                          # W > E: crosses 180
    return (plain[0], s, plain[1], n)


def densified_ring(x0, y0, x1, y1, n=11):
    """Closed ring of points around a rectangle, n per side (corners shared),
    anticlockwise from (x0, y0) and back to it: 4 * (n - 1) + 1 points.
    Project these, not just the corners, so curved edges are covered."""
    ring = []
    for i in range(n):
        f = i / (n - 1)
        ring.append((x0 + f * (x1 - x0), y0))
    for i in range(1, n):
        f = i / (n - 1)
        ring.append((x1, y0 + f * (y1 - y0)))
    for i in range(1, n):
        f = i / (n - 1)
        ring.append((x1 - f * (x1 - x0), y1))
    for i in range(1, n):
        f = i / (n - 1)
        ring.append((x0, y1 - f * (y1 - y0)))
    return ring
