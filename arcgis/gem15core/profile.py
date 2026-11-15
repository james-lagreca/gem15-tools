# SPDX-License-Identifier: GPL-2.0-or-later
"""
Swath profiles across fault scarps, and scarp separation from far-field fits.

A single-pixel profile of the GEM-15 DTM is noisy (the DTM is predicted by a
machine-learning model, so neighbouring pixels carry independent-looking
errors). A swath profile samples a band of the DEM along the line and
summarises each step along it (min, quartiles, median, max, mean), which damps
that noise and shows how consistent a scarp is along strike.

Needs numpy only (ships with ArcGIS Pro and QGIS); `plot_png` also uses
matplotlib if it is there. No GDAL, arcpy or QGIS: callers read the DEM
window themselves (see `window_for`) and pass the array in.

    win = window_for(line, 300.0, gt, cols, rows)
    arr = <read win from the raster>            # NaN for nodata
    sw = sample_swath(arr, window_geotransform(gt, win), line, 300.0)
    fit = scarp_height(sw, upper=(900, 1400), lower=(100, 600))
    to_csv(sw, "profile.csv", fit); plot_png(sw, "profile.png", fit)

Assumptions and choices (flagged):
 S1. The array's geotransform (GDAL order: x0, dx, 0, y0, 0, -dy) is north-up
     with no rotation, in a projected CRS in metres, and the line is in the
     same CRS. Distances are grid distances: no scale-factor correction (UTM
     within its zone is within 0.04 % of true; GA LCC, EPSG 7845, up to about
     1.2 %). Heights are unaffected; slopes carry the same small error. A pixel
     size under 0.01 is taken to mean degrees and refused.
 S2. Bilinear interpolation between the four nearest pixel centres. A sample
     is NaN if any of the four is NaN or lies outside the array (rather than
     renormalising the weights), so no height is ever made up across a data
     gap or an edge. The cost is about half a pixel lost round each gap, which
     the swath statistics absorb.
 S3. Stations sit at 0, step, 2 step, ... along the line (cumulative distance
     through the vertices), up to its length; a remainder shorter than one
     step at the end is not sampled. Each station is one bin: a row of samples
     across the line, at the station's distance (the bin centre). Across-line
     offsets are k = floor(width / 2 / step) steps each side of the line, so
     the band is symmetrical and never wider than asked (300 m at 15 m steps
     -> 21 samples, -150 to +150 m).
 S4. Samples run perpendicular to the segment the station is on. Positive
     offsets are on the LEFT of the direction of travel (travelling east,
     positive is north). At a bend the direction switches abruptly, so samples
     crowd on the inside of the bend and spread on the outside: draw lines
     straight across a scarp.
 S5. Percentiles use numpy's default (linear interpolation between ranks).
     A bin with no valid sample has n = 0 and NaN statistics. The bins are
     not detrended: where the surface slopes along strike (across the band),
     each bin spreads by slope x width, the median then averages out less
     noise, and a data gap on one side of the band shifts that bin's
     statistics. Keep the band narrow there, or fit the min or max envelope
     with care.
 S6. `scarp_height` returns the vertical separation (VS) of the two far-field
     surfaces at the scarp midpoint, not the crest-to-base height of the scarp
     face. Each far field is an ordinary least-squares line through the chosen
     statistic of each bin (equal weights). The 1-sigma uncertainty combines in
     quadrature the standard error of each fitted line at the midpoint (the
     error of the line itself, not of a new observation) and assumes the bins'
     residuals are independent. Neighbouring bins are correlated (interpolation,
     the DEM's own smoothing), so treat it as a lower bound: on synthetic
     scarps with uncorrelated 0.5 m pixel noise the real scatter of the
     separation was about 1.4 times sigma (300 noise seeds, 2026-11-15), and
     real DEM errors are correlated over longer distances. It also leaves out
     the choice of ranges, which is often the larger error: try a few.
 S7. The steepest slope is the central difference over three bins (a 2-step
     baseline, 30 m at 15 m steps), to damp noise. Scarp faces narrower than
     that, which a 15 m DEM cannot resolve anyway, come out gentler than they
     are. Slopes are signed: positive where the surface rises in the direction
     of travel.
 S8. Guards against mistakes, not limits of the method: line length at most
     100 km, swath width at most 5 km, at most 5 million samples (the sample
     array is then 40 MB; the DEM window a caller reads for a 100 km x 5 km
     swath can be far larger). Real scarp profiles are a few km long.
"""

import csv
import math
from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np

from . import PIXEL_M

MAX_LINE_M = 100000.0          # S8
MAX_WIDTH_M = 5000.0           # S8
MAX_SAMPLES = 5000000          # S8
MIN_FIT_BINS = 3               # a line fit with an uncertainty needs 3 points
DEFAULT_WIDTH_M = 300.0
STATS = ("min", "p25", "median", "p75", "max", "mean")
CSV_COLUMNS = ("distance_m", "n", "min_m", "p25_m", "median_m", "p75_m", "max_m", "mean_m")
_CHUNK_SAMPLES = 250000        # interpolate in chunks to keep temporaries small


# --------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------
@dataclass(eq=False)           # numpy arrays: == would be ambiguous
class Swath:
    """A swath profile. Arrays are 1-D, one value per bin (station), except
    `samples` (bins x offsets) and `offsets`."""
    distance: np.ndarray       # bin centres = station distances along the line, m
    n: np.ndarray              # valid samples per bin
    min: np.ndarray
    p25: np.ndarray
    median: np.ndarray
    p75: np.ndarray
    max: np.ndarray
    mean: np.ndarray
    width_m: float             # width asked for
    step_m: float
    length_m: float            # length of the whole line
    offsets: np.ndarray        # across-line offsets, m, positive on the left (S4)
    samples: np.ndarray        # interpolated heights, bins x offsets, NaN = no data
    station_x: np.ndarray      # station positions on the line, in the line's CRS
    station_y: np.ndarray

    def stat(self, name):
        """One of the summary arrays by name: min, p25, median, p75, max, mean."""
        if name not in STATS:
            raise ValueError(f"Statistic must be one of {', '.join(STATS)}, not {name!r}.")
        return getattr(self, name)


@dataclass
class LineFit:
    """Least-squares line z = intercept + slope * distance, with what is needed
    for the standard error of the fitted line anywhere along it."""
    intercept: float
    slope: float               # m per m along the line
    n: int                     # bins used
    d_mean: float
    sxx: float                 # sum of squared distance deviations
    rms_m: float               # residual standard deviation (n - 2 degrees of freedom)

    def at(self, d):
        return self.intercept + self.slope * np.asarray(d, dtype=float)

    def se_at(self, d):
        """Standard error of the fitted line (mean response) at distance d (S6)."""
        d = np.asarray(d, dtype=float)
        return self.rms_m * np.sqrt(1.0 / self.n + (d - self.d_mean) ** 2 / self.sxx)

    @property
    def slope_deg(self):
        return math.degrees(math.atan(self.slope))


@dataclass
class ScarpFit:
    """Far-field fits either side of a scarp and the separation between them."""
    stat: str
    upper_range: Tuple[float, float]   # sorted (start, end) along the line, m
    lower_range: Tuple[float, float]
    upper: LineFit
    lower: LineFit
    midpoint_m: float                  # halfway between the ranges' inner ends
    separation_m: float                # upper fit minus lower fit at the midpoint
    separation_sigma_m: float          # 1 sigma (S6)
    upper_slope_deg: float             # signed, + rises along the line (S7)
    lower_slope_deg: float
    steepest_slope_deg: float          # signed; NaN if no bin between the ranges
    steepest_at_m: float

    def describe(self):
        """Plain-English summary lines (used in CSV headers and plot notes)."""
        u0, u1 = self.upper_range
        l0, l1 = self.lower_range
        steep = ("n/a (no valid bins between the ranges)"
                 if math.isnan(self.steepest_slope_deg) else
                 f"{self.steepest_slope_deg:.1f} deg at {self.steepest_at_m:.0f} m")
        return [
            f"Vertical separation {self.separation_m:.2f} +/- {self.separation_sigma_m:.2f} m "
            f"(1 sigma, lower bound) at {self.midpoint_m:.0f} m, from the {self.stat} profile",
            f"Upper far field {u0:.0f}-{u1:.0f} m: {self.upper.n} bins, "
            f"slope {self.upper_slope_deg:.2f} deg, rms {self.upper.rms_m:.2f} m",
            f"Lower far field {l0:.0f}-{l1:.0f} m: {self.lower.n} bins, "
            f"slope {self.lower_slope_deg:.2f} deg, rms {self.lower.rms_m:.2f} m",
            f"Steepest slope between the ranges (3-bin gradient): {steep}",
            "Slopes are signed: positive where the surface rises along the line.",
        ]


# --------------------------------------------------------------------------
# Checks shared by the public functions
# --------------------------------------------------------------------------
def _check_geotransform(geotransform):
    gt = tuple(float(v) for v in geotransform)
    if len(gt) != 6:
        raise ValueError("The geotransform must have 6 numbers (GDAL order).")
    x0, dx, rx, y0, ry, dy = gt
    if rx != 0.0 or ry != 0.0:
        raise ValueError("The raster is rotated; swath profiles need a north-up raster.")
    if dx <= 0.0 or dy >= 0.0:
        raise ValueError("The geotransform must be north-up (positive pixel width, "
                         "negative pixel height).")
    if dx < 0.01 or -dy < 0.01:
        raise ValueError(f"Pixel size {dx:g} x {-dy:g} looks like degrees; swath profiles "
                         "need a projected coordinate system in metres (S1).")
    return gt


def _check_line(line_xy, width_m, step_m=None):
    """Line as an (n, 2) float array without repeated vertices, plus its
    cumulative distances and length. Raises ValueError for anything unusable
    (S8). Without a step, only the length limit is checked."""
    pts = np.asarray(line_xy, dtype=float)
    if pts.ndim != 2 or pts.shape[1] != 2 or len(pts) < 2:
        raise ValueError("The line needs at least two (x, y) vertices.")
    if not np.all(np.isfinite(pts)):
        raise ValueError("The line has a vertex that is not a finite number.")
    if step_m is not None and not (step_m > 0.0 and math.isfinite(step_m)):
        raise ValueError(f"The step must be a positive distance in metres, not {step_m}.")
    if not (0.0 <= width_m <= MAX_WIDTH_M):
        raise ValueError(f"The swath width must be between 0 and {MAX_WIDTH_M:.0f} m, "
                         f"not {width_m} m.")
    seg = np.hypot(*np.diff(pts, axis=0).T)
    pts = np.vstack([pts[:1], pts[1:][seg > 0.0]])          # drop repeated vertices
    seg = seg[seg > 0.0]
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    length = float(cum[-1])
    if length > MAX_LINE_M:
        raise ValueError(f"The line is {length / 1000:.1f} km long; the limit is "
                         f"{MAX_LINE_M / 1000:.0f} km. Is it in metres, in the DEM's "
                         "coordinate system?")
    if length == 0.0 or (step_m is not None and length < step_m):
        raise ValueError(f"The line is only {length:g} long, less than one step "
                         f"({step_m or PIXEL_M:g} m). Is it in metres, in the DEM's "
                         "coordinate system?")
    return pts, cum, length


def _offsets(width_m, step_m):
    k = int(math.floor(width_m / 2.0 / step_m + 1e-9))      # S3
    return step_m * np.arange(-k, k + 1, dtype=float)


# --------------------------------------------------------------------------
# Reading only what is needed
# --------------------------------------------------------------------------
def window_for(line_xy, width_m, geotransform, raster_cols, raster_rows, pad_px=2):
    """Pixel window (xoff, yoff, xsize, ysize) covering the line buffered by
    width / 2 plus `pad_px` pixels, clipped to the raster.

    Read this window (GDAL ReadAsArray(xoff, yoff, xsize, ysize), arcpy
    RasterToNumPyArray with the window's lower-left corner, ...) and pass
    `window_geotransform(geotransform, window)` with it to `sample_swath`;
    the result is the same as sampling the whole raster. A pad of at least
    1 pixel is needed for the bilinear neighbours (S2); 2 leaves a margin.
    Raises ValueError if the swath misses the raster.
    """
    x0, dx, _, y0, _, dy = _check_geotransform(geotransform)
    pts, _, _ = _check_line(line_xy, float(width_m))
    pad = int(pad_px)
    if pad < 0:
        raise ValueError("pad_px must be 0 or more.")
    half = float(width_m) / 2.0
    # The buffered polyline lies inside its vertices' bounding box grown by
    # half the width, so that box (in pixels, rounded out) is enough.
    xmin, ymin = pts.min(axis=0) - half
    xmax, ymax = pts.max(axis=0) + half
    c0 = math.floor((xmin - x0) / dx) - pad
    c1 = math.ceil((xmax - x0) / dx) + pad                  # exclusive
    r0 = math.floor((ymax - y0) / dy) - pad                 # dy < 0: top row first
    r1 = math.ceil((ymin - y0) / dy) + pad
    c0, c1 = max(c0, 0), min(c1, int(raster_cols))
    r0, r1 = max(r0, 0), min(r1, int(raster_rows))
    if c1 <= c0 or r1 <= r0:
        raise ValueError("The swath does not overlap the raster.")
    return (int(c0), int(r0), int(c1 - c0), int(r1 - r0))


def window_geotransform(geotransform, window):
    """Geotransform of a pixel window (xoff, yoff, xsize, ysize) of a raster."""
    x0, dx, rx, y0, ry, dy = _check_geotransform(geotransform)
    xoff, yoff = window[0], window[1]
    return (x0 + xoff * dx, dx, rx, y0 + yoff * dy, ry, dy)


# --------------------------------------------------------------------------
# Sampling
# --------------------------------------------------------------------------
def bilinear(array, geotransform, x, y):
    """Bilinear heights at points (x, y) in the raster's CRS; NaN where any of
    the four neighbouring pixel centres is NaN or off the array (S2)."""
    a = np.asarray(array)
    if a.ndim != 2 or a.size == 0:
        raise ValueError("The DEM must be a 2-D array with at least one pixel.")
    x0, dx, _, y0, _, dy = _check_geotransform(geotransform)
    x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    # Fractional column/row measured from pixel CENTRES (GDAL's x0, y0 are
    # the outer corner of the top-left pixel, hence the half pixel).
    u = (x - x0) / dx - 0.5
    v = (y - y0) / dy - 0.5
    c = np.floor(u)
    r = np.floor(v)
    fu, fv = u - c, v - r
    rows, cols = a.shape
    inside = (c >= 0) & (c + 1 <= cols - 1) & (r >= 0) & (r + 1 <= rows - 1)
    ci = np.where(inside, c, 0).astype(np.intp)
    ri = np.where(inside, r, 0).astype(np.intp)
    ci1 = np.minimum(ci + 1, cols - 1)
    ri1 = np.minimum(ri + 1, rows - 1)
    z = ((1 - fu) * (1 - fv) * a[ri, ci] + fu * (1 - fv) * a[ri, ci1]
         + (1 - fu) * fv * a[ri1, ci] + fu * fv * a[ri1, ci1]).astype(float)
    # NaN * 0 is NaN, so a NaN neighbour spoils the sample even at zero weight:
    # that is the strict rule of S2, applied the same everywhere.
    z[~inside] = np.nan
    return z


def _stations(pts, cum, length, step_m):
    """Station distances, positions and left-normal unit vectors (S3, S4)."""
    nst = int(math.floor(length / step_m + 1e-9)) + 1
    dist = step_m * np.arange(nst, dtype=float)
    seg = np.clip(np.searchsorted(cum, dist, side="right") - 1, 0, len(pts) - 2)
    p0, p1 = pts[seg], pts[seg + 1]
    seg_len = (cum[seg + 1] - cum[seg])[:, None]
    along = (p1 - p0) / seg_len                              # unit direction of travel
    xy = p0 + along * (dist - cum[seg])[:, None]
    left = np.column_stack([-along[:, 1], along[:, 0]])     # 90 deg anticlockwise (S4)
    return dist, xy, left


def sample_swath(array, geotransform, line_xy, width_m=DEFAULT_WIDTH_M, step_m=PIXEL_M):
    """Swath profile of a DEM along a polyline.

    array        : 2-D float array, NaN = no data (e.g. a window read with
                   `window_for`)
    geotransform : GDAL-style (x0, dx, 0, y0, 0, -dy) of that array, projected,
                   metres, north-up (S1)
    line_xy      : [(x, y), ...] with 2 or more vertices, same CRS
    width_m      : full width of the band (S3); 0 gives a single-line profile
    step_m       : spacing of stations along the line and of samples across it
    """
    width_m, step_m = float(width_m), float(step_m)
    pts, cum, length = _check_line(line_xy, width_m, step_m)
    gt = _check_geotransform(geotransform)
    a = np.asarray(array, dtype=float)
    offsets = _offsets(width_m, step_m)
    dist, xy, left = _stations(pts, cum, length, step_m)
    if len(dist) * len(offsets) > MAX_SAMPLES:
        raise ValueError(f"{len(dist)} stations x {len(offsets)} offsets is more than "
                         f"{MAX_SAMPLES:,} samples; use a larger step or a narrower swath.")

    samples = np.empty((len(dist), len(offsets)))
    per_chunk = max(1, _CHUNK_SAMPLES // len(offsets))
    for i in range(0, len(dist), per_chunk):
        sl = slice(i, i + per_chunk)
        x = xy[sl, 0:1] + left[sl, 0:1] * offsets[None, :]
        y = xy[sl, 1:2] + left[sl, 1:2] * offsets[None, :]
        samples[sl] = bilinear(a, gt, x, y)

    stats = _summarise(samples)
    return Swath(distance=dist, width_m=width_m, step_m=step_m, length_m=length,
                 offsets=offsets, samples=samples, station_x=xy[:, 0].copy(),
                 station_y=xy[:, 1].copy(), **stats)


def _summarise(samples):
    """Per-bin statistics, NaN-aware, without warnings for empty bins (S5)."""
    ok = np.isfinite(samples)
    n = ok.sum(axis=1)
    out = {k: np.full(len(samples), np.nan) for k in STATS}
    has = n > 0
    if has.any():
        s = samples[has]
        q = np.nanpercentile(s, [25.0, 50.0, 75.0], axis=1)
        out["p25"][has], out["median"][has], out["p75"][has] = q
        out["min"][has] = np.nanmin(s, axis=1)
        out["max"][has] = np.nanmax(s, axis=1)
        out["mean"][has] = np.nanmean(s, axis=1)
    out["n"] = n
    return out


# --------------------------------------------------------------------------
# Scarp separation
# --------------------------------------------------------------------------
def _range(r, name):
    try:
        a, b = (float(v) for v in r)
    except (TypeError, ValueError):
        raise ValueError(f"The {name} range must be two distances along the line, "
                         f"e.g. (100, 600), not {r!r}.") from None
    if not (math.isfinite(a) and math.isfinite(b)) or a == b:
        raise ValueError(f"The {name} range {r!r} must be two different distances.")
    return (min(a, b), max(a, b))


def _fit_line(d, z, name, rng):
    if len(d) < MIN_FIT_BINS:
        raise ValueError(
            f"The {name} range {rng[0]:g}-{rng[1]:g} m has {len(d)} bin(s) with data; "
            f"at least {MIN_FIT_BINS} are needed to fit a line with an uncertainty. "
            "Widen the range or move it onto the profile.")
    d_mean, z_mean = d.mean(), z.mean()
    sxx = float(((d - d_mean) ** 2).sum())
    slope = float(((d - d_mean) * (z - z_mean)).sum() / sxx)
    resid = z - (z_mean + slope * (d - d_mean))
    rms = math.sqrt(float((resid ** 2).sum()) / (len(d) - 2))
    return LineFit(intercept=float(z_mean - slope * d_mean), slope=slope, n=len(d),
                   d_mean=float(d_mean), sxx=sxx, rms_m=rms)


def scarp_height(swath, upper, lower, stat="median"):
    """Vertical separation across a scarp from straight-line fits to the far
    fields either side (S6), with far-field and steepest slopes (S7).

    upper, lower : (start, end) distances along the profile, m, of the
                   surfaces above and below the scarp, in either order and on
                   either side; they must not overlap or touch. The scarp
                   midpoint is halfway between their inner ends.
    stat         : which bin statistic to fit (default the median).
    """
    z_all = swath.stat(stat)
    up, lo = _range(upper, "upper"), _range(lower, "lower")
    if max(up[0], lo[0]) <= min(up[1], lo[1]):
        raise ValueError(f"The upper ({up[0]:g}-{up[1]:g} m) and lower ({lo[0]:g}-{lo[1]:g} m) "
                         "ranges overlap or touch; leave a gap between them for the scarp.")
    d_all = swath.distance
    valid = np.isfinite(z_all)

    def pick(rng):
        sel = valid & (d_all >= rng[0] - 1e-9) & (d_all <= rng[1] + 1e-9)
        return d_all[sel], z_all[sel]

    fu = _fit_line(*pick(up), "upper", up)
    fl = _fit_line(*pick(lo), "lower", lo)

    # Inner ends: the facing ends of the two ranges.
    gap = (up[1], lo[0]) if up[1] < lo[0] else (lo[1], up[0])
    mid = 0.5 * (gap[0] + gap[1])
    sep = float(fu.at(mid) - fl.at(mid))
    sigma = float(math.hypot(fu.se_at(mid), fl.se_at(mid)))

    # Steepest slope of the profile between the ranges: central difference
    # over three bins (S7); neighbours may fall inside the ranges.
    steep, steep_at = float("nan"), float("nan")
    idx = np.nonzero((d_all > gap[0]) & (d_all < gap[1]))[0]
    idx = idx[(idx > 0) & (idx < len(d_all) - 1)]
    if len(idx):
        grad = (z_all[idx + 1] - z_all[idx - 1]) / (d_all[idx + 1] - d_all[idx - 1])
        good = np.isfinite(grad)
        if good.any():
            k = int(np.argmax(np.where(good, np.abs(grad), -1.0)))
            steep = math.degrees(math.atan(float(grad[k])))
            steep_at = float(d_all[idx[k]])

    return ScarpFit(stat=stat, upper_range=up, lower_range=lo, upper=fu, lower=fl,
                    midpoint_m=mid, separation_m=sep, separation_sigma_m=sigma,
                    upper_slope_deg=fu.slope_deg, lower_slope_deg=fl.slope_deg,
                    steepest_slope_deg=steep, steepest_at_m=steep_at)


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------
def _cell(v, fmt="{:.3f}"):
    """CSV cell: empty for NaN, so spreadsheets and GIS read a blank, not 'nan'."""
    return "" if not np.isfinite(v) else fmt.format(float(v))


def to_csv(swath, path, fit: Optional[ScarpFit] = None):
    """Write the swath to CSV (columns CSV_COLUMNS; no data = empty cell).
    With a fit, its summary goes first as '#' comment lines. Returns path."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        if fit is not None:
            for line in fit.describe():
                f.write("# " + line + "\n")
            f.write(f"# Swath {swath.width_m:g} m wide, {swath.step_m:g} m steps, "
                    f"line {swath.length_m:.1f} m long\n")
        w = csv.writer(f)
        w.writerow(CSV_COLUMNS)
        for i in range(len(swath.distance)):
            w.writerow([_cell(swath.distance[i]), int(swath.n[i])]
                       + [_cell(swath.stat(k)[i]) for k in STATS])
    return path


# Colours: one blue ramp for the swath (light band = full range, mid =
# quartiles, dark = median), orange for the fits, grey chrome.
_C_RANGE, _C_IQR, _C_MEDIAN = "#cde2fb", "#86b6ef", "#184f95"
_C_FIT, _C_INK, _C_MUTED, _C_SURFACE = "#eb6834", "#0b0b0b", "#898781", "#fcfcfb"


def plot_png(swath, path, fit: Optional[ScarpFit] = None, title=""):
    """Swath plot as a PNG: shaded min-max and 25-75 % bands, median line and,
    with a fit, the far-field lines and the separation. Returns path, or None
    if matplotlib is not installed.

    Uses matplotlib's Figure and Agg canvas directly, not pyplot, so the
    caller's backend and open figures are left alone.
    """
    try:
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.figure import Figure
    except ImportError:
        return None

    d = swath.distance
    fig = Figure(figsize=(9.0, 4.8), dpi=150, facecolor=_C_SURFACE)
    FigureCanvasAgg(fig)
    ax = fig.add_subplot(1, 1, 1)
    ax.set_facecolor(_C_SURFACE)
    ax.fill_between(d, swath.min, swath.max, color=_C_RANGE, linewidth=0,
                    label="min to max")
    ax.fill_between(d, swath.p25, swath.p75, color=_C_IQR, linewidth=0,
                    label="25th to 75th percentile")
    ax.plot(d, swath.median, color=_C_MEDIAN, linewidth=1.5, label="median")

    if fit is not None:
        for f, rng in ((fit.upper, fit.upper_range), (fit.lower, fit.lower_range)):
            ax.plot(rng, f.at(rng), color=_C_FIT, linewidth=2.0, alpha=0.8)  # median shows through
            # Dashed extension from the range's inner end to the midpoint.
            inner = rng[1] if rng[1] <= fit.midpoint_m else rng[0]
            ax.plot([inner, fit.midpoint_m], f.at([inner, fit.midpoint_m]),
                    color=_C_FIT, linewidth=1.2, linestyle="--")
        ax.plot([], [], color=_C_FIT, linewidth=2.0, label=f"far-field fits ({fit.stat})")
        zu, zl = float(fit.upper.at(fit.midpoint_m)), float(fit.lower.at(fit.midpoint_m))
        ax.annotate("", xy=(fit.midpoint_m, zu), xytext=(fit.midpoint_m, zl),
                    arrowprops=dict(arrowstyle="<->", color=_C_INK, linewidth=1.0,
                                    shrinkA=0, shrinkB=0))
        ax.annotate(f"VS {fit.separation_m:.2f} ± {fit.separation_sigma_m:.2f} m",
                    xy=(fit.midpoint_m, 0.5 * (zu + zl)), xytext=(8, 0),
                    textcoords="offset points", va="center", ha="left",
                    color=_C_INK, fontsize=9)

    ax.set_xlabel("Distance along profile (m)", color=_C_INK)
    ax.set_ylabel("Height (m)", color=_C_INK)
    ax.grid(True, color=_C_MUTED, alpha=0.25, linewidth=0.6)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(_C_MUTED)
    ax.tick_params(colors=_C_MUTED, labelcolor=_C_INK, labelsize=8)
    ax.legend(loc="best", fontsize=8, frameon=False)
    if title:
        ax.set_title(title, color=_C_INK, fontsize=11, loc="left")
    note = (f"Swath {2 * swath.offsets[-1]:g} m wide ({len(swath.offsets)} samples), "
            f"{swath.step_m:g} m bins")
    if fit is not None:
        note += (f"; slopes {fit.upper_slope_deg:.1f}° / {fit.lower_slope_deg:.1f}°"
                 " far field")
        if not math.isnan(fit.steepest_slope_deg):
            note += f", steepest {fit.steepest_slope_deg:.1f}°"
    fig.text(0.01, 0.01, note, color=_C_MUTED, fontsize=7, ha="left", va="bottom")
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(path, facecolor=_C_SURFACE)
    return path
