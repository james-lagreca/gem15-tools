"""GEM15Tools.pyt with the real arcpy against the live, public bucket.

Skipped unless GEM15_LIVE=1 and arcpy is present. Anonymous, read-only
requests only; a run downloads a few MB (small areas, a few COG blocks).
"""
import os

import pytest

arcpy = pytest.importorskip("arcpy")
np = pytest.importorskip("numpy")
gdal = pytest.importorskip("osgeo.gdal")

import test_arcpy_tools as offline  # noqa: E402
from gem15core import extent as gext  # noqa: E402
from gem15core import gdalops, vrt  # noqa: E402

pytestmark = [pytest.mark.live, pytest.mark.arcpy]
pyt, tb = offline.pyt, offline.tb          # the same module-scoped fixtures
lonlat_extent = offline.lonlat_extent

VIC = (144.10, -37.52, 144.14, -37.49)          # UTM 55S
NZ = (173.97, -41.75, 174.03, -41.72)           # across 174 E: UTM 59S and 60S
CENTRAL = (131.9, -25.1, 132.1, -24.9)          # UTM 52S and 53S
ZONE52 = (129.90, -25.60, 129.95, -25.55)       # UTM 52S only (failed in Pro, 2026-11-15)


@pytest.fixture(scope="module")
def acs(tb, tmp_path_factory):
    res = tb.ConnectToGEM15(str(tmp_path_factory.mktemp("acs")), "gem15.acs")
    path = res.getOutput(0)
    assert os.path.exists(path) and "Connection checked" in res.getMessages(0)
    return path


@pytest.fixture(scope="module")
def gdb(tmp_path_factory):
    folder = str(tmp_path_factory.mktemp("gdb"))
    return arcpy.management.CreateFileGDB(folder, "live.gdb").getOutput(0)


def count(md):
    return int(arcpy.management.GetCount(md).getOutput(0))


def test_connect_reuses_then_replaces(tb, acs):
    res = tb.ConnectToGEM15(os.path.dirname(acs), "gem15")       # ".acs" added
    assert res.getOutput(0) == acs and "Kept existing" in res.getMessages(0)
    res = tb.ConnectToGEM15(os.path.dirname(acs), "gem15.acs", True)
    assert "Created" in res.getMessages(0) and "Connection checked" in res.getMessages(0)


def test_mosaic_victoria_with_live_terrain(tb, pyt, acs, gdb):
    res = tb.AddGEM15ForExtent(lonlat_extent(VIC), "dtm", "Mosaic dataset", acs, gdb)
    md = res.getOutput(0)
    assert "automatic" in res.getMessages(0)
    assert os.path.basename(md) == "GEM15_dtm_UTM55S"                   # blank name: from CRS
    assert arcpy.Describe(md).spatialReference.factoryCode == 32755    # tiles' own zone
    n = count(md)
    assert n >= 1
    tb.AddGEM15ForExtent(lonlat_extent(VIC), "dtm", "Mosaic dataset", acs, gdb)
    assert count(md) == n                                               # duplicates skipped
    # The live derivatives read pixels from the cloud through the mosaic.
    x0, _, _, y1 = gdalops.aoi_bounds_in("EPSG:32755", VIC)
    ranges = {"hillshade": (0, 255), "slope": (0, 90), "lrm": (-200, 200)}
    for key, r in pyt.terrain_rasters(md, list(ranges)).items():
        a = np.squeeze(r.read(origin_coordinate=(x0 + 600, y1 - 600), ncols=32, nrows=32))
        a = a.astype(float)
        a[a > 1e30] = np.nan
        lo, hi = ranges[key]
        assert np.isfinite(a).mean() > 0.9 and lo <= np.nanmin(a) and np.nanmax(a) <= hi, key


def test_mosaic_across_zones_gets_nztm(tb, acs, gdb):
    res = tb.AddGEM15ForExtent(lonlat_extent(NZ), "dtm", "Mosaic dataset", acs, gdb,
                               "GEM15_nz")
    md = res.getOutput(0)
    assert arcpy.Describe(md).spatialReference.factoryCode == 2193
    assert count(md) >= 2                                   # tiles from both zones went in


def test_utm_mosaic_refuses_distant_zones(tb, acs, gdb):
    tb.AddGEM15ForExtent(lonlat_extent(VIC), "dtm", "Mosaic dataset", acs, gdb, "GEM15_vic")
    # The message names the mosaic a blank name would use (52S + 53S -> GA LCC).
    with pytest.raises(arcpy.ExecuteError, match=r"UTM zone 55[^\n]*'GEM15_dtm_GA_LCC'"):
        tb.AddGEM15ForExtent(lonlat_extent(CENTRAL), "dtm", "Mosaic dataset", acs, gdb,
                             "GEM15_vic")


def test_blank_name_gives_each_zone_its_own_mosaic(tb, acs, gdb):
    """What failed in Pro: with one fixed default name, a second area in a
    distant zone was refused. Blank names now follow the coordinate system."""
    vic = tb.AddGEM15ForExtent(lonlat_extent(VIC), "dtm", "Mosaic dataset", acs, gdb)
    z52 = tb.AddGEM15ForExtent(lonlat_extent(ZONE52), "dtm", "Mosaic dataset", acs, gdb)
    assert os.path.basename(vic.getOutput(0)) == "GEM15_dtm_UTM55S"
    assert os.path.basename(z52.getOutput(0)) == "GEM15_dtm_UTM52S"
    assert arcpy.Describe(z52.getOutput(0)).spatialReference.factoryCode == 32752
    assert count(z52.getOutput(0)) >= 1


def test_separate_layers_return_paths_headless(tb, acs):
    res = tb.AddGEM15ForExtent(lonlat_extent(VIC), "dtm", "Separate layers", acs)
    paths = res.getOutput(1).split(";")
    assert paths and all(p.endswith("mosaic.tif") and arcpy.Exists(p) for p in paths)


def read_window(path, bbox, epsg):
    """(arcpy array, GDAL array) of the part of a raster inside a lon/lat box.
    The VRTs cover whole tiles now, so read only the test area."""
    r = arcpy.Raster(path)
    x0, y0, x1, y1 = gdalops.aoi_bounds_in(f"EPSG:{epsg}", bbox)
    ext, cell = r.extent, r.meanCellWidth
    col, row = int((x0 - ext.XMin) / cell), int((ext.YMax - y1) / cell)
    nx, ny = int((x1 - x0) / cell), int((y1 - y0) / cell)
    a = arcpy.RasterToNumPyArray(r, arcpy.Point(ext.XMin + col * cell,
                                                ext.YMax - (row + ny) * cell),
                                 nx, ny, nodata_to_value=np.nan)
    # osgeo's GDAL reads the "/vsicurl?" form only without a process-wide
    # CPL_VSIL_CURL_ALLOWED_EXTENSIONS (paths P2); other tests may set it.
    old = gdal.GetConfigOption("CPL_VSIL_CURL_ALLOWED_EXTENSIONS")
    gdal.SetConfigOption("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", None)
    try:
        g = gdal.Open(path).ReadAsArray(col, row, nx, ny)
    finally:
        gdal.SetConfigOption("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", old)
    return a, g


def test_vrt_with_portable_sources(tb, tmp_path, monkeypatch):
    res = tb.AddGEM15ForExtent(lonlat_extent(VIC), "dtm", "VRT file", None, None, None,
                               None, str(tmp_path))
    path = res.getOutput(1)
    with open(path, encoding="utf-8") as fh:
        assert "/vsicurl?list_dir=no&amp;empty_dir=yes&amp;url=https%3A%2F%2F" in fh.read()
    a, g = read_window(path, VIC, 32755)
    assert np.isfinite(a).mean() > 0.9
    np.testing.assert_array_equal(a, g)


def test_vrt_across_zones(tb, tmp_path):
    """Warped to NZTM, each zone's warp over its own tiles (gdalops G5)."""
    res = tb.AddGEM15ForExtent(lonlat_extent(NZ), "dtm", "VRT file", None, None, None,
                               None, str(tmp_path))
    path = res.getOutput(1)
    assert arcpy.Describe(path).spatialReference.factoryCode == 2193
    a, g = read_window(path, NZ, 2193)
    assert np.isfinite(a).mean() > 0.9                       # not silently empty
    np.testing.assert_array_equal(a, g)


def test_current_view_tool_with_canopy_and_edges(tb, pyt, tmp_path):
    """The one-click tool, given an extent (headless there is no view): a VRT,
    live layers, the canopy check and the tile edges."""
    layers = ["Hillshade (multidirectional)", "Local relief model",
              pyt.CANOPY_LABEL, pyt.EDGES_LABEL]
    res = tb.GEM15ForCurrentView("DTM (bare earth)", layers, None, lonlat_extent(VIC),
                                 "VRT file")
    outs = res.getOutput(0).split(";")
    dtm, dsm = outs[0], outs[1]
    assert dtm.endswith("_epsg32755.vrt") and "gem15_dsm_" in dsm
    assert any(o.endswith(pyt.TILE_EDGES_FC) for o in outs)
    lyrx = [o for o in outs if o.endswith(".lyrx")]
    assert [os.path.basename(f).rsplit("_", 1)[-1] for f in lyrx] == \
        ["hillshade.lyrx", "lrm300m.lyrx", "canopy.lyrx"]
    # DSM and DTM VRTs share their grid, so the difference needs no resampling.
    rd, rs = arcpy.Raster(dtm), arcpy.Raster(dsm)
    assert (rd.extent.XMin, rd.extent.YMax, rd.width) == (rs.extent.XMin, rs.extent.YMax,
                                                          rs.width)
    x0, _, _, y1 = gdalops.aoi_bounds_in("EPSG:32755", VIC)
    corner = (x0 + 300, y1 - 300)
    canopy = arcpy.Raster(lyrx[2])                              # through the layer file
    c = np.squeeze(canopy.read(origin_coordinate=corner, ncols=40, nrows=40)).astype(float)
    if canopy.noDataValue is not None:
        c[c == canopy.noDataValue] = np.nan
    assert set(np.unique(c[np.isfinite(c)])) <= {1.0}           # marks or NoData, never 0
    # Layers built on a stand-in (A15) read the real source's pixels.
    hs = np.squeeze(arcpy.Raster(lyrx[0]).read(origin_coordinate=corner, ncols=32,
                                               nrows=32)).astype(float)
    ref = np.squeeze(pyt.terrain_rasters(dtm, ["hillshade"])["hillshade"].read(
        origin_coordinate=corner, ncols=32, nrows=32)).astype(float)
    assert np.nanstd(hs) > 1 and np.allclose(hs, ref, atol=1e-3)
    assert "machine learning" in res.getMessages(0)             # the caveat is shown


def test_cloud_mosaic_dem_draws(tb, pyt, acs, gdb, tmp_path):
    """The DEM layer of a cloud mosaic draws (it drew nothing in Pro once
    styled with setDefinition, 2026-11-16)."""
    md = tb.AddGEM15ForExtent(lonlat_extent(VIC), "dtm", "Mosaic dataset", acs,
                              gdb).getOutput(0)
    aprx, m = offline.blank_map(tmp_path)
    m.addDataFromPath(md)                                   # as Pro adds a tool's output
    pyt.add_dem_layer(m, md, "GEM-15 DTM")
    sr = arcpy.Describe(md).spatialReference
    x0, y0, x1, y1 = gdalops.aoi_bounds_in(f"EPSG:{sr.factoryCode}", VIC)
    view = arcpy.Extent(x0, y0, x1, y1, spatial_reference=sr)
    assert offline.drawn_contrast(aprx, m, view, tmp_path) > 20


def test_terrain_stack_on_cloud_mosaic_is_quick(tb, pyt, acs, gdb, tmp_path):
    """MakeRasterLayer on a cloud mosaic took ~180 s per layer; built on a
    stand-in (A15) the whole stack is far quicker, with the same pixels."""
    import time
    md = tb.AddGEM15ForExtent(lonlat_extent(VIC), "dtm", "Mosaic dataset", acs,
                              gdb).getOutput(0)
    t = time.perf_counter()
    files = tb.TerrainStack(md, ["Hillshade (multidirectional)", "Local relief model"], 300,
                            30, str(tmp_path)).getOutput(0).split(";")
    assert time.perf_counter() - t < 120
    x0, _, _, y1 = gdalops.aoi_bounds_in("EPSG:32755", VIC)
    corner = (x0 + 600, y1 - 600)
    lrm = np.squeeze(arcpy.Raster(files[1]).read(origin_coordinate=corner, ncols=32,
                                                 nrows=32)).astype(float)
    ref = np.squeeze(pyt.terrain_rasters(md, ["lrm"])["lrm"].read(
        origin_coordinate=corner, ncols=32, nrows=32)).astype(float)
    assert np.isfinite(lrm).mean() > 0.9 and np.allclose(lrm, ref, atol=1e-3)


def test_swath_profile_on_live_vrt(tb, tmp_path):
    vrt_path = tb.AddGEM15ForExtent(lonlat_extent(VIC), "dtm", "VRT file", None, None, None,
                                    None, str(tmp_path)).getOutput(1)
    x0, y0, x1, y1 = gdalops.aoi_bounds_in("EPSG:32755", VIC)
    gdb = arcpy.management.CreateFileGDB(str(tmp_path), "p.gdb").getOutput(0)
    fc = arcpy.management.CreateFeatureclass(gdb, "line", "POLYLINE",
                                             spatial_reference=arcpy.SpatialReference(32755)
                                             ).getOutput(0)
    with arcpy.da.InsertCursor(fc, ["SHAPE@"]) as cur:
        cur.insertRow([arcpy.Polyline(arcpy.Array([arcpy.Point(x0 + 500, y0 + 500),
                                                   arcpy.Point(x1 - 500, y1 - 500)]),
                                      arcpy.SpatialReference(32755))])
    res = tb.SwathProfile(fc, vrt_path, 300, str(tmp_path), "vic", 0, 600, 1500, 2100)
    csv = res.getOutput(0)
    rows = [ln for ln in open(csv, encoding="utf-8") if not ln.startswith("#")]
    assert len(rows) > 50 and rows[0].startswith("distance_m")
    assert "Vertical separation" in res.getMessages(0)


def test_clip_matches_streamed_pixels(tb, pyt, tmp_path):
    out = str(tmp_path / "vic.tif")
    res = tb.ClipForAnalysis(lonlat_extent(VIC), "dtm", None, out)
    assert "Download: about" in res.getMessages(0)
    ds = gdal.Open(out)
    gt, nx, ny = ds.GetGeoTransform(), ds.RasterXSize, ds.RasterYSize
    bounds = (gt[0], gt[3] + gt[5] * ny, gt[0] + gt[1] * nx, gt[3])
    tiles = pyt.find_cells(gext.pad(VIC, pyt.PAD_DEG), "dtm")
    gdalops.configure_remote()
    ref = vrt.write_vrt(tiles, "dtm", tmp_path / "ref.vrt", bounds=bounds)
    clip = ds.GetRasterBand(1).ReadAsArray()
    np.testing.assert_array_equal(clip, gdal.Open(ref.path).ReadAsArray())
    assert arcpy.Describe(out).spatialReference.factoryCode == 32755
