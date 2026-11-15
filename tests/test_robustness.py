# SPDX-License-Identifier: GPL-2.0-or-later
"""Failure handling found by the 2026-11-15 robustness review: catalogue
responses that are not STAC, network errors, huge areas, poles, coordinate
systems in degrees or feet, GDAL with exceptions on or off, cancelled clips.
No network access."""
import io
import json
import os
import socket
import urllib.error

import pytest

from gem15core import Gem15Error, cells, extent, stac
from test_stac import FakeFetch, make_docs


# --------------------------------------------------------------------------
# Catalogue fetching (stac S4, S5)
# --------------------------------------------------------------------------
class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    """Stands in for a urllib opener: each call pops the next outcome."""

    def __init__(self, *outcomes):
        self.outcomes, self.calls = list(outcomes), 0

    def open(self, req, timeout=None):
        self.calls += 1
        out = self.outcomes.pop(0) if self.outcomes else self.last
        self.last = out
        if isinstance(out, BaseException):
            raise out
        return _Resp(out)


URL = "https://example.invalid/gem-15/stac/collection.json"
GOOD = json.dumps({"type": "Collection", "links": []}).encode()


def fetcher(tmp_path, opener, monkeypatch):
    monkeypatch.setattr(stac.time, "sleep", lambda s: None)     # no back-off waits
    f = stac.Fetcher(cache_dir=str(tmp_path))
    f.opener = opener
    return f


def test_login_page_is_reported_and_not_cached(tmp_path, monkeypatch):
    page = b"<!DOCTYPE html><html><body>Please sign in to the hotel Wi-Fi</body></html>"
    f = fetcher(tmp_path, FakeOpener(page), monkeypatch)
    with pytest.raises(Gem15Error, match="did not return the GEM-15 catalogue.*web browser"):
        f(URL)
    assert not any(n.endswith(".json") for n in os.listdir(tmp_path))


def test_stray_json_is_refused_and_never_cached(tmp_path, monkeypatch):
    f = fetcher(tmp_path, FakeOpener(b'{"error": "blocked by policy"}'), monkeypatch)
    with pytest.raises(Gem15Error, match="not part of the GEM-15 catalogue"):
        f(URL)
    assert not os.listdir(tmp_path)
    # A stray file already in the cache is ignored and fetched again.
    with open(f._path(URL), "w", encoding="utf-8") as fh:
        json.dump({"error": "old"}, fh)
    f.opener = FakeOpener(GOOD)
    assert f(URL)["type"] == "Collection" and f.opener.calls == 1


@pytest.mark.parametrize("err", [socket.timeout("timed out"), ConnectionResetError(),
                                 urllib.error.URLError("refused"), TimeoutError()])
def test_network_errors_are_retried_then_explained(tmp_path, monkeypatch, err):
    f = fetcher(tmp_path, FakeOpener(err, err, err), monkeypatch)
    with pytest.raises(Gem15Error, match="Could not fetch.*proxy or firewall"):
        f(URL)
    assert f.opener.calls == stac.HTTP_RETRIES
    f2 = fetcher(tmp_path, FakeOpener(err, GOOD), monkeypatch)     # recovers on retry
    assert f2(URL)["type"] == "Collection"


def test_clear_cache(tmp_path, monkeypatch):
    f = fetcher(tmp_path, FakeOpener(GOOD), monkeypatch)
    f(URL)
    assert stac.clear_cache(str(tmp_path)) == 1 and not os.listdir(tmp_path)
    assert stac.clear_cache(str(tmp_path / "missing")) == 0


def test_collection_without_groups_is_an_error():
    docs = make_docs()
    docs[stac.STAC_COLLECTION_URL] = {"type": "Collection", "links": []}
    with pytest.raises(Gem15Error, match="lists no tile groups"):
        stac.find_tiles((172.6, -41.95, 173.2, -41.65), "dtm", fetch=FakeFetch(docs))


def test_huge_area_refused_before_any_request():
    calls = []

    def fetch(url):
        calls.append(url)
        raise AssertionError("no request expected")
    with pytest.raises(Gem15Error, match="more than the limit of 1500"):
        stac.find_tiles((110, -45, 155, -9), "dtm", fetch=fetch, max_cells=1500)
    assert not calls


def test_progress_and_cancel():
    seen = []
    tiles = stac.find_tiles((172.6, -41.95, 173.2, -41.65), "dtm", fetch=FakeFetch(make_docs()),
                            progress=lambda d, t: seen.append((d, t)))
    assert tiles and seen[-1][0] == seen[-1][1] == len(seen)
    with pytest.raises(Gem15Error, match="Cancelled"):
        stac.find_tiles((172.6, -41.95, 173.2, -41.65), "dtm", fetch=FakeFetch(make_docs()),
                        is_cancelled=lambda: True)


# --------------------------------------------------------------------------
# Cells and extents
# --------------------------------------------------------------------------
@pytest.mark.parametrize("bbox, n", [
    ((144.1, -37.52, 144.14, -37.49), 1),
    ((144.0, -38.0, 145.0, -37.0), 1),               # exactly one cell
    ((143.5, -38.5, 145.5, -36.5), 9),
    ((179.5, -17.5, -179.5, -16.5), 4),               # across 180
    ((113, -44, 154, -10), 41 * 34),                  # Australia's box
])
def test_count_for_bbox(bbox, n):
    assert cells.count_for_bbox(bbox) == n
    if bbox[0] < bbox[2]:
        assert n == len(cells.cell_names_for_bbox(bbox))


def test_include_poles():
    assert extent.include_poles((-168.7, 78.3, 180, 81.7), True) == (-180, 78.3, 180, 90)
    assert extent.include_poles((10, -80, 30, -70), False, True) == (-180, -90, 180, -70)
    assert extent.include_poles((10, 20, 30, 40)) == (10, 20, 30, 40)


# --------------------------------------------------------------------------
# GDAL (gdalops G6, G7) - needs osgeo
# --------------------------------------------------------------------------
@pytest.fixture
def gdal():
    """GDAL, or skip (the tests above this need none)."""
    return pytest.importorskip("osgeo.gdal")


@pytest.fixture(params=[False, True], ids=["exceptions-off", "exceptions-on"])
def gdal_mode(request, gdal):
    """Run a test with GDAL exceptions off (as in QGIS and Pro) and on. GDAL
    3.6 keeps these settings as a stack (gdal under osr), so changes are made
    and undone in stack order."""
    from osgeo import osr
    done = []

    def put(mod, on):
        if bool(mod.GetUseExceptions()) != on:
            (mod.UseExceptions if on else mod.DontUseExceptions)()
            done.append((mod, not on))
    if request.param:
        put(gdal, True)
        put(osr, True)
    else:
        put(osr, False)
        put(gdal, False)
    yield request.param
    for mod, on in reversed(done):
        (mod.UseExceptions if on else mod.DontUseExceptions)()


def test_importing_gdalops_leaves_exceptions_alone(gdal):
    import importlib
    from osgeo import osr
    from gem15core import gdalops
    before = (gdal.GetUseExceptions(), osr.GetUseExceptions())
    importlib.reload(gdalops)
    assert (gdal.GetUseExceptions(), osr.GetUseExceptions()) == before


def test_bad_crs_and_units(gdal_mode):
    from gem15core import gdalops
    with pytest.raises(Gem15Error, match="Unknown coordinate system"):
        gdalops.srs("not a CRS")
    with pytest.raises(Gem15Error, match="uses degrees"):
        gdalops.check_metric_crs("EPSG:4326")
    with pytest.raises(Gem15Error, match="foot|feet|Foot"):
        gdalops.check_metric_crs("EPSG:2227")                  # California zone 3, US feet
    gdalops.check_metric_crs("EPSG:7845")                      # metres: fine


def test_geographic_target_refused_not_one_pixel(synthetic, tmp_path, gdal_mode):
    from gem15core import gdalops
    with pytest.raises(Gem15Error, match="degrees"):
        gdalops.build_vrt((173.9, -41.78, 174.1, -41.70), str(tmp_path / "g.vrt"),
                          dst_crs="EPSG:4326", collection_url=synthetic["collection_url"],
                          fetch=stac.Fetcher(cache_dir=None), log=None)
    assert not (tmp_path / "g.vrt").exists()


def test_clip_cancel_removes_partial_file(synthetic, tmp_path, gdal_mode):
    from gem15core import gdalops
    src = gdalops.build_vrt((173.84, -41.79, 173.96, -41.71), str(tmp_path / "c.vrt"),
                            collection_url=synthetic["collection_url"],
                            fetch=stac.Fetcher(cache_dir=None), log=None)
    out = tmp_path / "c.tif"
    with pytest.raises(Gem15Error, match="Cancelled"):
        gdalops.clip_to_geotiff(src, str(out), log=None, callback=lambda f, m, d: 0)
    assert not out.exists()
    gdalops.clip_to_geotiff(src, str(out), log=None)          # and it still works after
    assert out.exists()


def test_missing_source_is_a_gem15error(tmp_path, gdal_mode):
    from gem15core import gdalops
    with pytest.raises(Gem15Error, match="Opening"):
        gdalops.clip_to_geotiff(str(tmp_path / "nope.vrt"), str(tmp_path / "x.tif"), log=None)


def test_proxy_and_timeouts_scoped_to_bucket(monkeypatch, gdal):
    from gem15core import gdalops
    if not hasattr(gdal, "GetPathSpecificOption"):
        pytest.skip("GDAL < 3.6: options are not path-specific")
    prefix = "/vsicurl/http://127.0.0.1:1/gem15-test/"
    monkeypatch.setattr(gdalops.urllib.request, "getproxies",
                        lambda: {"https": "https://proxy.example:3128"})
    old = gdal.GetConfigOption("GDAL_HTTP_PROXY")
    gdal.SetConfigOption("GDAL_HTTP_PROXY", None)
    # ArcGIS Pro's own gdalrc sets 480 s process-wide (seen 2026-11-15); ours
    # must apply to the bucket only and leave that value as it was.
    process_timeout = gdal.GetConfigOption("GDAL_HTTP_TIMEOUT")
    try:
        assert gdalops.configure_remote(prefix=prefix) == "scoped"
        assert gdal.GetPathSpecificOption(prefix, "GDAL_HTTP_PROXY") == "proxy.example:3128"
        assert gdal.GetPathSpecificOption(prefix, "GDAL_HTTP_TIMEOUT") == "60"
        assert gdal.GetConfigOption("GDAL_HTTP_TIMEOUT") == process_timeout
        gdalops.configure_remote(prefix=prefix, proxy="qgis.example:8080")
        assert gdal.GetPathSpecificOption(prefix, "GDAL_HTTP_PROXY") == "qgis.example:8080"
    finally:
        gdal.SetConfigOption("GDAL_HTTP_PROXY", old)
        gdal.ClearPathSpecificOptions(prefix)
