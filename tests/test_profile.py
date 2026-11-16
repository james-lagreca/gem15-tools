# SPDX-License-Identifier: GPL-2.0-or-later
"""Swath profiles and scarp separation (numpy only; synthetic rasters, no network).

The synthetic DEM is a regional plane plus a smooth step of height H across a
straight fault, H/2 * tanh(s / W), where s is the signed distance from the
fault: about 76 % of the step lies within +-W of it, so W = 30 m gives a scarp
roughly 60 m wide. Far from the fault the surface is the plane +- H/2, so the
far-field fits should meet the fault exactly H apart.
"""
import csv
import math
import sys
import warnings

import numpy as np
import pytest

from gem15core import profile

PX = 15.0
X0, Y0 = 300000.0, 6000000.0                # outer corner of the top-left pixel
GT = (X0, PX, 0.0, Y0, 0.0, -PX)
ROWS = COLS = 240                            # 3.6 km square
CX, CY = X0 + 1800.0, Y0 - 1800.0            # centre of the grid
H, W = 4.0, 30.0                             # step height and tanh half-width, m
HALF_LEN = 1200.0                            # profiles run 1200 m either side of the fault
UPPER, LOWER = (1350.0, 2350.0), (50.0, 1050.0)


def unit(dx, dy):
    n = math.hypot(dx, dy)
    return np.array([dx / n, dy / n])


def pixel_centres():
    xs = X0 + PX * (np.arange(COLS) + 0.5)
    ys = Y0 - PX * (np.arange(ROWS) + 0.5)
    return np.meshgrid(xs, ys)


def scarp_dem(normal, grad, noise=0.0, seed=1):
    """Plane with gradient `grad` (dz/dx, dz/dy) through 200 m at the centre,
    plus the tanh step, rising towards `normal`, plus N(0, noise) per pixel."""
    X, Y = pixel_centres()
    s = (X - CX) * normal[0] + (Y - CY) * normal[1]
    z = 200.0 + grad[0] * (X - CX) + grad[1] * (Y - CY) + H / 2 * np.tanh(s / W)
    if noise:
        z = z + np.random.default_rng(seed).normal(0.0, noise, z.shape)
    return z


def across(normal, shift=0.0):
    """Line across the fault through the centre, travelling towards `normal`
    (from the low side to the high side), moved `shift` m along strike so it
    does not sit on pixel centres. The fault is at 1200 m along it."""
    left = np.array([-normal[1], normal[0]])
    c = np.array([CX, CY]) + shift * left
    return [tuple(c - HALF_LEN * normal), tuple(c + HALF_LEN * normal)]


def truth(normal, grad, line, d):
    """Noise-free height on the line at distance d."""
    p0 = np.array(line[0])
    x, y = p0[0] + d * normal[0], p0[1] + d * normal[1]
    return (200.0 + grad[0] * (x - CX) + grad[1] * (y - CY)
            + H / 2 * np.tanh((d - HALF_LEN) / W))


EAST = unit(1, 0)
GRAD = (0.02, -0.01)                         # 1.1 deg rising east, 0.6 deg rising south
# Slope across strike only. With GRAD each east-west profile's band also
# slopes 1.5 m from edge to edge, which widens every bin (module note S5);
# tests that need the bins to be pure noise around one height use this.
GRAD_ACROSS = (0.02, 0.0)


# --------------------------------------------------------------------------
# Geometry and interpolation
# --------------------------------------------------------------------------
def plane(x, y):
    return 100.0 + 0.05 * (x - X0) + 0.02 * (y - Y0)


def test_plane_exact_and_positive_offsets_on_the_left():
    """Bilinear interpolation is exact on a plane, so every sample must equal
    the plane at the expected position: this checks the half-pixel convention,
    station spacing, the perpendicular direction and its sign (left = +) on an
    L-shaped line (east, then north)."""
    X, Y = pixel_centres()
    dem = plane(X, Y)
    a = (CX - 600.0 + 3.3, CY - 500.0 + 4.1)
    b = (a[0] + 607.0, a[1])                 # 607 m east (no station on the corner)
    c = (b[0], b[1] + 500.0)                 # then 500 m north
    sw = profile.sample_swath(dem, GT, [a, b, c], width_m=300.0, step_m=15.0)

    assert sw.length_m == pytest.approx(1107.0)
    assert len(sw.offsets) == 21 and sw.offsets[0] == -150.0 and sw.offsets[-1] == 150.0
    np.testing.assert_allclose(sw.distance, 15.0 * np.arange(74))   # 0 .. 1095 m
    for i, d in enumerate(sw.distance):
        if d < 607.0:       # travelling east: left is north
            px, py, lx, ly = a[0] + d, a[1], 0.0, 1.0
        else:               # travelling north: left is west
            px, py, lx, ly = b[0], b[1] + d - 607.0, -1.0, 0.0
        assert (sw.station_x[i], sw.station_y[i]) == pytest.approx((px, py), abs=1e-9)
        np.testing.assert_allclose(sw.samples[i], plane(px + lx * sw.offsets, py + ly * sw.offsets),
                                   rtol=0, atol=1e-9)
    assert np.all(sw.n == 21)
    # On a plane the median of a symmetrical band is the centre-line value.
    np.testing.assert_allclose(sw.median, sw.samples[:, 10], atol=1e-9)


def test_bilinear_nan_rule_and_edges():
    """A sample is NaN if any of its four neighbouring pixel centres is NaN or
    off the array (S2)."""
    a = np.ones((10, 10))
    a[5, 5] = np.nan
    centre = lambda r, c: (X0 + PX * (c + 0.5), Y0 - PX * (r + 0.5))    # noqa: E731
    pts = [centre(5, 5),                       # on the NaN pixel
           centre(5, 7),                       # well clear
           centre(5, 6),                       # neighbours (5..6, 6..7): clear
           (centre(5, 4)[0] + 0.1, centre(5, 4)[1]),   # touches (5, 5) with tiny weight
           (centre(5, 5)[0] + 7.5, centre(5, 5)[1]),   # halfway to (5, 6)
           centre(0, 0),                       # first pixel centre: fine
           (X0 + 3.0, Y0 - 50.0),              # in the outer half pixel: no left neighbour
           (centre(9, 9)[0] - 0.1, centre(9, 9)[1] + 0.1),  # just inside the last centre
           centre(9, 9),                       # on it: the +1 neighbour (zero weight) is off
           (centre(9, 9)[0] + 1.0, centre(9, 9)[1])]  # beyond the last centre
    x, y = np.array(pts).T
    z = profile.bilinear(a, GT, x, y)
    expect = [np.nan, 1, 1, np.nan, np.nan, 1, np.nan, 1, np.nan, np.nan]
    np.testing.assert_array_equal(np.isnan(z), np.isnan(expect))
    assert np.all(z[~np.isnan(z)] == 1.0)


# --------------------------------------------------------------------------
# Profiles across a synthetic scarp
# --------------------------------------------------------------------------
def test_median_profile_recovers_the_step():
    """With 0.5 m pixel noise the median of a 21-sample band follows the true
    profile far better than the centre line does.

    Tolerance: the median of 21 samples of N(0, s) has a standard error of
    about 1.25 s / sqrt(21) = 0.27 s, and bilinear interpolation leaves
    s between 0.25 and 0.5 m, so about 0.07 to 0.14 m; interpolating the tanh
    adds at most about 0.05 m near the fault. 0.2 m RMS is a safe bound.
    The surface is level along strike (GRAD_ACROSS): a slope along strike
    spreads each bin and leaves the median nearer a single sample (S5).
    """
    dem = scarp_dem(EAST, GRAD_ACROSS, noise=0.5)
    line = across(EAST, shift=4.1)
    sw = profile.sample_swath(dem, GT, line, 300.0, 15.0)
    true = truth(EAST, GRAD_ACROSS, line, sw.distance)
    rms_median = np.sqrt(np.mean((sw.median - true) ** 2))
    rms_centre = np.sqrt(np.mean((sw.samples[:, 10] - true) ** 2))
    assert rms_median < 0.2
    assert rms_median < 0.5 * rms_centre
    # The step itself: far-field medians differ by H plus the regional rise.
    g = GRAD_ACROSS[0]
    far_low = sw.median[sw.distance <= 900].mean() - (g * (450 - HALF_LEN) + 200)
    far_high = sw.median[sw.distance >= 1500].mean() - (g * (1950 - HALF_LEN) + 200)
    assert far_high - far_low == pytest.approx(H, abs=0.1)
    assert np.all(sw.p25 <= sw.median) and np.all(sw.median <= sw.p75)
    assert np.all(sw.min <= sw.p25) and np.all(sw.p75 <= sw.max)


def test_scarp_height_noise_free():
    dem = scarp_dem(EAST, GRAD)
    sw = profile.sample_swath(dem, GT, across(EAST, shift=4.1), 300.0, 15.0)
    fit = profile.scarp_height(sw, UPPER, LOWER)
    assert fit.midpoint_m == pytest.approx(HALF_LEN)
    # Only the tanh tails (< 2e-4 m beyond 150 m) separate the far fields from
    # the plane +- H/2, so the separation is exact to well under 1 cm.
    assert fit.separation_m == pytest.approx(H, abs=0.01)
    assert fit.separation_sigma_m < 0.01
    slope = math.degrees(math.atan(GRAD[0]))
    assert fit.upper_slope_deg == pytest.approx(slope, abs=0.01)
    assert fit.lower_slope_deg == pytest.approx(slope, abs=0.01)
    # Steepest 3-bin gradient: regional plus (H/2)(tanh(15/W) - tanh(-15/W)) / 30 m
    # at the fault. Bilinear interpolation smooths it slightly (about 4 % here).
    expected = GRAD[0] + H / 2 * 2 * math.tanh(15.0 / W) / 30.0
    got = math.tan(math.radians(fit.steepest_slope_deg))
    assert 0.9 * expected < got <= expected * 1.001
    assert fit.steepest_at_m == pytest.approx(HALF_LEN, abs=15.0)
    # Ranges in either order give the same fit.
    again = profile.scarp_height(sw, UPPER[::-1], LOWER[::-1])
    assert again.separation_m == fit.separation_m and again.upper_range == UPPER
    assert "Vertical separation" in fit.describe()[0]


def test_scarp_height_noisy():
    """Pixel noise 0.5 m, regional slope along strike too (GRAD).

    Expected scatter: median bins scatter 0.1 to 0.15 m (more than above,
    because of the slope along strike, S5); 67 bins per side over 1000 m,
    extrapolated 650 m from their centres, give 0.1 x sqrt(1/67 + 650^2 /
    (67 x 1000^2 / 12)) = 0.03 m and up per side. Over 300 seeds the error had
    a standard deviation of 0.095 m (largest 0.28 m) while the reported sigma
    averaged 0.07 m, which is why S6 calls sigma a lower bound. 0.3 m is about
    3 standard deviations; the seed is fixed, so the test is not flaky."""
    dem = scarp_dem(EAST, GRAD, noise=0.5, seed=7)
    sw = profile.sample_swath(dem, GT, across(EAST, shift=4.1), 300.0, 15.0)
    fit = profile.scarp_height(sw, UPPER, LOWER)
    assert fit.separation_m == pytest.approx(H, abs=0.3)
    assert 0.01 < fit.separation_sigma_m < 0.3
    assert fit.upper.n == fit.lower.n == 67
    # Profile run the other way: upper range now comes first, slope sign flips.
    back = profile.sample_swath(dem, GT, across(EAST, shift=4.1)[::-1], 300.0, 15.0)
    fit_back = profile.scarp_height(back, (50.0, 1050.0), (1350.0, 2350.0))
    assert fit_back.separation_m == pytest.approx(H, abs=0.3)
    assert fit_back.upper_slope_deg == pytest.approx(-fit.upper_slope_deg, abs=0.2)


def test_diagonal_line_samples_perpendicular():
    """Fault striking NE, profile running SE as a three-vertex line. With no
    regional slope along strike, every row of samples (parallel to the fault)
    should be nearly level; sampling along the line instead would spread each
    row over the whole scarp."""
    n = unit(1, -1)
    grad = tuple(0.02 * n)                   # regional slope across strike only
    dem = scarp_dem(n, grad)
    a, b = across(n, shift=6.0)
    mid = tuple(np.array(a) + 700.0 * n)     # extra vertex, not on a station
    sw = profile.sample_swath(dem, GT, [a, mid, b], 300.0, 15.0)
    assert sw.length_m == pytest.approx(2 * HALF_LEN)
    assert len(sw.distance) == 161
    spread = np.nanmax(sw.samples, axis=1) - np.nanmin(sw.samples, axis=1)
    assert spread.max() < 0.2                # interpolating the tanh: < ~0.1 m
    np.testing.assert_allclose(sw.median, truth(n, grad, [a, b], sw.distance), atol=0.1)
    fit = profile.scarp_height(sw, UPPER, LOWER)
    assert fit.separation_m == pytest.approx(H, abs=0.01)
    assert fit.upper_slope_deg == pytest.approx(math.degrees(math.atan(0.02)), abs=0.01)


def test_nan_patches_are_ignored():
    """Gaps drop samples, never bins' worth of made-up values. Level along
    strike, so dropping one side of the band leaves the median unchanged
    (with a slope along strike it would shift it: S5)."""
    X, Y = pixel_centres()
    dem = scarp_dem(EAST, GRAD_ACROSS)
    line = across(EAST, shift=4.1)            # along y = CY + 4.1, left = north
    # Part of the band in the lower range: x 300-400 m along, north half only.
    dem[(X > CX - 900) & (X < CX - 800) & (Y > CY + 30) & (Y < CY + 200)] = np.nan
    # The whole band in the upper range: 600-660 m east of the fault.
    dem[(X > CX + 600) & (X < CX + 660)] = np.nan
    with warnings.catch_warnings():
        warnings.simplefilter("error")       # no "All-NaN slice" warnings
        sw = profile.sample_swath(dem, GT, line, 300.0, 15.0)
    d = sw.distance
    partial = (d > 315) & (d < 385)
    assert np.all((sw.n[partial] > 0) & (sw.n[partial] < 21))
    empty = (d >= 1815) & (d <= 1845)
    assert np.all(sw.n[empty] == 0) and np.all(np.isnan(sw.median[empty]))
    assert np.all(np.isnan(sw.min[empty]) & np.isnan(sw.max[empty]) & np.isnan(sw.mean[empty]))
    assert np.all(np.isfinite(sw.median[sw.n > 0]))
    # Remaining samples still sit on the plane + step, so the fit is unchanged.
    fit = profile.scarp_height(sw, UPPER, LOWER)
    assert fit.separation_m == pytest.approx(H, abs=0.01)
    assert fit.upper.n < 67 and fit.lower.n == 67
    # A range with no data at all is refused in plain English.
    with pytest.raises(ValueError, match="bin\\(s\\) with data"):
        profile.scarp_height(sw, (1815.0, 1845.0), LOWER)


# --------------------------------------------------------------------------
# Windows
# --------------------------------------------------------------------------
def test_window_for_values_and_clipping():
    line = [(X0 + 300.0, Y0 - 600.0), (X0 + 900.0, Y0 - 600.0)]
    # x 150-1050 m -> cols 10-70, y 450-750 m down -> rows 30-50, +2 px pad.
    assert profile.window_for(line, 300.0, GT, COLS, ROWS) == (8, 28, 64, 24)
    assert profile.window_for(line, 300.0, GT, COLS, ROWS, pad_px=0) == (10, 30, 60, 20)
    # Near the top-left corner: clipped at 0.
    corner = [(X0 + 30.0, Y0 - 30.0), (X0 + 600.0, Y0 - 30.0)]
    assert profile.window_for(corner, 300.0, GT, COLS, ROWS) == (0, 0, 52, 14)
    # Near the bottom-right corner: clipped at the raster size.
    far = [(X0 + 3000.0, Y0 - 3570.0), (X0 + 3590.0, Y0 - 3570.0)]
    xoff, yoff, xsize, ysize = profile.window_for(far, 300.0, GT, COLS, ROWS)
    assert (xoff + xsize, yoff + ysize) == (COLS, ROWS)
    assert (xoff, yoff) == (190 - 2, 228 - 2)
    assert profile.window_geotransform(GT, (8, 28, 64, 24)) == (
        X0 + 120.0, PX, 0.0, Y0 - 420.0, 0.0, -PX)
    with pytest.raises(ValueError, match="does not overlap"):
        profile.window_for([(X0 - 9000, Y0), (X0 - 8000, Y0)], 300.0, GT, COLS, ROWS)


@pytest.mark.parametrize("line", [
    across(unit(1, -1), shift=6.0),                                   # inside
    [(X0 + 30.0, Y0 - 30.0), (X0 + 600.0, Y0 - 300.0)],               # off the top-left
    [(X0 + 3000.0, Y0 - 3570.0), (X0 + 3590.0, Y0 - 3000.0)],         # off the bottom-right
])
def test_window_sampling_equals_whole_array(line):
    X, Y = pixel_centres()
    dem = scarp_dem(EAST, GRAD, noise=0.5)
    dem[(X > CX + 100) & (X < CX + 160) & (Y > CY - 300)] = np.nan
    win = profile.window_for(line, 300.0, GT, COLS, ROWS)
    xoff, yoff, xsize, ysize = win
    assert xsize * ysize < COLS * ROWS
    sub = dem[yoff:yoff + ysize, xoff:xoff + xsize]
    part = profile.sample_swath(sub, profile.window_geotransform(GT, win), line, 300.0)
    whole = profile.sample_swath(dem, GT, line, 300.0)
    np.testing.assert_allclose(part.samples, whole.samples, rtol=0, atol=1e-9, equal_nan=True)
    np.testing.assert_array_equal(part.n, whole.n)


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------
def _swath_with_gap():
    X, _ = pixel_centres()
    dem = scarp_dem(EAST, GRAD, noise=0.5)
    dem[(X > CX + 600) & (X < CX + 660)] = np.nan
    sw = profile.sample_swath(dem, GT, across(EAST, shift=4.1), 300.0, 15.0)
    return sw, profile.scarp_height(sw, UPPER, LOWER)


def test_csv(tmp_path):
    sw, fit = _swath_with_gap()
    path = tmp_path / "swath.csv"
    assert profile.to_csv(sw, path, fit) == path
    lines = path.read_text(encoding="utf-8").splitlines()
    comments = [ln for ln in lines if ln.startswith("#")]
    assert lines[:len(comments)] == comments and len(comments) == len(fit.describe()) + 2
    assert "Vertical separation" in comments[0]
    assert "EGM96" in comments[-1] and "produced by Google" in comments[-1]
    rows = list(csv.reader(lines[len(comments):]))
    assert tuple(rows[0]) == profile.CSV_COLUMNS == (
        "distance_m", "n", "min_m", "p25_m", "median_m", "p75_m", "max_m", "mean_m")
    assert len(rows) - 1 == len(sw.distance)
    assert rows[1][:2] == ["0.000", "21"]
    assert float(rows[1][4]) == pytest.approx(sw.median[0], abs=5e-4)
    empty = [r for r in rows[1:] if r[1] == "0"]
    assert empty and all(cell == "" for r in empty for cell in r[2:])   # no data -> blank
    # Without a fit: only the provenance lines; with a source, it is named.
    plain = tmp_path / "plain.csv"
    profile.to_csv(sw, plain, source="dem.vrt (EPSG:32755)")
    head = plain.read_text(encoding="utf-8").splitlines()
    assert head[0].startswith("# Swath") and head[1] == "# DEM: dem.vrt (EPSG:32755)"
    assert head[3] == ",".join(profile.CSV_COLUMNS)


def test_png(tmp_path):
    pytest.importorskip("matplotlib")
    pyplot_loaded = "matplotlib.pyplot" in sys.modules
    sw, fit = _swath_with_gap()
    for name, f in (("fit.png", fit), ("nofit.png", None)):
        path = tmp_path / name
        assert profile.plot_png(sw, path, f, title="Synthetic 4 m scarp") == path
        assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    if not pyplot_loaded:                    # pyplot (and its backend) left alone
        assert "matplotlib.pyplot" not in sys.modules


def test_png_without_matplotlib(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "matplotlib.backends.backend_agg", None)
    sw, _ = _swath_with_gap()
    assert profile.plot_png(sw, tmp_path / "x.png") is None
    assert not (tmp_path / "x.png").exists()


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def clean_swath():
    return profile.sample_swath(scarp_dem(EAST, GRAD), GT, across(EAST, shift=4.1), 300.0, 15.0)


@pytest.mark.parametrize("upper, lower, match", [
    ((1000.0, 1400.0), (50.0, 1050.0), "overlap"),
    ((1050.0, 2350.0), (50.0, 1050.0), "overlap"),        # touching counts
    ((1350.0, 1370.0), LOWER, "2 bin"),                   # bins at 1350 and 1365 only
    ((3000.0, 3500.0), LOWER, "0 bin"),                   # off the end of the profile
    ((5.0, 5.0), LOWER, "two different"),
    ("near the top", LOWER, "two distances"),
])
def test_scarp_height_bad_ranges(clean_swath, upper, lower, match):
    with pytest.raises(ValueError, match=match):
        profile.scarp_height(clean_swath, upper, lower)


def test_scarp_height_bad_stat(clean_swath):
    with pytest.raises(ValueError, match="Statistic"):
        profile.scarp_height(clean_swath, UPPER, LOWER, stat="mode")
    assert profile.scarp_height(clean_swath, UPPER, LOWER, stat="p75").separation_m == \
        pytest.approx(H, abs=0.01)


@pytest.mark.parametrize("kwargs, match", [
    (dict(line_xy=[(0.0, 0.0), (100001.0, 0.0)]), "100 km"),
    (dict(width_m=5001.0), "width"),
    (dict(width_m=-1.0), "width"),
    (dict(step_m=0.0), "step"),
    (dict(line_xy=[(CX, CY)]), "two"),
    (dict(line_xy=[(CX, CY), (CX, CY)]), "less than one step"),
    (dict(line_xy=[(CX, CY), (CX + 10.0, CY)]), "less than one step"),
    (dict(geotransform=(X0, PX, 0.5, Y0, 0.0, -PX)), "rotated"),
    (dict(geotransform=(X0, PX, 0.0, Y0, 0.0, PX)), "north-up"),
    (dict(geotransform=(144.0, 0.000135, 0.0, -37.0, 0.0, -0.000135)), "degrees"),
    (dict(line_xy=[(CX, CY), (CX + 20000.0, CY)], step_m=1.0, width_m=5000.0), "samples"),
])
def test_sample_swath_guards(kwargs, match):
    args = dict(array=np.zeros((ROWS, COLS)), geotransform=GT,
                line_xy=across(EAST), width_m=300.0, step_m=15.0)
    args.update(kwargs)
    with pytest.raises(ValueError, match=match):
        profile.sample_swath(**args)


def test_zero_width_is_a_single_line_profile():
    dem = scarp_dem(EAST, GRAD)
    sw = profile.sample_swath(dem, GT, across(EAST, shift=4.1), width_m=0.0)
    assert sw.samples.shape == (161, 1) and np.all(sw.n == 1)
    np.testing.assert_array_equal(sw.median, sw.samples[:, 0])
