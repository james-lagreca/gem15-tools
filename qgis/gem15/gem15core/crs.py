# SPDX-License-Identifier: GPL-2.0-or-later
"""
UTM helpers and the automatic choice of a projected CRS for an extent.

Rule (decided 2026-11-15):
  1. All tiles in one UTM zone -> that zone (the tiles' native grid, so no
     resampling).
  2. Tiles in several zones and the extent inside a region below ->
     that region's national projection (Australia: EPSG 7845, New Zealand:
     EPSG 2193).
  3. Otherwise a custom conformal projection centred on the extent: whichever
     of transverse Mercator, Lambert conformal conic or Mercator has the
     smallest scale error over it. Conformal keeps scarp shapes and
     orientations right, and hillshade and slope need metres in x and y.
The caller can always override the choice.

Assumptions (flagged):
 C1. The region boxes are rough rectangles. The Australia box also takes in
     the south-east tip of Papua New Guinea (north to 9 S); GA LCC still works
     there, with a larger scale error, which `describe()` reports.
 C2. GDA2020 and NZGD2000 differ from WGS 84 by no more than about 2 m, which
     is small against 15 m pixels; no datum transformation is chosen here.
 C3. Scale errors use spherical formulas: fine for choosing and reporting a
     projection, not for survey work.
 C4. GEM-15 tiles carry their EPSG code, so code should use that rather than
     a zone computed from longitude. `utm_zone_for` includes the Norway and
     Svalbard exceptions in case the data follow them (not yet checked).
"""

import math
from dataclasses import dataclass, field
from typing import Optional, Tuple

from . import Gem15Error, extent

MAX_AUTO_WIDTH_DEG = 60.0   # wider extents get no automatic custom projection


# --------------------------------------------------------------------------
# UTM
# --------------------------------------------------------------------------
def utm_zone_of_epsg(epsg):
    """32759 -> (59, 'S'); 32612 -> (12, 'N'); None if not WGS 84 / UTM."""
    epsg = int(epsg)
    if 32601 <= epsg <= 32660:
        return (epsg - 32600, "N")
    if 32701 <= epsg <= 32760:
        return (epsg - 32700, "S")
    return None


def zone_label(epsg):
    """32755 -> 'UTM 55S'; other codes -> 'EPSG:xxxx'. For layer names."""
    z = utm_zone_of_epsg(epsg)
    return f"UTM {z[0]}{z[1]}" if z else f"EPSG:{epsg}"


def utm_epsg(zone, south):
    if not 1 <= int(zone) <= 60:
        raise Gem15Error(f"UTM zone must be 1-60, not {zone}")
    return (32700 if south else 32600) + int(zone)


def central_meridian(zone):
    return 6 * int(zone) - 183


def utm_zone_for(lon, lat=0.0):
    """UTM zone for a point, including the Norway and Svalbard exceptions (C4)."""
    lon = ((lon + 180) % 360) - 180
    if 56 <= lat < 64 and 3 <= lon < 12:
        return 32
    if 72 <= lat < 84 and 0 <= lon < 42:
        return 31 if lon < 9 else 33 if lon < 21 else 35 if lon < 33 else 37
    return int((lon + 180) // 6) % 60 + 1


def utm_epsg_for(lon, lat):
    return utm_epsg(utm_zone_for(lon, lat), lat < 0)


def zone_distance(z1, z2):
    """How many zones apart two UTM zones are, wrapping at the antimeridian (60 -> 1)."""
    d = abs(int(z1) - int(z2)) % 60
    return min(d, 60 - d)


def utm_wkt(epsg):
    """OGC WKT1 (GDAL style, with EPSG authority codes) for a WGS 84 / UTM EPSG code."""
    info = utm_zone_of_epsg(epsg)
    if info is None:
        raise Gem15Error(f"EPSG:{epsg} is not a WGS 84 / UTM zone")
    zone, hemi = info
    return (
        f'PROJCS["WGS 84 / UTM zone {zone}{hemi}",'
        'GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563,'
        'AUTHORITY["EPSG","7030"]],AUTHORITY["EPSG","6326"]],'
        'PRIMEM["Greenwich",0,AUTHORITY["EPSG","8901"]],'
        'UNIT["degree",0.0174532925199433,AUTHORITY["EPSG","9122"]],'
        'AUTHORITY["EPSG","4326"]],'
        'PROJECTION["Transverse_Mercator"],'
        'PARAMETER["latitude_of_origin",0],'
        f'PARAMETER["central_meridian",{central_meridian(zone)}],'
        'PARAMETER["scale_factor",0.9996],'
        'PARAMETER["false_easting",500000],'
        f'PARAMETER["false_northing",{10000000 if hemi == "S" else 0}],'
        'UNIT["metre",1,AUTHORITY["EPSG","9001"]],'
        'AXIS["Easting",EAST],AXIS["Northing",NORTH],'
        f'AUTHORITY["EPSG","{int(epsg)}"]]')


# --------------------------------------------------------------------------
# Conformal projections on WGS 84
# --------------------------------------------------------------------------
_ESRI_GCS = ('GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",'
             'SPHEROID["WGS_1984",6378137.0,298.257223563]],'
             'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]]')


def _f(v):
    """Compact number for CRS strings: 147.5, 0.9996, 10000000."""
    return f"{float(v):.10g}"


@dataclass(frozen=True)
class Projection:
    """A conformal projection: 'tmerc', 'lcc' (two standard parallels) or 'merc'."""
    kind: str
    lon0: float
    k0: float = 1.0          # tmerc only
    lat0: float = 0.0        # lcc only
    sp1: float = 0.0         # lcc only
    sp2: float = 0.0         # lcc only
    lat_ts: float = 0.0      # merc only
    fe: float = 0.0
    fn: float = 0.0

    def scale(self, lon, lat):
        """Point scale factor k at (lon, lat), spherical approximation (C3)."""
        phi = math.radians(max(-89.0, min(89.0, lat)))
        if self.kind == "tmerc":
            dlam = math.radians(((lon - self.lon0 + 180) % 360) - 180)
            b = math.cos(phi) * math.sin(dlam)
            return self.k0 / math.sqrt(max(1e-12, 1 - b * b))
        if self.kind == "merc":
            return math.cos(math.radians(self.lat_ts)) / math.cos(phi)
        if self.kind == "lcc":
            p1, p2 = math.radians(self.sp1), math.radians(self.sp2)
            t = lambda p: math.tan(math.pi / 4 + p / 2)  # noqa: E731
            if abs(p1 - p2) < 1e-10:
                n = math.sin(p1)
            else:
                n = math.log(math.cos(p1) / math.cos(p2)) / math.log(t(p2) / t(p1))
            big_f = math.cos(p1) * t(p1) ** n / n
            return big_f * n / (math.cos(phi) * t(phi) ** n)
        raise Gem15Error(f"Unknown projection kind {self.kind!r}")

    def proj4(self):
        """PROJ string, for GDAL (SetFromUserInput) and QGIS (fromProj)."""
        tail = f"+x_0={_f(self.fe)} +y_0={_f(self.fn)} +datum=WGS84 +units=m +no_defs"
        if self.kind == "tmerc":
            return (f"+proj=tmerc +lat_0=0 +lon_0={_f(self.lon0)} +k_0={_f(self.k0)} "
                    + tail)
        if self.kind == "lcc":
            return (f"+proj=lcc +lat_0={_f(self.lat0)} +lon_0={_f(self.lon0)} "
                    f"+lat_1={_f(self.sp1)} +lat_2={_f(self.sp2)} " + tail)
        if self.kind == "merc":
            return f"+proj=merc +lon_0={_f(self.lon0)} +lat_ts={_f(self.lat_ts)} " + tail
        raise Gem15Error(f"Unknown projection kind {self.kind!r}")

    def esri_wkt(self, name):
        """Esri-dialect WKT, for arcpy.SpatialReference().loadFromString()."""
        head = f'PROJCS["{name}",{_ESRI_GCS},'
        fe = f'PARAMETER["False_Easting",{_f(self.fe)}],PARAMETER["False_Northing",{_f(self.fn)}],'
        if self.kind == "tmerc":
            body = ('PROJECTION["Transverse_Mercator"],' + fe +
                    f'PARAMETER["Central_Meridian",{_f(self.lon0)}],'
                    f'PARAMETER["Scale_Factor",{_f(self.k0)}],'
                    'PARAMETER["Latitude_Of_Origin",0.0],')
        elif self.kind == "lcc":
            body = ('PROJECTION["Lambert_Conformal_Conic"],' + fe +
                    f'PARAMETER["Central_Meridian",{_f(self.lon0)}],'
                    f'PARAMETER["Standard_Parallel_1",{_f(self.sp1)}],'
                    f'PARAMETER["Standard_Parallel_2",{_f(self.sp2)}],'
                    f'PARAMETER["Latitude_Of_Origin",{_f(self.lat0)}],')
        elif self.kind == "merc":
            body = ('PROJECTION["Mercator"],' + fe +
                    f'PARAMETER["Central_Meridian",{_f(self.lon0)}],'
                    f'PARAMETER["Standard_Parallel_1",{_f(self.lat_ts)}],')
        else:
            raise Gem15Error(f"Unknown projection kind {self.kind!r}")
        return head + body + 'UNIT["Meter",1.0]]'


def _samples(bbox, n=9):
    """n x n grid of (lon, lat) over bbox; longitudes unwrapped across 180."""
    w, s, e, nn = bbox
    if w > e:
        e += 360
    return [(w + i * (e - w) / (n - 1), s + j * (nn - s) / (n - 1))
            for i in range(n) for j in range(n)]


def max_scale_error(proj, bbox):
    """Largest |k - 1| over the extent, as a fraction (0.001 = 0.1 %)."""
    return max(abs(proj.scale(lon, lat) - 1) for lon, lat in _samples(bbox))


def _round_half(v):
    return round(v * 2) / 2


def custom_projection(bbox):
    """The conformal projection with the smallest scale error over bbox (rule 3).

    Central meridian and parallels are rounded to 0.5 degree, so nearby
    extents give the same CRS (handy when growing one mosaic dataset).
    """
    w, s, e, n = bbox
    width = extent.width_deg(bbox)
    if width > MAX_AUTO_WIDTH_DEG:
        raise Gem15Error(
            f"The extent is {width:.0f} degrees wide; that is too wide for one "
            "projected map at 15 m. Use a smaller extent, or one layer per UTM zone.")
    lon_c, lat_c = extent.centre(bbox)
    lon0 = _round_half(lon_c)
    if lon0 >= 180:
        lon0 -= 360
    cands = []

    # Transverse Mercator, secant: k0 chosen so the error is equal at the
    # central meridian (k0 - 1) and at the edge (k0 * kmax - 1).
    kmax = max(Projection("tmerc", lon0).scale(lon, lat) for lon, lat in _samples(bbox))
    k0 = round(2 / (1 + kmax), 6)
    cands.append(Projection("tmerc", lon0, k0=k0, fe=500000.0,
                            fn=10000000.0 if lat_c < 0 else 0.0))

    # Lambert conformal conic, standard parallels 1/6 in from each edge.
    # It degenerates when the parallels mirror each other across the equator.
    span = n - s
    sp1, sp2 = _round_half(s + span / 6), _round_half(n - span / 6)
    if sp1 != sp2 and abs(sp1 + sp2) >= 1.0:
        cands.append(Projection("lcc", lon0, lat0=_round_half(lat_c), sp1=sp1, sp2=sp2))

    # Mercator, for wide extents straddling the equator; secant parallel set
    # so the error at the equator equals the error at the extent's edge.
    if s < 0 < n:
        cmax = math.cos(math.radians(max(abs(s), abs(n))))
        lat_ts = math.degrees(math.acos(2 * cmax / (1 + cmax)))
        cands.append(Projection("merc", lon0, lat_ts=round(lat_ts, 2)))

    return min(cands, key=lambda p: max_scale_error(p, bbox))


# --------------------------------------------------------------------------
# Regions with a national projection (rule 2)
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Region:
    name: str
    box: Tuple[float, float, float, float]   # (W, S, E, N), C1
    epsg: int
    crs_name: str
    projection: Projection                   # same parameters, for scale error


REGIONS = (
    Region("Australia", (112.0, -44.5, 154.5, -9.0), 7845, "GDA2020 / GA LCC",
           Projection("lcc", 134.0, lat0=0.0, sp1=-18.0, sp2=-36.0)),
    Region("New Zealand", (166.0, -47.5, 179.0, -34.0), 2193,
           "NZGD2000 / New Zealand Transverse Mercator 2000",
           Projection("tmerc", 173.0, k0=0.9996, fe=1600000.0, fn=10000000.0)),
)


def region_for(bbox):
    """The first region whose box holds the whole extent, else None."""
    if extent.crosses_antimeridian(bbox):
        return None
    w, s, e, n = bbox
    for r in REGIONS:
        bw, bs, be, bn = r.box
        if w >= bw and e <= be and s >= bs and n <= bn:
            return r
    return None


# --------------------------------------------------------------------------
# The choice
# --------------------------------------------------------------------------
@dataclass
class CrsChoice:
    kind: str                      # "utm", "regional" or "custom"
    name: str
    reason: str
    max_scale_error: float         # fraction over the extent (C3)
    epsg: Optional[int] = None     # set for "utm" and "regional"
    projection: Optional[Projection] = None   # set for "custom"
    zones: Tuple[int, ...] = field(default_factory=tuple)  # tile EPSG codes seen

    def gdal_string(self):
        """'EPSG:xxxx' or a PROJ string: for GDAL SetFromUserInput / gdal.Warp dstSRS."""
        return f"EPSG:{self.epsg}" if self.epsg else self.projection.proj4()

    def qgis_string(self):
        """For QgsCoordinateReferenceSystem(...) / fromProj(...)."""
        return self.gdal_string()

    def esri_wkt(self):
        """Esri WKT for a custom choice (use arcpy.SpatialReference(epsg) otherwise)."""
        return self.projection.esri_wkt(self.name.replace(" ", "_")) if self.projection else None

    def describe(self):
        return (f"{self.name}. {self.reason} Largest scale error over the extent "
                f"about {100 * self.max_scale_error:.2f} %.")


def auto_crs(bbox, tile_epsgs):
    """Choose a projected CRS for an extent from its tiles' EPSG codes."""
    bbox = extent.check(bbox)
    zones = tuple(sorted({int(e) for e in tile_epsgs}))
    if not zones:
        raise Gem15Error("No tiles, so no CRS to choose.")

    if len(zones) == 1 and utm_zone_of_epsg(zones[0]):
        zone, hemi = utm_zone_of_epsg(zones[0])
        proj = Projection("tmerc", central_meridian(zone), k0=0.9996)
        return CrsChoice("utm", f"WGS 84 / UTM zone {zone}{hemi}",
                         "All tiles are in this UTM zone, so their native 15 m grid "
                         "is kept and nothing is resampled.",
                         max_scale_error(proj, bbox), epsg=zones[0], zones=zones)

    names = ", ".join(f"{z}{h}" for z, h in filter(None, map(utm_zone_of_epsg, zones)))
    region = region_for(bbox)
    if region is not None:
        return CrsChoice("regional", f"{region.crs_name} (EPSG {region.epsg})",
                         f"The tiles span UTM zones {names} and the extent lies in "
                         f"{region.name}, so tiles are resampled to this national grid.",
                         max_scale_error(region.projection, bbox),
                         epsg=region.epsg, zones=zones)

    proj = custom_projection(bbox)
    label = {"tmerc": "Transverse Mercator", "lcc": "Lambert Conformal Conic",
             "merc": "Mercator"}[proj.kind]
    hemi_lon = f"{abs(proj.lon0):g}{'E' if proj.lon0 >= 0 else 'W'}"
    return CrsChoice("custom", f"GEM-15 custom {label} {hemi_lon}",
                     f"The tiles span UTM zones {names}, so tiles are resampled to a "
                     "conformal projection centred on the extent.",
                     max_scale_error(proj, bbox), projection=proj, zones=zones)
