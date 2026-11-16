# SPDX-License-Identifier: GPL-2.0-or-later
"""Asset URL -> GDAL and .acs paths (pure Python)."""
import os
import pathlib

import pytest

from gem15core import Gem15Error, paths

B = "https://storage.googleapis.com/climate-and-sustainability/gem-15/"
URLS = [f"{B}m50_p170/w172sm42/dtm/zall/mosaic.tif",
        f"{B}m50_p170/w173sm42/dtm/zall/mosaic.tif"]


def test_gdal_path(tmp_path):
    assert paths.gdal_path(URLS[0]) == "/vsicurl/" + URLS[0]
    local = tmp_path / "a b.tif"
    assert pathlib.Path(paths.gdal_path(local.as_uri())) == local
    assert paths.gdal_path("/data/x.tif") == "/data/x.tif"


def test_portable_gdal_path(tmp_path):
    p = paths.portable_gdal_path(URLS[0])
    assert p.startswith("/vsicurl?list_dir=no&empty_dir=yes&url=https%3A%2F%2F")
    assert p.endswith("w172sm42%2Fdtm%2Fzall%2Fmosaic.tif")
    local = tmp_path / "a.tif"                  # local files are left as plain paths
    assert paths.portable_gdal_path(local.as_uri()) == paths.gdal_path(local.as_uri())


def test_cloud_paths_with_folder_in_connection():
    acs = os.path.join("C:" + os.sep, "proj", "gem15.acs")
    existing = {os.path.join(acs, "m50_p170", "w172sm42", "dtm", "zall", "mosaic.tif")}
    out, added = paths.cloud_paths(acs, URLS, lambda p: p in existing)
    assert not added
    assert out[0] == os.path.join(acs, "m50_p170", "w172sm42", "dtm", "zall", "mosaic.tif")


def test_cloud_paths_without_folder_in_connection():
    acs = os.path.join("C:" + os.sep, "proj", "gem15.acs")
    existing = {os.path.join(acs, "gem-15", "m50_p170", "w172sm42", "dtm", "zall", "mosaic.tif")}
    out, added = paths.cloud_paths(acs, URLS, lambda p: p in existing)
    assert added and out[1] == os.path.join(acs, "gem-15", "m50_p170", "w173sm42", "dtm",
                                            "zall", "mosaic.tif")


def test_cloud_paths_errors_and_empty():
    with pytest.raises(Gem15Error):
        paths.cloud_paths("x.acs", ["https://example.com/a.tif"], lambda p: True)
    assert paths.cloud_paths("x.acs", [], lambda p: True) == ([], False)
