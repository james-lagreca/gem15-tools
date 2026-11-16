# SPDX-License-Identifier: GPL-2.0-or-later
"""Catalogue lookup and cache, against an in-memory fake catalogue (pure Python)."""
import io
import json
import os
import urllib.error

import pytest

from gem15core import Gem15Error, stac

S = "https://storage.googleapis.com/climate-and-sustainability/gem-15/stac/gem-15/"
B = "https://storage.googleapis.com/climate-and-sustainability/gem-15/"


def item(group, cell, bbox, epsg, asset_epsg=True, products=("dsm", "dtm"), x0=414720.0):
    assets = {}
    for p in products:
        a = {"href": f"{B}{group}/{cell}/{p}/zall/mosaic.tif",
             "proj:shape": [8192, 8192],
             "proj:transform": [15.0, 0.0, x0, 0.0, -15.0, 5360640.0]}
        if asset_epsg:
            a["proj:epsg"] = epsg
        assets[p] = a
    return {"id": f"gem-15_{cell}", "bbox": bbox,
            "properties": {"proj:epsg": epsg, "gem:cell_id": cell, "gem:group_id": group},
            "assets": assets}


def make_docs():
    groups = {
        "m50_p170": [("w172sm42", [172, -42, 173, -41], 32759),
                     ("w173sm42", [173, -42, 174, -41], 32759),
                     ("w170sm45", [170, -45, 171, -44], 32759)],
        "m20_p170": [("w179sm17", [179, -17, 180, -16], 32760)],
        "m20_m180": [("wm180sm17", [-180, -17, -179, -16], 32701)],
    }
    docs = {S + "collection.json": {"links": [
        {"rel": "child", "href": f"./{g}/catalog.json"} for g in list(groups) + ["m10_m100"]]}}
    for g, cells_ in groups.items():
        links = [{"rel": "item", "href": f"./{c}/{c}.json"} for c, _, _ in cells_]
        docs[S + f"{g}/catalog.json"] = {"links": links}
        for c, bbox, epsg in cells_:
            docs[S + f"{g}/{c}/{c}.json"] = item(g, c, bbox, epsg)
    # An item whose name does not parse: must be fetched, then filtered by bbox.
    docs[S + "m50_p170/catalog.json"]["links"].append({"rel": "item", "href": "./odd/odd.json"})
    docs[S + "m50_p170/odd/odd.json"] = item("m50_p170", "odd", [170, -50, 171, -49], 32759)
    return docs


class FakeFetch:
    def __init__(self, docs):
        self.docs, self.urls = docs, []

    def __call__(self, url):
        self.urls.append(url)
        return self.docs[url]       # KeyError if a pruned group is fetched


def test_find_tiles_prunes_by_name():
    f = FakeFetch(make_docs())
    tiles = stac.find_tiles((172.6, -41.95, 173.2, -41.65), "dtm", fetch=f)
    assert [t.cell_id for t in tiles] == ["w172sm42", "w173sm42"]
    fetched = {u.rsplit("/", 1)[-1] for u in f.urls}
    assert "w170sm45.json" not in fetched          # pruned by its name
    assert "odd.json" in fetched                   # unparseable: fetched, then filtered
    assert not any("m20_" in u for u in f.urls)    # other groups pruned


def test_tile_fields():
    t = stac.find_tiles((172.6, -41.95, 172.7, -41.9), "dsm", fetch=FakeFetch(make_docs()))[0]
    assert (t.item_id, t.group_id, t.epsg) == ("gem-15_w172sm42", "m50_p170", 32759)
    a = t.asset("dsm")
    assert a.href.endswith("m50_p170/w172sm42/dsm/zall/mosaic.tif")
    assert a.band_index("dsm_std") == 5 and t.asset("dtm").band_index("dtm") == 1
    assert a.footprint() == (414720.0, 5360640.0 - 122880.0, 414720.0 + 122880.0, 5360640.0)
    assert a.geotransform() == (414720.0, 15.0, 0.0, 5360640.0, 0.0, -15.0)
    with pytest.raises(Gem15Error):
        a.band_index("nope")


def test_antimeridian_query():
    tiles = stac.find_tiles((179.5, -17, -179.5, -16.5), None, fetch=FakeFetch(make_docs()))
    assert [(t.cell_id, t.epsg) for t in tiles] == [("w179sm17", 32760), ("wm180sm17", 32701)]


def test_epsg_from_item_when_asset_lacks_it_and_product_filter():
    docs = make_docs()
    url = S + "m50_p170/w172sm42/w172sm42.json"
    docs[url] = item("m50_p170", "w172sm42", [172, -42, 173, -41], 32759,
                     asset_epsg=False, products=("dtm",))
    tiles = stac.find_tiles((172.1, -41.9, 172.2, -41.8), None, fetch=FakeFetch(docs))
    assert tiles[0].asset("dtm").epsg == 32759
    assert stac.find_tiles((172.1, -41.9, 172.2, -41.8), "dsm", fetch=FakeFetch(docs)) == []


def test_bad_input():
    with pytest.raises(Gem15Error):
        stac.find_tiles((10, 0, 5, -1), "dtm", fetch=FakeFetch(make_docs()))
    with pytest.raises(Gem15Error):
        stac.find_tiles((172, -42, 173, -41), "lidar", fetch=FakeFetch(make_docs()))


def test_group_by_epsg():
    tiles = stac.find_tiles((172.5, -42, 180, -16), None, fetch=FakeFetch(make_docs()))
    groups = stac.group_by_epsg(tiles)
    assert list(groups) == [32759, 32760]


# --------------------------------------------------------------------------
# Cache
# --------------------------------------------------------------------------
def test_fetcher_cache(tmp_path, monkeypatch):
    calls = []

    def fake_get(url, timeout, opener=None):
        calls.append(url)
        return {"links": [], "n": len(calls)}                  # a (tiny) STAC catalogue

    def n(doc):
        return doc["n"]

    monkeypatch.setattr(stac, "_http_get_json", fake_get)
    f = stac.Fetcher(cache_dir=str(tmp_path))
    assert n(f("https://x/a.json")) == 1
    assert n(f("https://x/a.json")) == 1                       # from cache
    assert len(calls) == 1 and len(os.listdir(tmp_path)) == 1
    assert n(stac.Fetcher(cache_dir=str(tmp_path), refresh=True)("https://x/a.json")) == 2
    assert n(stac.Fetcher(cache_dir=str(tmp_path), max_age_days=-1)("https://x/a.json")) == 3
    for name in os.listdir(tmp_path):                          # corrupt the cache
        (tmp_path / name).write_text("{not json")
    assert n(f("https://x/a.json")) == 4
    nocache = stac.Fetcher(cache_dir=None)
    assert n(nocache("https://x/b.json")) == 5
    assert n(nocache("https://x/b.json")) == 6


def test_fetcher_reads_file_urls_without_caching(tmp_path):
    p = tmp_path / "doc.json"
    p.write_text(json.dumps({"ok": True}))
    cache = tmp_path / "cache"
    assert stac.Fetcher(cache_dir=str(cache))(p.as_uri()) == {"ok": True}
    assert not cache.exists()


def test_default_cache_dir_env(monkeypatch, tmp_path):
    monkeypatch.setenv("GEM15_CACHE_DIR", str(tmp_path))
    assert stac.default_cache_dir() == str(tmp_path)


class _Opener:
    """Stands in for a urllib opener (stac uses opener.open, not urlopen)."""

    def __init__(self, fn):
        self.open = fn


def test_http_errors(monkeypatch):
    def not_found(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, io.BytesIO(b""))

    with pytest.raises(Gem15Error, match="HTTP 404"):
        stac._http_get_json("https://x/missing.json", 5, _Opener(not_found))

    tries = []

    def offline(req, timeout):
        tries.append(1)
        raise urllib.error.URLError("no network")

    monkeypatch.setattr(stac.time, "sleep", lambda s: None)
    with pytest.raises(Gem15Error, match="internet connection"):
        stac._http_get_json("https://x/a.json", 5, _Opener(offline))
    assert len(tries) == stac.HTTP_RETRIES


def test_user_agent_is_generic(monkeypatch):
    seen = {}

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def capture(req, timeout):
        seen.update(req.headers)
        return Resp(b"{}")

    stac._http_get_json("https://x/a.json", 5, _Opener(capture))
    assert seen["User-agent"].startswith("gem15-tools/")
    assert "@" not in seen["User-agent"]
