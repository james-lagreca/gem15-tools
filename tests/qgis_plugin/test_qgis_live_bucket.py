# SPDX-License-Identifier: GPL-2.0-or-later
"""The plugin against the live, public bucket (anonymous, read-only).

Skipped unless GEM15_LIVE=1. Victoria (UTM 55S). Downloads a few MB at most:
checks that QGIS streams through the plugin's VRTs (overviews when zoomed
out), and times a live LRM redraw at 1:25,000.
"""
import json
import time

import pytest

pytestmark = pytest.mark.live

VIEW = (144.10, -37.52, 144.14, -37.49)


@pytest.fixture(scope="module")
def plan(tmp_path_factory, qgis_app):
    from gem15 import gdalsafe, live
    from gem15.gem15core import stac
    gdalsafe.configure_remote()
    fetch = stac.Fetcher(cache_dir=str(tmp_path_factory.mktemp("cache")))
    folder = tmp_path_factory.mktemp("vrt")
    return {k: live.plan_layers(VIEW, k, str(folder), fetch=fetch) for k in ("dtm", "lrm")}


def _render(layer, extent, w, h):
    from qgis.core import QgsMapRendererSequentialJob, QgsMapSettings
    from qgis.PyQt.QtCore import QSize
    ms = QgsMapSettings()
    ms.setLayers([layer])
    ms.setDestinationCrs(layer.crs())
    ms.setOutputSize(QSize(w, h))
    ms.setExtent(extent)
    job = QgsMapRendererSequentialJob(ms)
    t = time.perf_counter()
    job.start()
    job.waitForFinished()
    return job.renderedImage(), time.perf_counter() - t, ms.scale()


def _downloaded_mb():
    from osgeo import gdal
    stats = json.loads(gdal.NetworkStatsGetAsSerializedJSON() or "{}")
    return stats.get("methods", {}).get("GET", {}).get("downloaded_bytes", 0) / 1e6


def test_zoomed_out_hillshade_streams_little(plan, qgis_app, gdal_exceptions_off):
    from osgeo import gdal
    from gem15 import layers
    gdal.SetConfigOption("CPL_VSIL_NETWORK_STATS_ENABLED", "YES")
    gdal.NetworkStatsReset()
    spec = plan["dtm"].layers[0]
    layer = layers.make_layer(spec.path, spec.name, spec.style)        # opens: samples stats
    opened = _downloaded_mb()
    img, dt, scale = _render(layer, layer.extent(), 512, 512)           # whole tile(s)
    total = _downloaded_mb()
    print(f"open {opened:.2f} MB, whole-tile render 512 px {total - opened:.2f} MB "
          f"in {dt:.1f} s at 1:{scale:,.0f}")
    assert not img.isNull()
    assert total < 15, f"downloaded {total:.1f} MB: overviews not used?"


def test_live_lrm_redraw_time(plan, qgis_app, gdal_exceptions_off):
    from gem15 import layers
    from qgis.core import QgsRectangle
    spec = plan["lrm"].layers[0]
    layer = layers.make_layer(spec.path, spec.name, spec.style)
    # 1:25,000 at 96 dpi is 6.6 m per pixel: a 960 x 540 view (a quarter of full HD).
    c = layer.extent().center()
    w, h, px = 960, 540, 6.6146
    ext = QgsRectangle(c.x() - w * px / 2, c.y() - h * px / 2, c.x() + w * px / 2, c.y() + h * px / 2)
    _render(layer, ext, w, h)                                           # first: network
    img, dt, scale = _render(layer, ext, w, h)                          # again: cached blocks
    print(f"live LRM redraw at 1:{scale:,.0f}, {w} x {h} px: {dt:.2f} s (blocks cached)")
    assert not img.isNull() and dt < 30
