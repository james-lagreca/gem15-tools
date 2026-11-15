# SPDX-License-Identifier: GPL-2.0-or-later
"""
Terrain derivatives from a local DEM clip: multidirectional hillshade, slope
in degrees and a local relief model (LRM).

Methods (flagged):
 T1. Hillshade and slope use GDAL's gdaldem (DEMProcessing), Horn's method,
     with computeEdges. NaN voids stay nodata and their neighbours are
     computed from the cells that exist (checked on GDAL 3.6.4 and 3.9.2:
     a plane of known gradient gives the exact slope, voids give nodata).
     Hillshade: multidirectional, altitude from gem15core.terrain (30
     degrees). Output: Byte 1-255, nodata 0. Slope: Float32 degrees, -9999.
 T2. LRM = DEM - mean of the DEM over a circle of the given radius, centre
     included (gem15core.terrain.circle_offsets). NaN neighbours are left out
     of the mean, and at the raster's edge only cells inside it count, so
     voids do not grow (unlike the live LRM, lrm_vrt L4). Computed with row
     running sums, ~2r + 1 array operations per strip rather than one per
     kernel cell, in strips of rows so memory stays bounded.
 T3. The DEM must be in a projected CRS with metres in x, y and z. The LRM
     radius is converted to pixels with the raster's x pixel size.
"""

import math

import numpy as np
from osgeo import gdal

from . import gdalsafe
from .gem15core import Gem15Error, terrain

TIFF_OPTIONS = ["COMPRESS=DEFLATE", "TILED=YES", "BIGTIFF=IF_SAFER"]


def _progress_cb(progress, is_cancelled):
    """GDAL progress callback: reports 0..1 and returns 0 to stop when cancelled."""
    def cb(fraction, _msg, _data):
        if progress:
            progress(fraction)
        return 0 if (is_cancelled and is_cancelled()) else 1
    return cb


def _dem_processing(src, dst, mode, progress=None, is_cancelled=None, **kw):
    opts = gdal.DEMProcessingOptions(format="GTiff", computeEdges=True,
                                     creationOptions=TIFF_OPTIONS,
                                     callback=_progress_cb(progress, is_cancelled), **kw)
    gdal.ErrorReset()
    out = gdal.DEMProcessing(dst, src, mode, options=opts)
    if is_cancelled and is_cancelled():
        raise Gem15Error("Cancelled.")
    gdalsafe.check(out, f"gdaldem {mode}")
    out = None                                     # close, so the file is complete
    return dst


def hillshade(src, dst, altitude=terrain.HILLSHADE_ALTITUDE, z_factor=terrain.HILLSHADE_Z_FACTOR,
              progress=None, is_cancelled=None):
    """Multidirectional hillshade GeoTIFF (T1)."""
    return _dem_processing(src, dst, "hillshade", progress, is_cancelled,
                           multiDirectional=True, altitude=altitude, zFactor=z_factor)


def slope(src, dst, z_factor=1.0, progress=None, is_cancelled=None):
    """Slope in degrees GeoTIFF (T1)."""
    return _dem_processing(src, dst, "slope", progress, is_cancelled,
                           slopeFormat="degree", scale=1.0, zFactor=z_factor)


def run_lengths(r):
    """Half-width of the circle on each row offset -r..r: |dx| <= w(dy) keeps
    exactly terrain.circle_offsets' cells (hypot(dx, dy) <= r)."""
    return [int(math.floor(math.sqrt(max(0.0, (r + 1e-9) ** 2 - dy * dy)))) for dy in range(-r, r + 1)]


def circle_mean(block, r, rows_out=None):
    """Mean over a radius-r circle of every pixel in the middle rows of `block`,
    ignoring NaN (T2).

    block    : 2-D float array whose first and last r rows are context only
               (fill rows beyond the raster with NaN).
    Returns an array of rows_out = block rows - 2r rows (NaN where no cell counts).
    """
    nrows, ncols = block.shape
    rows_out = nrows - 2 * r if rows_out is None else rows_out
    valid = np.isfinite(block)
    # Zero-padded running sums along each row, with a leading zero column.
    vals = np.zeros((nrows, ncols + 2 * r + 1))
    cnts = np.zeros((nrows, ncols + 2 * r + 1))
    vals[:, r + 1:r + 1 + ncols] = np.where(valid, block, 0.0)
    cnts[:, r + 1:r + 1 + ncols] = valid
    np.cumsum(vals, axis=1, out=vals)
    np.cumsum(cnts, axis=1, out=cnts)
    total = np.zeros((rows_out, ncols))
    count = np.zeros((rows_out, ncols))
    for i, w in enumerate(run_lengths(r)):        # i = dy + r
        rows = slice(i, i + rows_out)
        hi = slice(r + w + 1, r + w + 1 + ncols)
        lo = slice(r - w, r - w + ncols)
        total += vals[rows, hi] - vals[rows, lo]
        count += cnts[rows, hi] - cnts[rows, lo]
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(count > 0, total / count, np.nan)


def lrm(src, dst, radius_m=terrain.LRM_RADIUS_M, strip_rows=None, progress=None,
        is_cancelled=None):
    """Local relief model GeoTIFF from a DEM file (T2, T3). Returns dst."""
    ds = gdalsafe.open_raster(src)
    band = ds.GetRasterBand(1)
    gt = ds.GetGeoTransform()
    r = terrain.radius_cells(radius_m, abs(gt[1]))
    cols, rows = ds.RasterXSize, ds.RasterYSize
    nodata = band.GetNoDataValue()
    strip_rows = strip_rows or max(64, min(2048, int(4e6 // max(cols, 1))))

    drv = gdal.GetDriverByName("GTiff")
    out = gdalsafe.check(drv.Create(dst, cols, rows, 1, gdal.GDT_Float32,
                                    TIFF_OPTIONS + ["PREDICTOR=3"]), f"Creating {dst}")
    out.SetGeoTransform(gt)
    out.SetProjection(ds.GetProjection())
    ob = out.GetRasterBand(1)
    ob.SetNoDataValue(float("nan"))
    ob.SetDescription(f"LRM, {radius_m:g} m radius (m)")

    def read(y0, y1):
        """Rows y0..y1 (may run past the raster: NaN there) as float64."""
        a = np.full((y1 - y0, cols), np.nan)
        lo, hi = max(0, y0), min(rows, y1)
        if hi > lo:
            chunk = band.ReadAsArray(0, lo, cols, hi - lo)
            if chunk is None:
                raise Gem15Error(f"Reading {src} failed: {gdal.GetLastErrorMsg()}")
            chunk = chunk.astype("float64")
            if nodata is not None and not math.isnan(nodata):
                chunk[chunk == nodata] = np.nan
            a[lo - y0:hi - y0] = chunk
        return a

    for y in range(0, rows, strip_rows):
        if is_cancelled and is_cancelled():
            out = None
            raise Gem15Error("Cancelled.")
        n = min(strip_rows, rows - y)
        block = read(y - r, y + n + r)
        mean = circle_mean(block, r, n)
        dem = block[r:r + n]
        ob.WriteArray((dem - mean).astype("float32"), 0, y)
        if progress:
            progress((y + n) / rows)
    ob.FlushCache()
    out = None
    ds = None
    return dst
