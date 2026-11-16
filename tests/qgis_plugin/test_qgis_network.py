# SPDX-License-Identifier: GPL-2.0-or-later
"""QGIS-side robustness: QGIS's proxy setting and views around a pole.
Headless, no network."""
import pytest

pytest.importorskip("qgis.core")


def test_qgis_proxy_setting(qgis_app):
    from qgis.core import QgsSettings
    from gem15 import network
    s = QgsSettings()
    keys = ("proxy/proxyEnabled", "proxy/proxyType", "proxy/proxyHost", "proxy/proxyPort")
    before = {k: s.value(k) for k in keys}
    try:
        s.setValue("proxy/proxyEnabled", False)
        assert network.qgis_proxy() is None
        s.setValue("proxy/proxyEnabled", True)
        s.setValue("proxy/proxyType", "HttpProxy")
        s.setValue("proxy/proxyHost", "proxy.example")
        s.setValue("proxy/proxyPort", "3128")
        assert network.qgis_proxy() == "proxy.example:3128"
        assert network.fetcher(cache_dir=None).opener is not None
        s.setValue("proxy/proxyType", "DefaultProxy")          # the system's: urllib's job
        assert network.qgis_proxy() is None
    finally:
        for k, v in before.items():
            if v is None:
                s.remove(k)
            else:
                s.setValue(k, v)


def test_polar_view_reaches_the_pole(qgis_app):
    from qgis.core import QgsCoordinateReferenceSystem, QgsRectangle
    from gem15 import view
    arctic = QgsCoordinateReferenceSystem("EPSG:3995")
    w, s, e, n = view.lonlat_bbox(QgsRectangle(-900000, -900000, 900000, 900000), arctic)
    assert n == 90 and (w, e) == (-180, 180) and s < 82
    # A view beside the pole is not widened.
    w, s, e, n = view.lonlat_bbox(QgsRectangle(1000000, -500000, 1500000, 0), arctic)
    assert n < 90 and (e - w) % 360 < 90
