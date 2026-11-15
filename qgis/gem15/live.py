# SPDX-License-Identifier: GPL-2.0-or-later
"""
Work out and write the VRT(s) for "GEM-15 for current view".

No GUI and no QGIS layer objects here, so it runs in a background task and
in tests. `plan_layers` does the catalogue lookup (network, cached) and
writes small VRT files; `layers.make_layer` then turns each LayerSpec into a
styled QGIS layer.

Behaviour (flagged):
 L1. The VRT covers the whole of every tile that touches the view, not just
     the view, so the layer stays useful when panning nearby.
 L2. One UTM zone: a VRT on the tiles' native 15 m grid (nothing resampled).
     Several zones: by default one warped VRT in the automatic CRS
     (gem15core.crs.auto_crs: GDA2020 / GA LCC in Australia, NZTM2000 in
     New Zealand, else a custom conformal projection), resampled bilinearly
     as QGIS draws it; or, with per_zone=True, one native-grid layer per
     zone. The live LRM is always one layer per zone, on the native grid.
 L3. File names come from the product, band, radius and the sorted cell
     ids, so the same view writes the same files. An existing file is reused
     rather than rewritten, because QGIS (GDAL) may hold it open in a layer.
"""

import hashlib
import os
from dataclasses import dataclass
from typing import List, Optional

from .gem15core import Gem15Error, cells, crs, extent, stac, terrain, vrt

KINDS = {
    # kind: (product, band, style, label)
    "dtm": ("dtm", "dtm", "hillshade", "DTM hillshade"),
    "dsm": ("dsm", "dsm", "hillshade", "DSM hillshade"),
    "lrm": ("dtm", "dtm", "lrm", "DTM live LRM"),
}


@dataclass
class LayerSpec:
    path: str           # VRT to open
    name: str           # layer name
    style: str          # "hillshade" or "lrm"
    crs_note: str       # e.g. "UTM 55S (native grid)"


@dataclass
class Plan:
    layers: List[LayerSpec]
    tiles: list
    messages: List[str]
    group: Optional[str] = None     # layer-tree group name when several layers


def _tag(tiles, product, band, extra=""):
    ids = ",".join(sorted(t.cell_id for t in tiles))
    h = hashlib.sha1(f"{product}|{band}|{extra}|{ids}".encode("utf-8")).hexdigest()[:8]
    first = min(t.cell_id for t in tiles)
    return f"{first}_{len(tiles)}t_{h}"


def tiles_bbox(tiles):
    """Lon/lat box around the tiles' 1-degree cells (L1), across 180 if need be."""
    pts = []
    for t in tiles:
        w, s, e, n = cells.parse_cell(t.cell_id) or t.bbox
        # Points just inside each cell, so a cell ending at 180 is not read as -180.
        pts += [(w + 1e-6, s), (e - 1e-6, n)]
    w, s, e, n = extent.from_lonlat_points(pts)
    return extent.check((round(w, 5), s, round(e, 5), n))


def zone_label(epsg):
    z = crs.utm_zone_of_epsg(epsg)
    return f"UTM {z[0]}{z[1]}" if z else f"EPSG:{epsg}"


def plan_layers(bbox, kind, folder, per_zone=False, fetch=None, collection_url=None,
                lrm_radius_m=terrain.LRM_RADIUS_M, log=None, is_cancelled=None):
    """Find tiles for bbox (W, S, E, N) and write the VRT(s) for `kind`
    ("dtm", "dsm" or "lrm") into `folder`. Returns a Plan.

    Raises Gem15Error on no coverage, bad input or network failure.
    """
    from . import lrm_vrt                     # local import: no GDAL needed for it

    if kind not in KINDS:
        raise Gem15Error(f"kind must be one of {list(KINDS)}, not {kind!r}")
    log = log or (lambda *_: None)
    is_cancelled = is_cancelled or (lambda: False)
    product, band, style, label = KINDS[kind]
    kw = {"fetch": fetch}
    if collection_url:
        kw["collection_url"] = collection_url
    tiles = stac.find_tiles(bbox, product, **kw)
    if not tiles:
        raise Gem15Error("No GEM-15 tiles cover this view. GEM-15 covers land "
                         "between about 61 S and 84 N.")
    if is_cancelled():
        return Plan([], tiles, ["Cancelled."])
    zones = stac.group_by_epsg(tiles)
    msgs = [f"{len(tiles)} GEM-15 tile(s) in {', '.join(zone_label(z) for z in zones)}."]
    os.makedirs(folder, exist_ok=True)
    radius_tag = f"r{lrm_radius_m:g}" if style == "lrm" else ""

    def native(epsg, ztiles):
        stem = os.path.join(folder, f"gem15_{band}_{_tag(ztiles, product, band)}_epsg{epsg}")
        path = stem + ".vrt"
        if not os.path.exists(path):                                # L3
            vrt.write_vrt(ztiles, product, path, band)
        return path

    layers = []
    if len(zones) == 1 or per_zone or style == "lrm":                # L2
        for epsg, ztiles in zones.items():
            base = native(epsg, ztiles)
            where = f"{zone_label(epsg)}, {len(ztiles)} tile(s)"
            if style == "lrm":
                path = base[:-4] + f"_lrm_{radius_tag}.vrt"
                if not os.path.exists(path):
                    lrm_vrt.write_lrm_vrt(base, path, lrm_radius_m)
                name = f"GEM-15 {label} {lrm_radius_m:g} m ({where})"
            else:
                path = base
                name = f"GEM-15 {label} ({where})"
            layers.append(LayerSpec(path, name, style, f"{zone_label(epsg)} (native grid)"))
    else:
        from . import gdalsafe                # needs GDAL: only for the warped case
        area = tiles_bbox(tiles)
        choice = crs.auto_crs(area, list(zones))
        out = os.path.join(folder, f"gem15_{band}_{_tag(tiles, product, band, 'auto')}.vrt")
        if not os.path.exists(out):                                  # L3
            gdalsafe.call("Building the warped VRT", gdalsafe.gdalops.build_vrt,
                          area, out, product=product, band_name=band, dst_crs="auto",
                          tiles=tiles, log=log)
        msgs.append("CRS: " + choice.describe())
        layers.append(LayerSpec(out, f"GEM-15 {label} ({choice.name}, {len(tiles)} tiles)",
                                style, choice.name))
    group = None
    if len(layers) > 1:
        group = f"GEM-15 {label} ({len(layers)} UTM zones)"
    return Plan(layers, tiles, msgs, group)
