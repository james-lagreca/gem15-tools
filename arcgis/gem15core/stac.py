# SPDX-License-Identifier: GPL-2.0-or-later
"""
Find GEM-15 tiles for an area using the public, static STAC catalogue.

Layout (checked 2026-11-15): collection.json -> <group>/catalog.json ->
<cell>/<cell>.json. A lookup fetches the collection, the catalogue of each
10-degree group that overlaps the area, and then only the items whose cell
name says they overlap. A one-cell area therefore costs three small requests,
and nothing at all once the documents are in the local cache.

Assumptions (flagged):
 S1. Group and cell names encode their extents (see `cells`). They are only
     used to skip requests; a name that does not parse is fetched anyway, and
     the final filter always uses each item's own bbox.
 S2. The catalogue is static (one acquisition date), so cached documents are
     reused for CACHE_MAX_AGE_DAYS. Pass refresh=True to ignore the cache.
 S3. Each asset carries proj:epsg, proj:shape and proj:transform (STAC
     projection extension v1.1); item-level values are used if an asset lacks
     them. proj:transform is in affine order (a, b, c, d, e, f):
     x = a*col + b*row + c, y = d*col + e*row + f.
 S4. Only documents that look like STAC (a catalogue or collection with
     links, or an item with a bbox and assets) are cached. A Wi-Fi login page,
     proxy block page or other stray JSON raises an error instead, so it can
     never be cached and then report "no coverage" for CACHE_MAX_AGE_DAYS.
 S5. Network failures of every kind (timeouts, which Python 3.9 raises as
     socket.timeout; resets; refused connections) are retried HTTP_RETRIES
     times, each waiting at most HTTP_TIMEOUT_S, then reported in plain words.
"""

import hashlib
import http.client
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

from . import (DSM_BANDS, DTM_BANDS, PRODUCTS, STAC_COLLECTION_URL, USER_AGENT,
               Gem15Error, cells, extent)

CACHE_MAX_AGE_DAYS = 30
HTTP_RETRIES = 3
HTTP_TIMEOUT_S = 20                # per attempt (S5); the documents are a few kB
NETWORK_HINT = ("Check the internet connection. On hotel, campus or corporate "
                "networks a Wi-Fi login page, proxy or firewall may be blocking "
                "storage.googleapis.com: open " + STAC_COLLECTION_URL +
                " in a web browser to test.")


# --------------------------------------------------------------------------
# Tiles
# --------------------------------------------------------------------------
@dataclass
class Asset:
    """One product (dtm or dsm) of one tile."""
    href: str                       # absolute URL (https:// or file://)
    epsg: int
    shape: Optional[Tuple[int, int]]          # (rows, cols)
    transform: Optional[Tuple[float, ...]]    # affine (a, b, c, d, e, f), S3
    bands: Tuple[str, ...]

    def band_index(self, name):
        """1-based band number of a band name, e.g. 'dsm_std' -> 5."""
        if name not in self.bands:
            raise Gem15Error(f"Band '{name}' is not one of {list(self.bands)}")
        return self.bands.index(name) + 1

    def footprint(self):
        """(xmin, ymin, xmax, ymax) of the whole tile in its own UTM CRS (metres)."""
        if not self.shape or not self.transform:
            raise Gem15Error(f"No proj:shape / proj:transform for {self.href}")
        a, b, c, d, e, f = self.transform
        if b or d:
            raise Gem15Error(f"Rotated grid not supported: {self.href}")
        rows, cols = self.shape
        xs = (c, c + a * cols)
        ys = (f, f + e * rows)
        return (min(xs), min(ys), max(xs), max(ys))

    def geotransform(self):
        """The same grid as a GDAL geotransform (c, a, b, f, d, e)."""
        a, b, c, d, e, f = self.transform
        return (c, a, b, f, d, e)


@dataclass
class Tile:
    """One GEM-15 1 x 1 degree cell and its COG assets."""
    item_id: str                    # e.g. "gem-15_w172sm42"
    cell_id: str                    # e.g. "w172sm42"
    group_id: str                   # e.g. "m50_p170"
    bbox: Tuple[float, float, float, float]   # cell extent, lon/lat (W, S, E, N)
    epsg: int                       # UTM EPSG code of the tile's grid
    item_url: str
    assets: Dict[str, Asset] = field(default_factory=dict)

    def asset(self, product):
        if product not in self.assets:
            raise Gem15Error(f"{self.item_id} has no '{product}' asset")
        return self.assets[product]


def _default_bands(product):
    return DSM_BANDS if product == "dsm" else DTM_BANDS


def tile_from_item(item, item_url):
    """Build a Tile from a STAC item dictionary (hrefs made absolute)."""
    props = item.get("properties", {})
    item_epsg = props.get("proj:epsg")
    assets = {}
    for product in PRODUCTS:
        a = item.get("assets", {}).get(product)
        if not a:
            continue
        epsg = a.get("proj:epsg", item_epsg)
        if epsg is None:
            raise Gem15Error(f"No proj:epsg for {product} in {item_url}")
        shape = a.get("proj:shape", props.get("proj:shape"))
        transform = a.get("proj:transform", props.get("proj:transform"))
        names = tuple(b.get("name") for b in a.get("raster:bands", []) if b.get("name"))
        assets[product] = Asset(
            href=urllib.parse.urljoin(item_url, a["href"]),
            epsg=int(epsg),
            shape=tuple(int(v) for v in shape) if shape else None,
            transform=tuple(float(v) for v in transform[:6]) if transform else None,
            bands=names or _default_bands(product))
    item_id = item.get("id", "")
    cell_id = props.get("gem:cell_id") or item_id.replace(cells.ITEM_PREFIX, "", 1)
    epsg = item_epsg if item_epsg is not None else next(
        (a.epsg for a in assets.values()), None)
    if epsg is None:
        raise Gem15Error(f"No proj:epsg in {item_url}")
    return Tile(item_id=item_id, cell_id=cell_id,
                group_id=props.get("gem:group_id", ""),
                bbox=tuple(float(v) for v in item["bbox"][:4]),
                epsg=int(epsg), item_url=item_url, assets=assets)


# --------------------------------------------------------------------------
# Fetching, with an on-disk cache
# --------------------------------------------------------------------------
def default_cache_dir():
    """Per-user cache folder: GEM15_CACHE_DIR if set, else the platform's usual place."""
    env = os.environ.get("GEM15_CACHE_DIR")
    if env:
        return env
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Caches")
    else:
        base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(base, "gem15tools", "stac")


def _opener(proxy):
    """urllib opener: the system's proxies by default, or `proxy` ("host:port")."""
    if not proxy:
        return urllib.request.build_opener()
    url = proxy if "://" in proxy else "http://" + proxy
    return urllib.request.build_opener(urllib.request.ProxyHandler({"http": url, "https": url}))


def _http_get_json(url, timeout, opener=None):
    """GET a JSON document, retrying transient failures (not 4xx) (S5)."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    opener = opener or urllib.request.build_opener()
    last = None
    for attempt in range(HTTP_RETRIES):
        try:
            with opener.open(req, timeout=timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:                  # before OSError: a subclass
            if 400 <= e.code < 500:
                raise Gem15Error(f"HTTP {e.code} for {url}. {NETWORK_HINT}") from e
            last = e
        except (OSError, http.client.HTTPException) as e:    # URLError, timeouts, resets
            last = e
        else:
            try:
                return json.loads(raw.decode("utf-8"))
            except ValueError as e:
                start = raw[:60].decode("utf-8", "replace").strip()
                raise Gem15Error(f"{url} did not return the GEM-15 catalogue (it began "
                                 f"{start!r}). {NETWORK_HINT}") from e
        if attempt + 1 < HTTP_RETRIES:
            time.sleep(0.5 * 2 ** attempt)
    raise Gem15Error(f"Could not fetch {url} ({last}). {NETWORK_HINT}")


def looks_like_stac(doc):
    """True for a STAC catalogue/collection (has links) or item (bbox + assets) (S4)."""
    return isinstance(doc, dict) and (
        isinstance(doc.get("links"), list) or ("bbox" in doc and "assets" in doc))


def clear_cache(cache_dir=None):
    """Delete the cached catalogue documents; returns how many were removed."""
    folder = cache_dir or default_cache_dir()
    n = 0
    try:
        names = os.listdir(folder)
    except OSError:
        return 0
    for name in names:
        if name.endswith((".json", ".tmp")):
            try:
                os.remove(os.path.join(folder, name))
                n += 1
            except OSError:
                pass
    return n


class Fetcher:
    """Callable url -> parsed JSON, caching http(s) documents on disk.

    cache_dir=None disables the cache. file:// URLs (used by tests) are never cached.
    proxy : "host:port" of an HTTP proxy (e.g. QGIS's own setting); default:
            the system's proxy settings, as urllib finds them.
    """

    def __init__(self, cache_dir="default", max_age_days=CACHE_MAX_AGE_DAYS,
                 refresh=False, timeout=HTTP_TIMEOUT_S, proxy=None):
        self.cache_dir = default_cache_dir() if cache_dir == "default" else cache_dir
        self.max_age_s = max_age_days * 86400
        self.refresh = refresh
        self.timeout = timeout
        self.opener = _opener(proxy)

    def _path(self, url):
        return os.path.join(self.cache_dir, hashlib.sha1(url.encode("utf-8")).hexdigest() + ".json")

    def _read_cache(self, url):
        p = self._path(url)
        try:
            if time.time() - os.path.getmtime(p) > self.max_age_s:
                return None
            with open(p, encoding="utf-8") as fh:
                doc = json.load(fh)
            return doc if looks_like_stac(doc) else None    # S4: never trust a stray file
        except (OSError, ValueError):
            return None                      # missing, unreadable or corrupt: refetch

    def _write_cache(self, url, doc):
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.cache_dir, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(doc, fh)
            os.replace(tmp, self._path(url))  # atomic, so readers never see half a file
        except OSError:
            pass                             # a cache we cannot write is not an error

    def __call__(self, url):
        if url.startswith("file:"):
            with urllib.request.urlopen(url) as resp:
                return json.load(resp)
        cacheable = self.cache_dir is not None
        if cacheable and not self.refresh:
            doc = self._read_cache(url)
            if doc is not None:
                return doc
        doc = _http_get_json(url, self.timeout, self.opener)
        if not looks_like_stac(doc):                                     # S4
            raise Gem15Error(f"{url} returned JSON that is not part of the GEM-15 "
                             f"catalogue. {NETWORK_HINT}")
        if cacheable:
            self._write_cache(url, doc)
        return doc


def _links(doc, base_url, rel):
    return [urllib.parse.urljoin(base_url, link["href"])
            for link in doc.get("links", []) if link.get("rel") == rel]


def _name_from_url(url, depth):
    """Folder name `depth` levels above the file: depth 1 = parent folder."""
    parts = urllib.parse.urlparse(url).path.rstrip("/").split("/")
    return parts[-1 - depth] if len(parts) > depth else ""


# --------------------------------------------------------------------------
# Lookup
# --------------------------------------------------------------------------
def too_many_cells(bbox, max_cells):
    """A Gem15Error message if bbox covers more than max_cells 1-degree cells, else None."""
    n = cells.count_for_bbox(bbox)
    if max_cells and n > max_cells:
        return (f"This area covers {n} one-degree cells, more than the limit of "
                f"{max_cells}. Zoom in, or draw a smaller extent.")
    return None


def find_tiles(bbox, product=None, collection_url=STAC_COLLECTION_URL, fetch=None,
               workers=8, max_cells=None, progress=None, is_cancelled=None):
    """Tiles whose cell overlaps bbox (W, S, E, N in degrees; W > E crosses 180).

    product      : "dtm" or "dsm" to keep only tiles that have that asset; None for all.
    fetch        : callable url -> JSON; defaults to a cached Fetcher().
    max_cells    : refuse (before any request) an area of more 1-degree cells.
    progress     : optional callable(done, total) as item documents arrive.
    is_cancelled : optional callable -> True to stop (raises Gem15Error "Cancelled").
    Returns a list of Tile, sorted by cell id. Raises Gem15Error on bad input
    or network failure; an empty list means no coverage.
    """
    bbox = extent.check(bbox)
    if product is not None and product not in PRODUCTS:
        raise Gem15Error(f"product must be one of {PRODUCTS}, not {product!r}")
    msg = too_many_cells(bbox, max_cells)
    if msg:
        raise Gem15Error(msg)
    fetch = fetch or Fetcher()
    is_cancelled = is_cancelled or (lambda: False)

    collection = fetch(collection_url)
    children = _links(collection, collection_url, "child")
    if not children:
        raise Gem15Error(f"The catalogue at {collection_url} lists no tile groups; it may "
                         f"have moved. {NETWORK_HINT}")
    group_urls = []
    for url in children:
        gbox = cells.parse_group(_name_from_url(url, 1))
        if gbox is None or extent.intersects(gbox, bbox):         # S1
            group_urls.append(url)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        item_urls = []
        for gurl, gdoc in zip(group_urls, pool.map(fetch, group_urls)):
            for iurl in _links(gdoc, gurl, "item"):
                cbox = cells.parse_cell(_name_from_url(iurl, 1))
                if cbox is None or extent.intersects(cbox, bbox):  # S1
                    item_urls.append(iurl)
        items = [None] * len(item_urls)
        futures = {pool.submit(fetch, u): i for i, u in enumerate(item_urls)}
        for done, fut in enumerate(as_completed(futures), 1):
            if is_cancelled():
                for f in futures:
                    f.cancel()
                raise Gem15Error("Cancelled.")
            items[futures[fut]] = fut.result()
            if progress:
                progress(done, len(item_urls))

    tiles = []
    for item, iurl in zip(items, item_urls):
        if not extent.intersects(tuple(item["bbox"][:4]), bbox):
            continue
        tile = tile_from_item(item, iurl)
        if product is None or product in tile.assets:
            tiles.append(tile)
    return sorted(tiles, key=lambda t: t.cell_id)


def group_by_epsg(tiles):
    """{epsg: [tiles]} in ascending EPSG order."""
    out = {}
    for t in tiles:
        out.setdefault(t.epsg, []).append(t)
    return dict(sorted(out.items()))
