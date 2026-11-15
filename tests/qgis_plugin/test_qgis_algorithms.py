# SPDX-License-Identifier: GPL-2.0-or-later
"""The Processing algorithms, run through processing.run() on the synthetic
catalogue (two tiles either side of the UTM 59S/60S boundary; no network)."""
import pytest

np = pytest.importorskip("numpy")

ONE_ZONE = "173.85,173.95,-41.78,-41.72 [EPSG:4326]"
TWO_ZONES = "173.90,174.10,-41.78,-41.72 [EPSG:4326]"


def _open(path):
    from osgeo import gdal
    ds = gdal.Open(str(path))
    assert ds is not None, path
    return ds


def _epsg(ds):
    from osgeo import osr
    s = osr.SpatialReference(wkt=ds.GetProjection())
    s.AutoIdentifyEPSG()
    return s.GetAuthorityCode(None)


def test_build_vrt_native_with_live_lrm(processing, synthetic, tmp_path, gdal_exceptions_off):
    res = processing.run("gem15:buildvrt", {
        "EXTENT": ONE_ZONE, "LAYER": 0, "CRS_MODE": 0, "LIVE_LRM": True, "LRM_RADIUS": 150,
        "COLLECTION_URL": synthetic["collection_url"], "OUTPUT": str(tmp_path / "a.vrt")})
    assert res["OUTPUT"] == str(tmp_path / "a.vrt")
    assert len(res["OUTPUT_LAYERS"]) == 2 and res["OUTPUT_LAYERS"][1].endswith("_lrm_r150.vrt")
    assert _epsg(_open(res["OUTPUT"])) == "32759"
    lrm = _open(res["OUTPUT_LAYERS"][1])
    assert lrm.GetRasterBand(1).ReadAsArray(0, 0, 8, 8) is not None


def test_build_vrt_modes_across_zones(processing, synthetic, tmp_path, gdal_exceptions_off):
    url = synthetic["collection_url"]
    auto = processing.run("gem15:buildvrt", {"EXTENT": TWO_ZONES, "LAYER": 1, "CRS_MODE": 0,
                                             "COLLECTION_URL": url, "OUTPUT": str(tmp_path / "auto.vrt")})
    assert _epsg(_open(auto["OUTPUT"])) == "2193"                  # NZTM2000 in New Zealand
    per = processing.run("gem15:buildvrt", {"EXTENT": TWO_ZONES, "LAYER": 5, "CRS_MODE": 1,
                                            "COLLECTION_URL": url, "OUTPUT": str(tmp_path / "per.vrt")})
    assert sorted(_epsg(_open(p)) for p in per["OUTPUT_LAYERS"]) == ["32759", "32760"]
    tgt = processing.run("gem15:buildvrt", {"EXTENT": TWO_ZONES, "LAYER": 0, "CRS_MODE": 2,
                                            "TARGET_CRS": "EPSG:7855", "COLLECTION_URL": url,
                                            "OUTPUT": str(tmp_path / "tgt.vrt")})
    assert _epsg(_open(tgt["OUTPUT"])) == "7855"


def test_build_vrt_loads_styled_layers(processing, project, synthetic, tmp_path, gdal_exceptions_off):
    processing.runAndLoadResults("gem15:buildvrt", {
        "EXTENT": ONE_ZONE, "LAYER": 0, "CRS_MODE": 0, "LIVE_LRM": True,
        "COLLECTION_URL": synthetic["collection_url"], "OUTPUT": str(tmp_path / "b.vrt")})
    types = sorted(lyr.renderer().type() for lyr in project.mapLayers().values())
    assert types == ["hillshade", "singlebandpseudocolor"]
    for lyr in project.mapLayers().values():
        assert "This dataset is produced by Google" in lyr.metadata().rights()


def test_clip_matches_tile_pixels(processing, synthetic, tmp_path, gdal_exceptions_off):
    out = tmp_path / "clip.tif"
    res = processing.run("gem15:clip", {"EXTENT": ONE_ZONE, "LAYER": 0, "CRS_MODE": 0,
                                        "COLLECTION_URL": synthetic["collection_url"],
                                        "OUTPUT": str(out)})
    clip = _open(res["OUTPUT"])
    assert _epsg(clip) == "32759" and clip.GetDriver().ShortName == "GTiff"
    tile = _open(synthetic["tiles_dir"] / "w173sm42_dtm.tif")
    cg, tg = clip.GetGeoTransform(), tile.GetGeoTransform()
    col, row = int(round((cg[0] - tg[0]) / 15)), int(round((tg[3] - cg[3]) / 15))
    n = min(clip.RasterXSize, 50), min(clip.RasterYSize, 50)
    np.testing.assert_array_equal(clip.GetRasterBand(1).ReadAsArray(0, 0, *n),
                                  tile.GetRasterBand(1).ReadAsArray(col, row, *n))


def test_clip_stops_over_download_limit(processing, synthetic, tmp_path, gdal_exceptions_off):
    from qgis.core import QgsProcessingException
    with pytest.raises(QgsProcessingException, match="limit"):
        processing.run("gem15:clip", {"EXTENT": TWO_ZONES, "LAYER": 1, "MAX_DOWNLOAD_MB": 0.5,
                                      "COLLECTION_URL": synthetic["collection_url"],
                                      "OUTPUT": str(tmp_path / "big.tif")})
    assert not (tmp_path / "big.tif").exists()


def test_derivatives_from_clip(processing, synthetic, tmp_path, gdal_exceptions_off):
    # A native single-zone clip: a warped two-zone clip has a resampling seam at the
    # edge of a tile's valid data (up to ~8 m on this 18 m/px synthetic slope),
    # which is the warp's doing, not the derivatives'.
    clip = processing.run("gem15:clip", {"EXTENT": ONE_ZONE, "LAYER": 0,
                                         "COLLECTION_URL": synthetic["collection_url"],
                                         "OUTPUT": str(tmp_path / "c.tif")})["OUTPUT"]
    res = processing.run("gem15:derivatives", {
        "INPUT": clip, "LRM_RADIUS": 60, "OUTPUT_HILLSHADE": str(tmp_path / "h.tif"),
        "OUTPUT_SLOPE": str(tmp_path / "s.tif"), "OUTPUT_LRM": str(tmp_path / "l.tif")})
    hs, sl, lrm = (_open(res[k]) for k in ("OUTPUT_HILLSHADE", "OUTPUT_SLOPE", "OUTPUT_LRM"))
    assert hs.GetRasterBand(1).DataType == 1                         # Byte
    s = sl.GetRasterBand(1).ReadAsArray()
    s = s[s != sl.GetRasterBand(1).GetNoDataValue()]
    # The synthetic surface is ~a plane of about 1.2 and 0.9 m/m: slope ~56 degrees.
    assert 50 < np.median(s) < 62 and np.std(s) < 2
    a = lrm.GetRasterBand(1).ReadAsArray()
    dem_ds = _open(clip)                 # keep the dataset alive while using its band (GDAL < 3.8)
    dem = dem_ds.GetRasterBand(1).ReadAsArray()
    assert np.array_equal(np.isnan(a), np.isnan(dem)), "LRM voids must not grow (T2)"
    # Judge only pixels whose whole circle (r = 4 px) has data: next to voids and
    # edges the mean uses part of the circle, which on this steep plane biases it.
    # (The warped clip has small empty corners outside the requested lon/lat box.)
    r = 4
    bad = np.pad(np.isnan(dem), r, constant_values=True).astype(int)
    c = np.pad(bad.cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    k = 2 * r + 1
    near_void = (c[k:, k:] - c[:-k, k:] - c[k:, :-k] + c[:-k, :-k]) > 0
    assert (~near_void).sum() > 0.8 * dem.size
    assert np.max(np.abs(a[~near_void])) < 0.5                       # a plane has ~no local relief
    assert np.isnan(lrm.GetRasterBand(1).GetNoDataValue())


def test_derivatives_skips_outputs_and_refuses_degrees(processing, tmp_path, gdal_exceptions_off):
    from osgeo import gdal, osr
    from qgis.core import QgsProcessingException
    p = tmp_path / "geo.tif"
    ds = gdal.GetDriverByName("GTiff").Create(str(p), 10, 10, 1, gdal.GDT_Float32)
    ds.SetGeoTransform((144, 0.001, 0, -37, 0, -0.001))
    s = osr.SpatialReference()
    s.ImportFromEPSG(4326)
    ds.SetProjection(s.ExportToWkt())
    ds = None
    with pytest.raises(QgsProcessingException, match="geographic"):
        processing.run("gem15:derivatives", {"INPUT": str(p), "OUTPUT_HILLSHADE": str(tmp_path / "x.tif"),
                                             "OUTPUT_SLOPE": None, "OUTPUT_LRM": None})
