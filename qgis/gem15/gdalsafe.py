# SPDX-License-Identifier: GPL-2.0-or-later
"""
GDAL inside QGIS without changing GDAL for the rest of QGIS.

Two process-wide GDAL settings matter here (both checked 2026-11-15 on
GDAL 3.6.4, as in QGIS 3.28.6, and on GDAL 3.9.2):

 S1. gdal.UseExceptions() is one flag for the whole process and every
     thread, and QGIS's own Python code relies on it being off (DB Manager
     retries gdal.OpenEx(..., OF_UPDATE) when it returns None), as may other
     plugins. Neither gem15core.gdalops (its G6) nor this plugin ever turns
     it on: both check GDAL's results themselves (`check`), so background
     tasks cannot change how GDAL behaves for code on the main thread.
 S2. The remote-read options are applied only to paths in the GEM-15 bucket
     (gdalops.configure_remote, scoped), so the user's other rasters keep
     their .ovr / .aux.xml side-car files. QGIS's HTTP proxy, if set, is
     passed on for the bucket too (network N1).
"""

from osgeo import gdal

from . import network
from .gem15core import Gem15Error, gdalops

_configured = None


def configure_remote():
    """Apply the GEM-15 remote-read options once per QGIS session (S2).
    Returns "scoped" or "global" (GDAL < 3.5: only harmless options)."""
    global _configured
    if _configured is None:
        _configured = gdalops.configure_remote(scoped=True, proxy=network.qgis_proxy())
    return _configured


def check(result, what):
    """Raise Gem15Error with GDAL's last message if a GDAL call returned None (S1)."""
    if result is None:
        msg = gdal.GetLastErrorMsg() or "no message from GDAL"
        raise Gem15Error(f"{what} failed: {msg}")
    return result


def open_raster(path, update=False):
    """gdal.Open that raises Gem15Error instead of returning None."""
    gdal.ErrorReset()
    return check(gdal.Open(path, gdal.GA_Update if update else gdal.GA_ReadOnly),
                 f"Opening {path}")


def call(what, fn, *args, **kwargs):
    """Run a gdalops function with GDAL exceptions as the user has them (S1),
    turning any failure into a Gem15Error that names `what`."""
    gdal.ErrorReset()
    try:
        return fn(*args, **kwargs)
    except Gem15Error:
        raise
    except Exception as e:  # None results surface as AttributeError etc. when exceptions are off
        msg = gdal.GetLastErrorMsg()
        raise Gem15Error(f"{what} failed: {msg or e}") from e
