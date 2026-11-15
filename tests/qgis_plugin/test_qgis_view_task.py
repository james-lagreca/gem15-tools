# SPDX-License-Identifier: GPL-2.0-or-later
"""Canvas extent -> lon/lat, styled layers, and the "for current view" task
run through QGIS's task manager (synthetic catalogue, no network)."""
import pytest


def _crs(code):
    from qgis.core import QgsCoordinateReferenceSystem
    c = QgsCoordinateReferenceSystem(code)
    assert c.isValid(), code
    return c


def _rect(*v):
    from qgis.core import QgsRectangle
    return QgsRectangle(*v)


def test_lonlat_from_web_mercator(qgis_app):
    from gem15 import view
    from qgis.core import QgsCoordinateTransform, QgsPointXY, QgsProject
    ct = QgsCoordinateTransform(_crs("EPSG:4326"), _crs("EPSG:3857"), QgsProject.instance())
    a, b = ct.transform(QgsPointXY(144.0, -37.6)), ct.transform(QgsPointXY(144.3, -37.4))
    w, s, e, n = view.lonlat_bbox(_rect(a.x(), a.y(), b.x(), b.y()), _crs("EPSG:3857"))
    assert (w, s, e, n) == pytest.approx((144.0, -37.6, 144.3, -37.4), abs=1e-9)


def test_lonlat_from_utm_covers_curved_edges(qgis_app):
    """A UTM rectangle's lon/lat outline bulges: the box must hold every edge point."""
    from gem15 import view
    from gem15.gem15core import extent
    from qgis.core import QgsCoordinateTransform, QgsPointXY, QgsProject
    utm = _crs("EPSG:32755")
    w, s, e, n = view.lonlat_bbox(_rect(200000, 5800000, 800000, 6000000), utm)
    ct = QgsCoordinateTransform(utm, _crs("EPSG:4326"), QgsProject.instance())
    dense = [ct.transform(QgsPointXY(x, y))
             for x, y in extent.densified_ring(200000, 5800000, 800000, 6000000, 401)]
    corners = dense[0::400]
    tol = 1e-3                                     # 21 points a side vs 401: ~100 m
    assert all(w - tol <= p.x() <= e + tol and s - tol <= p.y() <= n + tol for p in dense)
    # An edge bows beyond its corners, and the box still holds it:
    cs, cn = min(p.y() for p in corners), max(p.y() for p in corners)
    assert s < cs - 0.005 or n > cn + 0.005


def test_lonlat_across_antimeridian(qgis_app):
    """A view over Fiji in a Pacific-centred CRS gives W > E, not a world-wide box."""
    from gem15 import view
    from qgis.core import QgsCoordinateTransform, QgsPointXY, QgsProject
    pdc = _crs("EPSG:3832")                      # WGS 84 / PDC Mercator, centred on 150 E
    ct = QgsCoordinateTransform(_crs("EPSG:4326"), pdc, QgsProject.instance())
    a, b = ct.transform(QgsPointXY(177.0, -19.0)), ct.transform(QgsPointXY(-178.0, -16.0))
    bbox = view.lonlat_bbox(_rect(a.x(), a.y(), b.x(), b.y()), pdc)
    assert bbox == pytest.approx((177.0, -19.0, -178.0, -16.0), abs=1e-6)
    assert view.cell_count(bbox) == 5 * 3


def test_cell_count():
    from gem15 import view
    assert view.cell_count((144.0, -37.6, 144.3, -37.4)) == 1
    assert view.cell_count((143.5, -38.5, 145.5, -36.5)) == 9
    assert view.cell_count((144.0, -38.0, 150.0, -32.0)) == 36


def test_make_layer_styles(qgis_app, synthetic, tmp_path, gdal_exceptions_off):
    from gem15 import layers, live, settings
    plan = live.plan_layers((173.85, -41.78, 173.95, -41.72), "lrm", str(tmp_path),
                            collection_url=synthetic["collection_url"])
    base = plan.layers[0].path.split("_lrm_")[0] + ".vrt"
    hill = layers.make_layer(base, "hill", "hillshade")
    r = hill.renderer()
    assert r.type() == "hillshade" and r.multiDirectional() and r.altitude() == 30.0
    assert hill.crs().authid() == "EPSG:32759"
    assert "This dataset is produced by Google" in hill.metadata().rights()
    lrm = layers.make_layer(plan.layers[0].path, "lrm", "lrm")
    assert lrm.renderer().type() == "singlebandpseudocolor"
    assert lrm.hasScaleBasedVisibility() and lrm.minimumScale() == settings.LRM_MAX_SCALE
    assert not lrm.isInScaleRange(100000) and lrm.isInScaleRange(25000)


@pytest.mark.parametrize("bbox, per_zone, n_layers, crs_id", [
    ((173.85, -41.78, 173.95, -41.72), False, 1, "EPSG:32759"),   # one zone: native grid
    ((173.90, -41.78, 174.10, -41.72), False, 1, "EPSG:2193"),    # two zones in NZ: NZTM
    ((173.90, -41.78, 174.10, -41.72), True, 2, "EPSG:32759"),    # one layer per zone
])
def test_task_adds_layers(qgis_app, project, synthetic, tmp_path, wait_for,
                          gdal_exceptions_off, bbox, per_zone, n_layers, crs_id):
    from gem15 import plugin, tasks
    from osgeo import gdal
    from qgis.core import QgsApplication
    done = []

    def on_done(task, ok):
        done.append(ok)
        if ok:
            plugin.add_layers(task.plan, task.layers)

    task = tasks.CurrentViewTask(bbox, "dtm", str(tmp_path), per_zone, on_done,
                                 collection_url=synthetic["collection_url"])
    QgsApplication.taskManager().addTask(task)
    wait_for(lambda: done)
    assert done == [True], task.error
    lyrs = list(project.mapLayers().values())
    assert len(lyrs) == n_layers
    for lyr in lyrs:
        assert lyr.isValid() and lyr.renderer().type() == "hillshade"
        assert lyr.thread() == qgis_app.thread(), "layer must live on the main thread"
    assert sorted(lyr.crs().authid() for lyr in lyrs)[0] == crs_id
    if n_layers > 1:
        (group,) = project.layerTreeRoot().findGroups()
        assert "UTM zones" in group.name() and len(group.findLayers()) == n_layers
    assert gdal.GetUseExceptions() == 0


def test_task_reports_no_coverage(qgis_app, project, synthetic, tmp_path, wait_for):
    from gem15 import tasks
    from qgis.core import QgsApplication
    done = []
    task = tasks.CurrentViewTask((175.5, -41.8, 175.6, -41.7), "dtm", str(tmp_path), False,
                                 lambda t, ok: done.append(ok),
                                 collection_url=synthetic["collection_url"])
    QgsApplication.taskManager().addTask(task)
    wait_for(lambda: done)
    assert done == [False] and "No GEM-15 tiles" in task.error
    assert not project.mapLayers()
