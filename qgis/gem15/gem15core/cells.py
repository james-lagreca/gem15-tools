# SPDX-License-Identifier: GPL-2.0-or-later
"""
GEM-15 group and cell names.

Naming (checked against the live catalogue on 2026-11-15, in both
hemispheres; "m" means minus, there is no zero padding):

  group  <p|m><lat>_<p|m><lon>  SW corner of a 10 x 10 degree block
         m50_p170 -> lon 170..180, lat -50..-40
         p30_m120 -> lon -120..-110, lat 30..40
         p0_p0    -> lon 0..10, lat 0..10
  cell   w<m?><lon>s<m?><lat>   SW corner of a 1 x 1 degree cell
         w172sm42 -> [172, -42, 173, -41]
         wm111s30 -> [-111, 30, -110, 31]

STAC item ids are the cell name with a "gem-15_" prefix. Groups only list
cells that hold data (mostly land), so a name formatted here may not exist.
"""

import math
import re

_GROUP_RE = re.compile(r"([mp])(\d+)_([mp])(\d+)")
_CELL_RE = re.compile(r"w(m?)(\d+)s(m?)(\d+)")
ITEM_PREFIX = "gem-15_"


def _signed(prefix, digits, minus):
    return -int(digits) if prefix == minus else int(digits)


def parse_group(name):
    """'m50_p170' -> (170, -50, 180, -40) as (W, S, E, N); None if not a group name."""
    m = _GROUP_RE.fullmatch(name or "")
    if not m:
        return None
    lat = _signed(m.group(1), m.group(2), "m")
    lon = _signed(m.group(3), m.group(4), "m")
    return (lon, lat, lon + 10, lat + 10)


def parse_cell(name):
    """'w172sm42' or 'gem-15_w172sm42' -> (172, -42, 173, -41); None if not a cell name."""
    name = (name or "")
    if name.startswith(ITEM_PREFIX):
        name = name[len(ITEM_PREFIX):]
    m = _CELL_RE.fullmatch(name)
    if not m:
        return None
    lon = _signed(m.group(1), m.group(2), "m")
    lat = _signed(m.group(3), m.group(4), "m")
    return (lon, lat, lon + 1, lat + 1)


def _sw_corner(lon, lat, step):
    """South-west corner of the step-degree block holding (lon, lat).
    Longitude 180 belongs to the last block (179..180), latitude 90 likewise."""
    lon = min(lon, 180 - 1e-9)
    lat = min(lat, 90 - 1e-9)
    return int(step * math.floor(lon / step)), int(step * math.floor(lat / step))


def group_name(lon, lat):
    """Name of the 10-degree group containing a point, e.g. (172.5, -41.5) -> 'm50_p170'."""
    x, y = _sw_corner(lon, lat, 10)
    return f"{'m' if y < 0 else 'p'}{abs(y)}_{'m' if x < 0 else 'p'}{abs(x)}"


def cell_name(lon, lat):
    """Name of the 1-degree cell containing a point, e.g. (172.5, -41.5) -> 'w172sm42'."""
    x, y = _sw_corner(lon, lat, 1)
    return f"w{'m' if x < 0 else ''}{abs(x)}s{'m' if y < 0 else ''}{abs(y)}"


def cell_names_for_bbox(bbox):
    """All 1-degree cell names whose area overlaps bbox (W, S, E, N), W < E.

    A cell that only touches the bbox along an edge is not included.
    """
    w, s, e, n = bbox
    x0, y0 = int(math.floor(w)), int(math.floor(s))
    x1, y1 = int(math.ceil(e)), int(math.ceil(n))   # exclusive
    return [cell_name(x + 0.5, y + 0.5)
            for y in range(max(y0, -90), min(y1, 90))
            for x in range(max(x0, -180), min(x1, 180))]
