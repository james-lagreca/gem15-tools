# SPDX-License-Identifier: GPL-2.0-or-later
"""Shared test set-up: core on sys.path, the live-test switch, and a synthetic
STAC catalogue with real GeoTIFF tiles (needs GDAL and numpy).

Synthetic tiles straddle the UTM 59S/60S boundary (174 E). Pixel values are an
analytic, steep function of lon/lat, so any georeferencing error, half-pixel
shift or wrong band shows up as a large residual.
"""
import json
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "core"))

K = 1e5  # m per degree in the synthetic surface (~18 m change per 15 m pixel)


def surface(lon, lat):
    return 500.0 + K * (lon - 174.0) + K * (lat + 41.75)


def pytest_collection_modifyitems(config, items):
    if os.environ.get("GEM15_LIVE") == "1":
        return
    skip = pytest.mark.skip(reason="reads the live bucket; set GEM15_LIVE=1 to run")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


def make_tile(path, epsg, lonlat_box, nbands, nan_west_of=None, offset=0.0, overviews=True):
    """GeoTIFF on a 15 m UTM grid covering a lon/lat box; band k = surface + 1000 k.
    Returns (shape, affine transform) as STAC would report them."""
    import numpy as np
    from osgeo import gdal, osr
    from gem15core import gdalops

    xmin, ymin, xmax, ymax = gdalops.aoi_bounds_in(f"EPSG:{epsg}", lonlat_box)
    nx, ny = int((xmax - xmin) / 15), int((ymax - ymin) / 15)
    xs = xmin + 15 * (np.arange(nx) + 0.5)
    ys = ymax - 15 * (np.arange(ny) + 0.5)
    X, Y = np.meshgrid(xs, ys)
    ct = osr.CoordinateTransformation(gdalops.srs(f"EPSG:{epsg}"), gdalops.srs("EPSG:4326"))
    ll = np.array(ct.TransformPoints(np.c_[X.ravel(), Y.ravel()]))
    lon, lat = ll[:, 0].reshape(X.shape), ll[:, 1].reshape(X.shape)
    base = surface(lon, lat) + offset
    if nan_west_of is not None:
        base = np.where(lon < nan_west_of, np.nan, base)
    ds = gdal.GetDriverByName("GTiff").Create(str(path), nx, ny, nbands, gdal.GDT_Float32,
                                              ["TILED=YES"])
    ds.SetGeoTransform((xmin, 15, 0, ymax, 0, -15))
    ds.SetProjection(gdalops.srs(f"EPSG:{epsg}").ExportToWkt())
    for k in range(nbands):
        b = ds.GetRasterBand(k + 1)
        b.SetNoDataValue(float("nan"))
        b.WriteArray((base + 1000 * k).astype("float32"))
    if overviews:
        ds.BuildOverviews("NEAREST", [2, 4])
    ds = None
    return [ny, nx], [15.0, 0.0, xmin, 0.0, -15.0, ymax]


# Synthetic cells: tile A (zone 59) runs east past 174 E, so it overlaps
# tile B (zone 60), which is NaN west of 174.0.
SYNTH = {
    "w173sm42": dict(epsg=32759, box=(173.80, -41.80, 174.02, -41.70), nan_west_of=None,
                     cell=(173, -42, 174, -41)),
    "w174sm42": dict(epsg=32760, box=(173.98, -41.80, 174.20, -41.70), nan_west_of=174.0,
                     cell=(174, -42, 175, -41)),
}


@pytest.fixture(scope="session")
def synthetic(tmp_path_factory):
    """Synthetic catalogue on disk. Returns dict(collection_url, root, tiles_dir)."""
    pytest.importorskip("osgeo")
    pytest.importorskip("numpy")
    from gem15core import DSM_BANDS

    root = tmp_path_factory.mktemp("gem15")
    tiles = root / "tiles"
    tiles.mkdir()
    stac = root / "stac"
    grp = stac / "m50_p170"
    grp.mkdir(parents=True)
    links = []
    for cell, spec in SYNTH.items():
        assets = {}
        for prod, names in (("dsm", DSM_BANDS), ("dtm", ("dtm",))):
            tif = tiles / f"{cell}_{prod}.tif"
            shape, transform = make_tile(tif, spec["epsg"], spec["box"], len(names),
                                         spec["nan_west_of"])
            assets[prod] = {"href": tif.as_uri(), "proj:epsg": spec["epsg"],
                            "proj:shape": shape, "proj:transform": transform,
                            "raster:bands": [{"name": n} for n in names]}
        (grp / cell).mkdir()
        item = {"id": f"gem-15_{cell}", "bbox": list(spec["cell"]),
                "properties": {"proj:epsg": spec["epsg"], "gem:cell_id": cell,
                               "gem:group_id": "m50_p170"},
                "assets": assets}
        (grp / cell / f"{cell}.json").write_text(json.dumps(item))
        links.append({"rel": "item", "href": f"./{cell}/{cell}.json"})
    (grp / "catalog.json").write_text(json.dumps({"links": links}))
    # m10_m100 has no file on purpose: if group pruning fails, fetching it raises.
    (stac / "collection.json").write_text(json.dumps({"links": [
        {"rel": "child", "href": "./m50_p170/catalog.json"},
        {"rel": "child", "href": "./m10_m100/catalog.json"}]}))
    return {"collection_url": (stac / "collection.json").as_uri(), "root": root,
            "tiles_dir": tiles}
