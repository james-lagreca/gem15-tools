# SPDX-License-Identifier: GPL-2.0-or-later
"""gdalops.configure_remote limits its options to the bucket (G4), so other
rasters in the same process keep their side-car files."""
import pytest

gdal = pytest.importorskip("osgeo.gdal")
from gem15core import gdalops  # noqa: E402


def _ovr_count(path):
    ds = gdal.Open(str(path))               # keep ds alive while using its band (GDAL < 3.8)
    n = ds.GetRasterBand(1).GetOverviewCount()
    ds = None
    return n


@pytest.fixture
def local_tif_with_ovr(tmp_path):
    p = tmp_path / "local.tif"
    ds = gdal.GetDriverByName("GTiff").Create(str(p), 64, 64, 1, gdal.GDT_Float32)
    ds = None
    ds = gdal.Open(str(p))                  # read-only, so overviews go to local.tif.ovr
    ds.BuildOverviews("NEAREST", [2])
    ds = None
    assert (tmp_path / "local.tif.ovr").exists()
    return p


@pytest.mark.skipif(not hasattr(gdal, "SetPathSpecificOption"), reason="needs GDAL >= 3.6")
def test_scoped_leaves_local_sidecars_visible(local_tif_with_ovr):
    for k in ("GDAL_DISABLE_READDIR_ON_OPEN", "CPL_VSIL_CURL_ALLOWED_EXTENSIONS"):
        gdal.SetConfigOption(k, None)
    assert gdalops.configure_remote() == "scoped"
    assert gdal.GetConfigOption("GDAL_DISABLE_READDIR_ON_OPEN") is None
    assert gdal.GetConfigOption("CPL_VSIL_CURL_ALLOWED_EXTENSIONS") is None
    assert _ovr_count(local_tif_with_ovr) == 1


@pytest.mark.skipif(not hasattr(gdal, "SetPathSpecificOption"), reason="needs GDAL >= 3.6")
def test_scoped_options_apply_under_prefix(local_tif_with_ovr):
    """The same option set for a path prefix does hide that folder's side-cars,
    which is how it saves requests for the remote tiles."""
    prefix = str(local_tif_with_ovr.parent).replace("\\", "/")
    try:
        assert gdalops.configure_remote(prefix=prefix) == "scoped"
        assert _ovr_count(str(local_tif_with_ovr).replace("\\", "/")) == 0
    finally:
        gdal.SetPathSpecificOption(prefix, "GDAL_DISABLE_READDIR_ON_OPEN", None)


def test_global_mode_sets_process_wide():
    try:
        assert gdalops.configure_remote(scoped=False) == "global"
        assert gdal.GetConfigOption("GDAL_DISABLE_READDIR_ON_OPEN") == "EMPTY_DIR"
    finally:
        for k in gdalops.REMOTE_GDAL_CONFIG:
            gdal.SetConfigOption(k, None)
