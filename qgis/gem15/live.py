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
 L6. The canopy check is one layer per zone on the native grid; DSM and DTM
     tiles share their origin and 15 m pixels (checked on 39 cached items,
     2026-11-15; some DSM tiles have fewer rows), so VRTs over the same
     bounds line up pixel for pixel.
"""

import os
from dataclasses import dataclass
from typing import List, Optional

from .gem15core import Gem15Error, crs, stac, terrain, vrt

KINDS = {
    # kind: (product, band, style, label)
    "dtm": ("dtm", "dtm", "hillshade", "DTM hillshade"),
    "dsm": ("dsm", "dsm", "hillshade", "DSM hillshade"),
    "lrm": ("dtm", "dtm", "lrm", "DTM live LRM"),
    "canopy": ("dtm", "dtm", "canopy", "canopy check (DSM - DTM)"),
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


# Shared with the ArcGIS toolbox (moved to gem15core 2026-11-15), so both name
# and size their VRTs the same way (L1, L3).
_tag = stac.tiles_tag
tiles_bbox = stac.tiles_bbox
zone_label = crs.zone_label


def plan_layers(bbox, kind, folder, per_zone=False, fetch=None, collection_url=None,
                lrm_radius_m=terrain.LRM_RADIUS_M, log=None, is_cancelled=None):
    """Find tiles for bbox (W, S, E, N) and write the VRT(s) for `kind`
    ("dtm", "dsm", "lrm" or "canopy") into `folder`. Returns a Plan.

    "canopy" (L6) writes, per UTM zone, DSM (first band) and DTM VRTs over
    the same bounds and a VRT of their difference, styled to mark where the
    DTM is a guess under trees or buildings (gem15core.terrain T7). It
    streams the DSM: about 5 times the data of the DTM.
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
    tiles = stac.find_tiles(bbox, None if style == "canopy" else product, **kw)
    if style == "canopy":
        tiles = [t for t in tiles if "dtm" in t.assets]
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

    def canopy(epsg, ztiles):
        """(diff VRT path) for one zone: DSM and DTM on the DTM tiles' bounds (L6)."""
        both = [t for t in ztiles if "dsm" in t.assets]
        if not both:
            raise Gem15Error("No GEM-15 DSM tiles here, so no canopy check.")
        fps = [t.asset("dtm").footprint() for t in both]
        bounds = (min(f[0] for f in fps), min(f[1] for f in fps),
                  max(f[2] for f in fps), max(f[3] for f in fps))
        tag = _tag(both, "canopy", "dsm-dtm")
        paths = {}
        for prod in ("dsm", "dtm"):
            paths[prod] = os.path.join(folder, f"gem15_{prod}_{tag}_epsg{epsg}.vrt")
            if not os.path.exists(paths[prod]):                      # L3
                vrt.write_vrt(both, prod, paths[prod], prod, bounds=bounds)
        out = os.path.join(folder, f"gem15_canopy_{tag}_epsg{epsg}.vrt")
        if not os.path.exists(out):
            lrm_vrt.write_diff_vrt(paths["dsm"], paths["dtm"], out)
        return out

    layers = []
    if style == "canopy":                                            # L6, per zone
        for epsg, ztiles in zones.items():
            where = f"{zone_label(epsg)}, {len(ztiles)} tile(s)"
            layers.append(LayerSpec(canopy(epsg, ztiles), f"GEM-15 {label} ({where})", style,
                                    f"{zone_label(epsg)} (native grid)"))
        msgs.append(f"The canopy check marks where the DSM is over "
                    f"{terrain.CANOPY_THRESHOLD_M:g} m above the DTM: trees or buildings, where "
                    "the DTM is the model's guess. It streams the DSM (5 times the data).")
    elif len(zones) == 1 or per_zone or style == "lrm":              # L2
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
