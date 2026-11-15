# -*- coding: utf-8 -*-
"""
GEM15Tools.pyt - ArcGIS Pro Python toolbox for Google GEM-15 (15 m) elevation.

Tool: "Add GEM-15 For Extent"
    Finds every GEM-15 1 x 1 degree cell that intersects an extent (for
    example the current map view) using the public STAC catalogue, then either
      (a) Mosaic dataset: builds, or extends, a mosaic dataset that
          REFERENCES the cloud COGs through your .acs connection. You get one
          seamless layer and nothing is downloaded. Needs a Standard or
          Advanced licence (Create Mosaic Dataset / Add Rasters To Mosaic
          Dataset are not available at Basic).
      (b) Separate layers: adds each tile as its own layer (any licence).
    Run it again with a new extent and the same mosaic name to add more tiles;
    tiles already present are skipped.

Install: Catalog pane > Toolboxes > right-click > Add Toolbox > this file.
Keep the "gem15core" folder next to this file: the toolbox imports it.
ArcGIS Pro keeps imported modules for the whole session, so restart Pro
after updating the toolbox folder.

Assumptions (flagged; verify if something looks wrong):
 A1. Your .acs connection points at bucket "climate-and-sustainability"
     with folder "gem-15". If it was made without the folder, the tool
     detects that and adds "gem-15" to the paths itself.
 A2. Catalogue layout, names and asset URLs as checked on 2026-11-15 in
     both hemispheres (see gem15core.cells and gem15core.stac):
     https://storage.googleapis.com/climate-and-sustainability/gem-15/
         <group>/<cell>/<dtm|dsm>/zall/mosaic.tif
 A3. Mosaic datasets should honour the COGs' NaN nodata, so overlapping
     tiles fill each other's gaps. Not tested. If you see steps along tile
     overlaps, try Mosaic Method = Closest to Center on the layer's Data tab
     before reading them as landforms.
 A4. No overviews, pyramids or statistics are built (that would read the
     data), so display relies on the COGs' internal overviews.
 A5. The extent is converted to WGS84 lon/lat; extents crossing the
     antimeridian are not handled.
 A6. ArcGIS will not add rasters that fall outside the valid extent of the
     mosaic dataset's coordinate system (reported on Esri Community as the
     cause of a bare ERROR 999999). A UTM mosaic therefore only accepts tiles
     in or next to its own zone (+/-1 zone tolerance is my assumption).
     Zones come from each tile's own EPSG code in the catalogue. For a mosaic
     that can grow anywhere in Australia use a continental projected CRS,
     e.g. GDA2020 / GA LCC (EPSG 7845).
"""

import os
import re
import sys

import arcpy

_here = os.path.dirname(os.path.abspath(__file__))
if _here not in sys.path:
    sys.path.insert(0, _here)

from gem15core import Gem15Error  # noqa: E402
from gem15core import crs as gcrs  # noqa: E402
from gem15core import extent as gext  # noqa: E402
from gem15core import paths as gpaths  # noqa: E402
from gem15core import stac  # noqa: E402

DEFAULT_ACS_NAME = "gem15.acs"  # looked for in the project home folder
WARN_CELLS = 60                 # warn above this many 1-degree cells
PAD_DEG = 0.005                 # pad the extent slightly (~500 m) after projecting


# --------------------------------------------------------------------------
# Helpers (pure Python apart from extent_to_lonlat)
# --------------------------------------------------------------------------
def find_cells(bbox, product, fetch=None, collection_url=None):
    """Tiles (gem15core.stac.Tile) with a `product` asset whose cell overlaps bbox."""
    kw = {"fetch": fetch}
    if collection_url:
        kw["collection_url"] = collection_url
    return stac.find_tiles(bbox, product, **kw)


def cloud_paths(acs, asset_urls, exists):
    """Map https asset URLs to paths inside the .acs connection (A1)."""
    return gpaths.cloud_paths(acs, asset_urls, exists)


def sr_utm_zone(sr):
    """UTM zone number from a coordinate system name like 'WGS_1984_UTM_Zone_55S'; else None."""
    m = re.search(r"UTM_Zone_(\d+)", getattr(sr, "name", "") or "")
    return int(m.group(1)) if m else None


def zone_mismatch(md_zone, tile_epsgs, tolerance=1):
    """UTM zones of tiles more than `tolerance` zones from the mosaic's zone (A6)."""
    if md_zone is None:
        return []
    bad = set()
    for epsg in tile_epsgs:
        info = gcrs.utm_zone_of_epsg(epsg)
        if info is None:
            bad.add(epsg)                      # not UTM at all: cannot judge, refuse
        elif gcrs.zone_distance(info[0], md_zone) > tolerance:
            bad.add(info[0])
    return sorted(bad)


def extent_to_lonlat(ext, densify=11):
    """GPExtent value in any CRS -> padded (W, S, E, N) in degrees.

    The value a GPExtent parameter returns in a Python toolbox is not a full
    arcpy.Extent, and calling projectAs on it fails with "CreateObject error
    creating spatial reference" (Esri Community). So rebuild clean objects
    from its coordinates and coordinate-system code, and project a densified
    polygon of the box instead.
    """
    x0, y0, x1, y1 = (float(ext.XMin), float(ext.YMin), float(ext.XMax), float(ext.YMax))
    sr = getattr(ext, "spatialReference", None)
    code = int(getattr(sr, "factoryCode", 0) or 0) if sr is not None else 0
    if sr is None or code == 4326:
        w, s, e, n = x0, y0, x1, y1          # already lon/lat (or assumed to be)
    else:
        if code:
            src = arcpy.SpatialReference(code)
        else:                                # custom CRS with no code
            src = arcpy.SpatialReference()
            src.loadFromString(sr.exportToString())
        ring = gext.densified_ring(x0, y0, x1, y1, densify)  # so curved edges are covered
        poly = arcpy.Polygon(arcpy.Array([arcpy.Point(x, y) for x, y in ring]), src)
        g = poly.projectAs(arcpy.SpatialReference(4326)).extent
        w, s, e, n = g.XMin, g.YMin, g.XMax, g.YMax
    if not (-180 <= w < e <= 180 and -90 <= s < n <= 90):
        raise ValueError("Could not convert the extent to longitude/latitude; "
                         "set the extent's coordinate system or enter it in degrees.")
    return gext.pad((w, s, e, n), PAD_DEG)


# --------------------------------------------------------------------------
# Toolbox
# --------------------------------------------------------------------------
class Toolbox:
    def __init__(self):
        self.label = "GEM-15 Tools"
        self.alias = "gem15"
        self.tools = [AddGEM15ForExtent]


class AddGEM15ForExtent:
    MODES = ["Mosaic dataset", "Separate layers"]

    def __init__(self):
        self.label = "Add GEM-15 For Extent"
        self.description = ("Find the GEM-15 tiles covering an extent and add them as one "
                            "mosaic dataset (referenced from the cloud) or as separate layers.")
        self.canRunInBackground = False  # 'Separate layers' needs the active map

    def getParameterInfo(self):
        ext = arcpy.Parameter(displayName="Extent", name="extent", datatype="GPExtent",
                              parameterType="Required", direction="Input")
        acs = arcpy.Parameter(displayName="GEM-15 cloud storage connection (.acs)",
                              name="acs", datatype="DEFile",
                              parameterType="Required", direction="Input")
        acs.filter.list = ["acs"]
        prod = arcpy.Parameter(displayName="Product", name="product", datatype="GPString",
                               parameterType="Required", direction="Input")
        prod.filter.type = "ValueList"
        prod.filter.list = ["dtm", "dsm"]
        prod.value = "dtm"
        mode = arcpy.Parameter(displayName="Output", name="mode", datatype="GPString",
                               parameterType="Required", direction="Input")
        mode.filter.type = "ValueList"
        mode.filter.list = self.MODES
        mode.value = self.MODES[0]
        gdb = arcpy.Parameter(displayName="Geodatabase for mosaic dataset", name="gdb",
                              datatype="DEWorkspace", parameterType="Optional",
                              direction="Input")
        name = arcpy.Parameter(displayName="Mosaic dataset name", name="md_name",
                               datatype="GPString", parameterType="Optional",
                               direction="Input")
        name.value = "GEM15_dtm"
        sr = arcpy.Parameter(displayName="Mosaic dataset coordinate system", name="sr",
                             datatype="GPSpatialReference", parameterType="Optional",
                             direction="Input")
        out = arcpy.Parameter(displayName="Output mosaic dataset", name="out_md",
                              datatype="DEMosaicDataset", parameterType="Derived",
                              direction="Output")
        try:  # sensible defaults from the open project
            aprx = arcpy.mp.ArcGISProject("CURRENT")
            gdb.value = aprx.defaultGeodatabase
            cand = os.path.join(aprx.homeFolder, DEFAULT_ACS_NAME)
            if os.path.exists(cand):
                acs.value = cand
            if aprx.activeMap is not None:
                sr.value = aprx.activeMap.spatialReference
        except Exception:
            pass
        return [ext, acs, prod, mode, gdb, name, sr, out]

    def isLicensed(self):
        return True

    def updateParameters(self, p):
        is_md = p[3].valueAsText == self.MODES[0]
        for i in (4, 5, 6):
            p[i].enabled = is_md
        # Keep the default mosaic name in step with the product, unless the
        # user has typed their own name.
        if p[2].valueAsText and (not p[5].valueAsText
                                 or p[5].valueAsText in ("GEM15_dtm", "GEM15_dsm")):
            p[5].value = f"GEM15_{p[2].valueAsText}"

    def updateMessages(self, p):
        if p[3].valueAsText == self.MODES[0]:
            if arcpy.ProductInfo() == "ArcView":
                p[3].setErrorMessage("Mosaic datasets need a Standard or Advanced licence; "
                                     "choose 'Separate layers'.")
            for i in (4, 5, 6):
                if not p[i].value:
                    p[i].setErrorMessage("Required for a mosaic dataset.")
            sr = p[6].value
            if sr is not None and getattr(sr, "type", "") == "Geographic":
                p[6].setWarningMessage(
                    "Geographic (degree) coordinates make hillshade/slope z-scaling "
                    "wrong. Use a projected CRS in metres, ideally the tiles' own "
                    "WGS 1984 UTM zone (e.g. zone 55S for Victoria), so no resampling occurs.")

    def execute(self, p, messages):
        product = p[2].valueAsText
        acs = p[1].valueAsText
        bbox = extent_to_lonlat(p[0].value)
        arcpy.AddMessage("Extent (lon/lat): " + ", ".join(f"{v:.4f}" for v in bbox))

        try:
            tiles = find_cells(bbox, product)
        except Gem15Error as e:
            arcpy.AddError(str(e))
            return
        if not tiles:
            arcpy.AddError("No GEM-15 cells intersect this extent (check coverage).")
            return
        arcpy.AddMessage(f"{len(tiles)} cell(s): " + ", ".join(t.item_id for t in tiles))
        if len(tiles) > WARN_CELLS:
            arcpy.AddWarning(f"{len(tiles)} cells is a large area; display will be slow "
                             "when zoomed out.")

        paths, kept = cloud_paths(acs, [t.asset(product).href for t in tiles], arcpy.Exists)
        if not arcpy.Exists(paths[0]):
            arcpy.AddWarning(f"Could not confirm {paths[0]} exists; check the .acs "
                             "bucket/folder settings (A1). Continuing anyway.")
        if kept:
            arcpy.AddMessage("Connection has no 'gem-15' folder set; added it to paths.")

        if p[3].valueAsText == self.MODES[1]:  # separate layers
            m = arcpy.mp.ArcGISProject("CURRENT").activeMap
            if m is None:
                arcpy.AddError("Open a map first.")
                return
            for t, path in zip(tiles, paths):
                lyr = m.addDataFromPath(path)
                lyr.name = f"{t.item_id} {product}"
            return

        gdb, name, sr = p[4].valueAsText, p[5].valueAsText, p[6].value
        md = os.path.join(gdb, name)
        exists = arcpy.Exists(md)
        md_sr = arcpy.Describe(md).spatialReference if exists else sr
        epsgs = [t.epsg for t in tiles]
        bad = zone_mismatch(sr_utm_zone(md_sr), epsgs)
        if bad:
            try:
                hint = " Suggested for this extent: " + gcrs.auto_crs(bbox, epsgs).name + "."
            except Gem15Error:
                hint = ""
            arcpy.AddError(
                f"'{name}' is in UTM zone {sr_utm_zone(md_sr)}, but these tiles are in "
                f"zone(s) {bad}; ArcGIS cannot add rasters outside a UTM zone's valid "
                "extent (A6). Use a new mosaic name with a CRS that covers them all, "
                "such as GDA2020 / GA LCC (EPSG 7845) for one mosaic covering all of "
                "Australia." + hint)
            return
        if not exists:
            arcpy.management.CreateMosaicDataset(
                gdb, name, sr, 1 if product == "dtm" else 5, "32_BIT_FLOAT")
        else:
            arcpy.AddMessage(f"{name} exists; adding new tiles only.")
        arcpy.management.AddRastersToMosaicDataset(
            md, "Raster Dataset", paths,
            update_cellsize_ranges="UPDATE_CELL_SIZES",
            update_boundary="UPDATE_BOUNDARY",
            update_overviews="NO_OVERVIEWS",
            duplicate_items_action="EXCLUDE_DUPLICATES",
            build_pyramids="NO_PYRAMIDS",
            calculate_statistics="NO_STATISTICS",
            build_thumbnails="NO_THUMBNAILS",
            estimate_statistics="ESTIMATE_STATISTICS")
        p[7].value = md
