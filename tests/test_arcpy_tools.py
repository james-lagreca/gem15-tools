"""GEM15Tools.pyt with the real arcpy (ArcGIS Pro), headless and offline.

Helpers are checked against numpy and GDAL on synthetic rasters, and the
tools themselves run through arcpy.ImportToolbox on the synthetic catalogue
(conftest), which GEM15_COLLECTION_URL points them at. Skipped without arcpy.
"""
import importlib.machinery
import importlib.util
import json
import math
import os
import re
import shutil

import pytest

arcpy = pytest.importorskip("arcpy")
np = pytest.importorskip("numpy")
gdal = pytest.importorskip("osgeo.gdal")

from conftest import ROOT  # noqa: E402
from gem15core import crs as gcrs  # noqa: E402
from gem15core import gdalops, paths, stac, terrain, vrt  # noqa: E402

pytestmark = pytest.mark.arcpy
PYT = ROOT / "arcgis" / "GEM15Tools.pyt"
AOI = (173.85, -41.78, 173.95, -41.72)          # inside synthetic tile A (UTM 59S) only
ACROSS = (173.97, -41.78, 174.03, -41.72)       # across 174 E: UTM 59S and 60S


@pytest.fixture(scope="module")
def pyt():
    """The .pyt as a module, with the real arcpy."""
    loader = importlib.machinery.SourceFileLoader("gem15pyt_real", str(PYT))
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("gem15pyt_real",
                                                                          loader))
    loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def tb():
    """The toolbox as ArcGIS loads it: tools callable as geoprocessing tools."""
    arcpy.env.overwriteOutput = True
    return arcpy.ImportToolbox(str(PYT))


@pytest.fixture
def catalogue(synthetic, monkeypatch):
    monkeypatch.setenv("GEM15_COLLECTION_URL", synthetic["collection_url"])
    return synthetic


@pytest.fixture(scope="module")
def dem_tif(tmp_path_factory):
    """200 x 160 px DEM at 15 m in UTM 55S: a tilted, wavy surface, a flat
    strip (columns 0-29) and a 10 x 10 px NoData hole."""
    from osgeo import osr
    path = tmp_path_factory.mktemp("dem") / "dem.tif"
    ny, nx = 160, 200
    yy, xx = np.mgrid[0:ny, 0:nx] * 15.0
    z = 200 + 0.08 * xx + 0.03 * yy + 25 * np.sin(xx / 120.0) * np.cos(yy / 90.0)
    z[:, :30] = 500.0
    z[100:110, 120:130] = np.nan
    ds = gdal.GetDriverByName("GTiff").Create(str(path), nx, ny, 1, gdal.GDT_Float32)
    ds.SetGeoTransform((300000, 15, 0, 6000000, 0, -15))
    s = osr.SpatialReference()
    s.ImportFromEPSG(32755)
    ds.SetProjection(s.ExportToWkt())
    b = ds.GetRasterBand(1)
    b.SetNoDataValue(float("nan"))
    b.WriteArray(z.astype("float32"))
    ds = None
    return str(path)


def as_array(r):
    """arcpy.Raster -> 2-D float array with NoData as NaN."""
    a = np.squeeze(r.read()).astype(float)
    nd = r.noDataValue
    if nd is not None and not (isinstance(nd, float) and math.isnan(nd)):
        a[a == nd] = np.nan
    return a


def read_dem(path):
    return gdal.Open(path).ReadAsArray().astype(float)


def circle_mean(z, radius_m=300, px=15):
    """Mean over the circle, NoData and outside ignored (the reference LRM)."""
    r = terrain.radius_cells(radius_m, px)
    ny, nx = z.shape
    pad = np.pad(z, r, constant_values=np.nan)
    acc, cnt = np.zeros_like(z), np.zeros_like(z)
    for dx, dy in terrain.circle_offsets(radius_m, px):
        w = pad[r + dy: r + dy + ny, r + dx: r + dx + nx]
        ok = np.isfinite(w)
        acc[ok] += w[ok]
        cnt += ok
    return acc / cnt, cnt


def horn_slope(z, px=15):
    p = np.pad(z, 1, constant_values=np.nan)
    a, b, c = p[:-2, :-2], p[:-2, 1:-1], p[:-2, 2:]
    d, f = p[1:-1, :-2], p[1:-1, 2:]
    g, h, i = p[2:, :-2], p[2:, 1:-1], p[2:, 2:]
    dzdx = ((c + 2 * f + i) - (a + 2 * d + g)) / (8 * px)
    dzdy = ((g + 2 * h + i) - (a + 2 * b + c)) / (8 * px)
    return np.degrees(np.arctan(np.hypot(dzdx, dzdy)))


# --------------------------------------------------------------------------
# Extents and coordinate systems
# --------------------------------------------------------------------------
def test_extent_around_a_pole(pyt):
    """A polar stereographic view containing the pole reaches it, at every
    longitude (it stopped at 81.7 N before, missing northern tiles)."""
    arctic = arcpy.SpatialReference(3995)
    w, s, e, n = pyt.extent_to_lonlat(arcpy.Extent(-900000, -900000, 900000, 900000,
                                                   spatial_reference=arctic))
    assert n == 90 and (w, e) == (-180, 180) and s < 82
    w, s, e, n = pyt.extent_to_lonlat(arcpy.Extent(1000000, -500000, 1500000, 0,
                                                   spatial_reference=arctic))
    assert n < 90                                               # beside the pole: unchanged


def test_sr_problem_and_limit(pyt):
    assert "degrees" in pyt.sr_problem(arcpy.SpatialReference(4326))
    assert "Foot" in pyt.sr_problem(arcpy.SpatialReference(2227))       # US survey feet
    assert pyt.sr_problem(arcpy.SpatialReference(7845)) is None
    assert pyt.sr_problem(None) is None
    assert pyt.clip_limit(None) == pyt.DEFAULT_MAX_MB and pyt.clip_limit(0) == float("inf")
    assert pyt.clip_limit(50) == 50


def test_extent_from_utm(pyt):
    ext = arcpy.Extent(300000, 5800000, 320000, 5815000,
                       spatial_reference=arcpy.SpatialReference(32755))
    w, s, e, n = pyt.extent_to_lonlat(ext)
    from osgeo import osr
    ct = osr.CoordinateTransformation(gdalops.srs("EPSG:32755"), gdalops.srs("EPSG:4326"))
    for x, y in ((300000, 5800000), (320000, 5815000), (300000, 5815000)):
        lon, lat = ct.TransformPoint(x, y)[:2]
        assert w < lon < e and s < lat < n
    assert w < e and e - w < 0.3


def test_extent_across_antimeridian(pyt):
    """Fiji Map Grid (EPSG 3460), centred on 178.75 E, from about 177 E to 179 W."""
    ext = arcpy.Extent(1820000, 3900000, 2240000, 4050000,
                       spatial_reference=arcpy.SpatialReference(3460))
    w, s, e, n = pyt.extent_to_lonlat(ext)
    assert w > e                                          # crosses 180, not the whole globe
    assert 176.5 < w < 177.5 and -179.5 < e < -178.5 and -18.5 < s < n < -16.5


def test_sr_for_custom_choice_makes_a_mosaic(pyt, tmp_path):
    """A custom conformal CRS (multi-zone, outside AU/NZ) loads in arcpy, matches
    the PROJ definition, and a mosaic dataset can be created in it."""
    bbox = (-109.5, 35.0, -101.5, 37.0)                   # UTM 12N-14N, New Mexico
    choice = gcrs.auto_crs(bbox, [32612, 32613, 32614])
    assert choice.kind == "custom"
    sr = pyt.sr_for_choice(choice)
    assert sr.type == "Projected" and sr.linearUnitName == "Meter"
    gdal_def = pyt.sr_to_gdal(sr)
    assert gdal_def.startswith("PROJCS[")                 # no EPSG code: Esri WKT
    from osgeo import osr
    a = osr.CoordinateTransformation(gdalops.srs("EPSG:4326"), gdalops.srs(gdal_def))
    b = osr.CoordinateTransformation(gdalops.srs("EPSG:4326"),
                                     gdalops.srs(choice.projection.proj4()))
    for lon, lat in ((-109, 35.5), (-105.5, 36), (-102, 36.8)):
        pa, pb = a.TransformPoint(lon, lat)[:2], b.TransformPoint(lon, lat)[:2]
        assert math.dist(pa, pb) < 0.01
    gdb = arcpy.management.CreateFileGDB(str(tmp_path), "t.gdb").getOutput(0)
    name = pyt.auto_mosaic_name("dtm", sr)               # also a valid geodatabase name
    assert re.fullmatch(r"GEM15_dtm_(TM|LCC)_\d+(_\d+)?[EW]", name), name
    md = arcpy.management.CreateMosaicDataset(gdb, name, sr, 1, "32_BIT_FLOAT").getOutput(0)
    assert arcpy.Describe(md).spatialReference.name == sr.name


def test_auto_mosaic_names_from_real_coordinate_systems(pyt):
    names = {code: pyt.auto_mosaic_name("dtm", arcpy.SpatialReference(code))
             for code in (32755, 32652, 7845, 2193, 7855)}
    assert names == {32755: "GEM15_dtm_UTM55S", 32652: "GEM15_dtm_UTM52N",
                     7845: "GEM15_dtm_GA_LCC", 2193: "GEM15_dtm_NZTM",
                     7855: "GEM15_dtm_GDA2020_MGA_Zone_55"}
    assert pyt.sr_utm_zone(arcpy.SpatialReference(7855)) == 55      # MGA zone = UTM zone


def test_sr_to_gdal_codes(pyt):
    assert pyt.sr_to_gdal(arcpy.SpatialReference(7845)) == "EPSG:7845"
    esri_only = pyt.sr_to_gdal(arcpy.SpatialReference(54009))     # World Mollweide
    assert esri_only.startswith("PROJCS[") and ";" not in esri_only
    gdalops.srs(esri_only)                                         # GDAL can read it


# --------------------------------------------------------------------------
# Terrain derivatives
# --------------------------------------------------------------------------
def test_slope_matches_horn(pyt, dem_tif):
    s = as_array(pyt.terrain_rasters(dem_tif, ["slope"])["slope"])
    ref = horn_slope(read_dem(dem_tif))
    ok = np.isfinite(s) & np.isfinite(ref)
    assert ok.sum() > 0.9 * ref.size
    np.testing.assert_allclose(s[ok], ref[ok], atol=1e-3)


@pytest.mark.parametrize("method, tol", [("focal", 1e-3), ("convolution", 1e-6)])
def test_lrm_matches_circle_mean(pyt, dem_tif, method, tol):
    z = read_dem(dem_tif)
    lrm = as_array(pyt.terrain_rasters(dem_tif, ["lrm"], 300, method=method)["lrm"])
    mean, cnt = circle_mean(z)
    ref = z - mean
    assert np.array_equal(np.isnan(lrm), np.isnan(z))     # NoData only where the DEM has none
    if method == "focal":
        ok = np.isfinite(ref)                             # NoData and edges ignored, as Esri does
    else:                                                 # A10: full circles only
        r = terrain.radius_cells(300)
        ok = cnt == len(terrain.circle_offsets(300))
        ok[:r], ok[-r:], ok[:, :r], ok[:, -r:] = False, False, False, False
    assert ok.sum() > 1000
    np.testing.assert_allclose(lrm[ok], ref[ok], atol=tol)


def test_multidirectional_hillshade(pyt, dem_tif):
    dem = arcpy.Raster(dem_tif)
    md = as_array(pyt.terrain_rasters(dem_tif, ["hillshade"], altitude=30)["hillshade"])
    h = {az: as_array(pyt.hillshade(dem, az, 30)) for az in pyt.MD_AZIMUTHS}
    a = np.radians(as_array(arcpy.ia.Aspect(dem)))
    ref = 0.5 * sum(np.cos(a - np.radians(az)) ** 2 * h[az] for az in pyt.MD_AZIMUTHS)
    ok = np.isfinite(md) & np.isfinite(ref)
    # Aspect read on its own comes back in whole degrees (S16); inside the
    # chain it stays float, hence up to ~0.5 grey level of difference.
    np.testing.assert_allclose(md[ok], ref[ok], atol=0.5)
    # Flat ground: as one sun at 30 degrees (255 sin 30, rounded by Esri's U8 hillshade).
    assert md[50, 10] == pytest.approx(255 * math.sin(math.radians(30)), abs=1)
    # Unlike Pro's own multidirectional hillshade (A12), the altitude matters.
    md60 = as_array(pyt.terrain_rasters(dem_tif, ["hillshade"], altitude=60)["hillshade"])
    assert md60[50, 10] == pytest.approx(255 * math.sin(math.radians(60)), abs=1)


def test_layer_files_and_map(pyt, dem_tif, tmp_path):
    files = pyt.terrain_layer_files(dem_tif, "dem", ["lrm", "hillshade", "slope"],
                                    str(tmp_path / "layers"), 250, 30)
    assert [os.path.basename(f) for f in files] == ["dem_hillshade.lyrx", "dem_slope.lyrx",
                                                    "dem_lrm250m.lyrx"]
    lrm = json.load(open(files[2], encoding="utf-8"))["layerDefinitions"][0]
    assert lrm["name"] == "dem LRM (250 m)" and lrm["minScale"] == pyt.LIVE_LRM_MIN_SCALE
    assert "FunctionRasterDatasetName" in lrm["dataConnection"]["dataset"]   # live, not pixels
    for f in files:                                       # Pro parses the styled CIM
        layer = arcpy.mp.LayerFile(f).listLayers()[0]
        col = layer.getDefinition("V3").colorizer
        assert col.statsType == "AreaOfView"              # DRA
    col = arcpy.mp.LayerFile(files[2]).listLayers()[0].getDefinition("V3").colorizer
    assert type(col.colorRamp).__name__ == "CIMMultipartColorRamp"
    assert col.stretchType == "StandardDeviations"
    # Into a map, in a project copied from Pro's blank template.
    blank = os.path.join(arcpy.GetInstallInfo()["InstallDir"],
                         r"Resources\ArcToolBox\Services\routingservices\data\Blank.aprx")
    if not os.path.exists(blank):
        pytest.skip("Pro's Blank.aprx template not found")
    shutil.copyfile(blank, tmp_path / "t.aprx")
    aprx = arcpy.mp.ArcGISProject(str(tmp_path / "t.aprx"))
    m = aprx.createMap("GEM", "MAP")
    group = pyt.add_layer_files(m, files, "GEM-15 terrain: dem")
    names = [lyr.name for lyr in group.listLayers()]
    assert names == ["dem LRM (250 m)", "dem slope (degrees)",
                     "dem hillshade (multidirectional, sun 30 deg)"]     # LRM on top
    assert not group.listLayers()[1].visible                             # slope off
    lyr = pyt.add_rasters_to_map(m, [dem_tif], ["GEM-15 dtm"])[0]
    assert lyr.name == "GEM-15 dtm"
    assert lyr.getDefinition("V3").colorizer.statsType == "AreaOfView"


def test_save_derivatives(pyt, dem_tif, tmp_path):
    dem = str(tmp_path / "dem.tif")
    shutil.copyfile(dem_tif, dem)
    outs = pyt.save_derivatives(dem, ["hillshade", "lrm"], 300, 30)
    assert [os.path.basename(f) for f in outs] == ["dem_hillshade.tif", "dem_lrm300m.tif"]
    live = as_array(pyt.terrain_rasters(dem, ["lrm"], 300)["lrm"])
    saved = read_dem(outs[1])
    saved[saved < -1e30] = np.nan
    ok = np.isfinite(live)
    np.testing.assert_allclose(saved[ok], live[ok], atol=1e-3)            # same as live
    assert arcpy.Describe(outs[0]).spatialReference.factoryCode == 32755


# --------------------------------------------------------------------------
# The tools, run as geoprocessing tools
# --------------------------------------------------------------------------
def lonlat_extent(bbox):
    return arcpy.Extent(*bbox, spatial_reference=arcpy.SpatialReference(4326))


def test_clip_tool_native_grid(tb, catalogue, tmp_path):
    out = str(tmp_path / "clip.tif")
    res = tb.ClipForAnalysis(lonlat_extent(AOI), "dtm", None, out, None,
                             ["Hillshade (multidirectional)", "Local relief model"],
                             300, 30, 1000)
    assert "Download: about" in res.getMessages(0)        # the estimate, as information
    ds = gdal.Open(out)
    gt = ds.GetGeoTransform()
    assert gt[1] == 15 and gt[5] == -15 and gt[0] % 15 == 0 and gt[3] % 15 == 0
    assert arcpy.Describe(out).spatialReference.factoryCode == 32759     # as Pro sees it
    # Pixel for pixel the same as the source tile on its native grid.
    src = gdal.Open(str(catalogue["tiles_dir"] / "w173sm42_dtm.tif"))
    sgt = src.GetGeoTransform()
    col, row = int(round((gt[0] - sgt[0]) / 15)), int(round((sgt[3] - gt[3]) / 15))
    clip = ds.GetRasterBand(1).ReadAsArray()
    np.testing.assert_array_equal(
        clip, src.GetRasterBand(1).ReadAsArray(col, row, ds.RasterXSize, ds.RasterYSize))
    assert ds.GetMetadataItem("ATTRIBUTION") == "This dataset is produced by Google"
    outs = res.getOutput(1).split(";")
    assert [os.path.basename(f) for f in outs] == ["clip_hillshade.tif", "clip_lrm300m.tif"]


def test_clip_tool_across_zones_and_dsm_band(tb, catalogue, tmp_path):
    out = str(tmp_path / "nz.tif")
    tb.ClipForAnalysis(lonlat_extent(ACROSS), "dsm", "dsm_std", out)
    ds = gdal.Open(out)
    assert arcpy.Describe(out).spatialReference.factoryCode == 2193      # automatic, NZ
    a = ds.GetRasterBand(1).ReadAsArray()
    assert np.isfinite(a).mean() > 0.95
    # dsm_std is band 5 = surface + 4000 in the synthetic tiles.
    assert 3000 < np.nanmedian(a) - 0 < 30000


def test_clip_tool_stops_over_the_limit(tb, catalogue, tmp_path):
    out = str(tmp_path / "big.tif")
    with pytest.raises(arcpy.ExecuteError, match="limit"):
        tb.ClipForAnalysis(lonlat_extent(AOI), "dtm", None, out, None, None, 300, 30, 0.01)
    assert not os.path.exists(out)


def test_vrt_mode(tb, catalogue, tmp_path):
    res = tb.AddGEM15ForExtent(lonlat_extent(AOI), "dtm", "VRT file", None, None, None,
                               None, str(tmp_path))
    path = res.getOutput(1)
    assert path.endswith(".vrt") and os.path.exists(path)
    r = arcpy.Raster(path)
    assert r.spatialReference.factoryCode == 32759
    a = arcpy.RasterToNumPyArray(r, nodata_to_value=np.nan)
    g = gdal.Open(path).ReadAsArray()
    np.testing.assert_array_equal(a, g)


def test_vrt_mode_across_zones(tb, catalogue, tmp_path):
    res = tb.AddGEM15ForExtent(lonlat_extent(ACROSS), "dtm", "VRT file", None, None, None,
                               None, str(tmp_path))
    r = arcpy.Raster(res.getOutput(1))
    assert r.spatialReference.factoryCode == 2193
    a = arcpy.RasterToNumPyArray(r, nodata_to_value=np.nan)
    np.testing.assert_array_equal(a, gdal.Open(res.getOutput(1)).ReadAsArray())
    assert np.isfinite(a).mean() > 0.95


def test_terrain_tool(tb, dem_tif, tmp_path):
    res = tb.TerrainStack(dem_tif, ["Slope (degrees)", "Local relief model"], 300, 30,
                          str(tmp_path))
    files = res.getOutput(0).split(";")
    assert [os.path.basename(f) for f in files] == ["dem_slope.lyrx", "dem_lrm300m.lyrx"]
    assert all(os.path.exists(f) for f in files)


def test_mosaic_mode_needs_a_connection(tb, catalogue, tmp_path):
    with pytest.raises(arcpy.ExecuteError):
        tb.AddGEM15ForExtent(lonlat_extent(AOI), "dtm", "Mosaic dataset", None,
                             str(tmp_path), "md")


def test_portable_vrt_sources_open_in_gdal(tmp_path, synthetic):
    """The /vsicurl? form is only used for https; local tiles stay plain paths,
    so the VRT built with it still opens (the https form is checked live)."""
    tiles = stac.find_tiles(AOI, "dtm", collection_url=synthetic["collection_url"],
                            fetch=stac.Fetcher(cache_dir=None))
    info = vrt.write_vrt(tiles, "dtm", tmp_path / "p.vrt",
                         bounds=gdalops.aoi_bounds_in("EPSG:32759", AOI),
                         path_for=paths.portable_gdal_path)
    assert np.isfinite(gdal.Open(info.path).ReadAsArray()).all()
