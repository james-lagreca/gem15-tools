# SPDX-License-Identifier: GPL-2.0-or-later
"""QGIS features added after the 2026-11-15 review: canopy check, tile edges,
single-sun hillshade, swath profile, dsm_std styling, no duplicate layers.
Synthetic catalogue, no network."""
import pytest

np = pytest.importorskip("numpy")

ONE_ZONE = "173.85,173.95,-41.78,-41.72 [EPSG:4326]"
AOI = (173.85, -41.78, 173.95, -41.72)


def test_canopy_vrt_is_dsm_minus_dtm(qgis_app, synthetic, tmp_path, gdal_exceptions_off):
    """DSM and DTM VRTs over the same bounds line up, so the difference VRT is
    exact: the synthetic DSM's first band equals the DTM, so it is 0."""
    from osgeo import gdal
    from gem15 import live
    from gem15.gem15core import stac
    plan = live.plan_layers(AOI, "canopy", str(tmp_path),
                            fetch=stac.Fetcher(cache_dir=None),
                            collection_url=synthetic["collection_url"])
    assert len(plan.layers) == 1 and plan.layers[0].style == "canopy"
    ds = gdal.Open(plan.layers[0].path)
    a = ds.GetRasterBand(1).ReadAsArray()
    valid = np.isfinite(a)
    assert valid.mean() > 0.5 and np.all(a[valid] == 0)
    from gem15 import layers
    lyr = layers.make_layer(plan.layers[0].path, "canopy", "canopy")
    assert lyr.renderer().type() == "singlebandpseudocolor"
    assert lyr.hasScaleBasedVisibility()


def test_tile_edges_gpkg(qgis_app, synthetic, tmp_path, gdal_exceptions_off):
    from gem15 import edges, layers
    from gem15.gem15core import stac
    tiles = stac.find_tiles((173.85, -41.78, 174.10, -41.72), "dtm",
                            collection_url=synthetic["collection_url"],
                            fetch=stac.Fetcher(cache_dir=None))
    path = edges.update_tile_edges(tiles, "dtm", str(tmp_path))
    edges.update_tile_edges(tiles, "dtm", str(tmp_path))         # again: no duplicates
    lyr = layers.make_edges_layer(path)
    feats = sorted((f["cell_id"], f["utm_zone"]) for f in lyr.getFeatures())
    assert feats == [("w173sm42", "UTM 59S"), ("w174sm42", "UTM 60S")]
    assert lyr.labelsEnabled()
    # Outlines are the tiles' true extents (here within their 1-degree cells).
    ext = lyr.extent()
    assert 173.7 < ext.xMinimum() < 173.9 and 174.1 < ext.xMaximum() < 174.3


def test_hillshade_across_a_fault_trend(processing, synthetic, tmp_path, gdal_exceptions_off):
    from osgeo import gdal
    from qgis.core import QgsProcessingException
    clip = processing.run("gem15:clip", {"EXTENT": ONE_ZONE, "LAYER": 0,
                                         "COLLECTION_URL": synthetic["collection_url"],
                                         "OUTPUT": str(tmp_path / "c.tif")})["OUTPUT"]
    md = processing.run("gem15:derivatives", {
        "INPUT": clip, "OUTPUT_HILLSHADE": str(tmp_path / "md.tif"), "OUTPUT_SLOPE": None,
        "OUTPUT_LRM": None})["OUTPUT_HILLSHADE"]
    one = processing.run("gem15:derivatives", {
        "INPUT": clip, "ILLUMINATION": 2, "FAULT_TREND": 0, "Z_FACTOR": 2,
        "OUTPUT_HILLSHADE": str(tmp_path / "one.tif"), "OUTPUT_SLOPE": None,
        "OUTPUT_LRM": None})["OUTPUT_HILLSHADE"]
    a = gdal.Open(md).ReadAsArray().astype(float)
    b = gdal.Open(one).ReadAsArray().astype(float)
    assert np.abs(a - b).mean() > 1                         # a different illumination
    with pytest.raises(QgsProcessingException, match="trend"):
        processing.run("gem15:derivatives", {
            "INPUT": clip, "ILLUMINATION": 2, "OUTPUT_HILLSHADE": str(tmp_path / "x.tif"),
            "OUTPUT_SLOPE": None, "OUTPUT_LRM": None})


def _line_layer(path, crs, pts):
    from qgis.core import (QgsFeature, QgsGeometry, QgsPointXY, QgsVectorFileWriter,
                           QgsVectorLayer)
    mem = QgsVectorLayer(f"LineString?crs={crs}", "line", "memory")
    f = QgsFeature()
    f.setGeometry(QgsGeometry.fromPolylineXY([QgsPointXY(x, y) for x, y in pts]))
    mem.dataProvider().addFeatures([f])
    opts = QgsVectorFileWriter.SaveVectorOptions()
    opts.driverName = "GPKG"
    QgsVectorFileWriter.writeAsVectorFormatV2(mem, str(path), mem.transformContext(), opts)
    return str(path)


def test_swath_profile_algorithm(processing, synthetic, tmp_path, gdal_exceptions_off):
    from qgis.core import QgsProcessingException
    clip = processing.run("gem15:clip", {"EXTENT": ONE_ZONE, "LAYER": 0,
                                         "COLLECTION_URL": synthetic["collection_url"],
                                         "OUTPUT": str(tmp_path / "c.tif")})["OUTPUT"]
    line = _line_layer(tmp_path / "line.gpkg", "EPSG:4326",
                       [(173.87, -41.76), (173.93, -41.74)])          # lon/lat: reprojected
    res = processing.run("gem15:swathprofile", {
        "LINE": line, "DEM": clip, "WIDTH": 150, "UPPER_FROM": 0, "UPPER_TO": 1500,
        "LOWER_FROM": 3500, "LOWER_TO": 5000, "OUTPUT_CSV": str(tmp_path / "p.csv"),
        "OUTPUT_PNG": str(tmp_path / "p.png")})
    text = open(res["OUTPUT_CSV"], encoding="utf-8").read()
    rows = [ln for ln in text.splitlines() if not ln.startswith("#")]
    assert rows[0].startswith("distance_m") and len(rows) > 200
    assert "Vertical separation" in text
    with pytest.raises(QgsProcessingException, match="all four"):
        processing.run("gem15:swathprofile", {
            "LINE": line, "DEM": clip, "UPPER_FROM": 0, "OUTPUT_CSV": str(tmp_path / "q.csv")})


def test_dsm_std_is_not_hillshaded(processing, project, synthetic, tmp_path, gdal_exceptions_off):
    processing.runAndLoadResults("gem15:buildvrt", {
        "EXTENT": ONE_ZONE, "LAYER": 5, "CRS_MODE": 0,
        "COLLECTION_URL": synthetic["collection_url"], "OUTPUT": str(tmp_path / "std.vrt")})
    types = [lyr.renderer().type() for lyr in project.mapLayers().values()]
    assert types == ["singlebandpseudocolor"]


def test_layers_not_added_twice(project, synthetic, tmp_path, gdal_exceptions_off):
    from gem15 import layers, live, plugin
    from gem15.gem15core import stac
    plan = live.plan_layers(AOI, "dtm", str(tmp_path), fetch=stac.Fetcher(cache_dir=None),
                            collection_url=synthetic["collection_url"])
    first = plugin.add_layers(plan, [layers.make_layer(s.path, s.name, s.style)
                                     for s in plan.layers])
    again = plugin.add_layers(plan, [layers.make_layer(s.path, s.name, s.style)
                                     for s in plan.layers])
    assert len(first) == 1 and again == [] and len(project.mapLayers()) == 1
