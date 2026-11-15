# SPDX-License-Identifier: GPL-2.0-or-later
"""
Catalogue requests that follow QGIS's own network settings.

The catalogue lookup uses Python's urllib, which follows the system's proxy
but not the one set in QGIS (Settings > Options > Network). GDAL's curl
follows neither on Windows. So when QGIS has an HTTP proxy configured, the
plugin passes it to both: to the catalogue Fetcher and, for the GEM-15 bucket
only, to GDAL (gdalsafe.configure_remote).

Assumptions (flagged):
 N1. QGIS stores its proxy in QgsSettings under proxy/proxyEnabled,
     proxy/proxyType, proxy/proxyHost and proxy/proxyPort (QGIS 3.x).
     "DefaultProxy" means the system's proxy, which urllib finds itself.
 N2. Only plain HTTP proxies are passed on. Proxy user names and passwords
     are not: set them for GDAL with GDAL_HTTP_PROXYUSERPWD if needed.
"""

from .gem15core import stac


def qgis_proxy():
    """'host:port' of QGIS's HTTP proxy, or None to use the system's (N1, N2)."""
    try:
        from qgis.core import QgsSettings
        s = QgsSettings()
        if not s.value("proxy/proxyEnabled", False, type=bool):
            return None
        if s.value("proxy/proxyType", "", type=str) not in ("HttpProxy", "HttpCachingProxy"):
            return None
        host = (s.value("proxy/proxyHost", "", type=str) or "").strip()
        port = str(s.value("proxy/proxyPort", "", type=str) or "").strip()
    except Exception:                  # settings unreadable: fall back to the system's
        return None
    if not host:
        return None
    return f"{host}:{port}" if port else host


def fetcher(**kwargs):
    """A catalogue Fetcher that uses QGIS's proxy when one is set."""
    return stac.Fetcher(proxy=qgis_proxy(), **kwargs)
