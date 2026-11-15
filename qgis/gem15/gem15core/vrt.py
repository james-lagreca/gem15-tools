# SPDX-License-Identifier: GPL-2.0-or-later
"""
Write a GDAL VRT over GEM-15 tiles straight from catalogue metadata.

No GDAL is needed, and building the VRT reads nothing from the remote COGs:
each tile's grid comes from its STAC proj:shape / proj:transform. QGIS, GDAL
or ArcGIS then stream only the blocks and overview levels they display.

Behaviour (flagged):
 V1. One VRT holds tiles of a single UTM zone, on their native 15 m grid, so
     no pixel is resampled. Tiles in other zones need their own VRT, or a warp
     (see gdalops).
 V2. Tiles overlap (each is larger than its 1-degree cell). Each source is a
     ComplexSource with NaN as its nodata, so a NaN pixel never covers a valid
     one. Where two tiles both have valid data the later tile in the list
     wins (tiles come sorted by cell id). If they disagree (e.g. different
     acquisition dates) a straight step can appear along the overlap edge:
     do not map it as a scarp.
 V3. Every source declares its size, type and 512 px block size
     (SourceProperties), so GDAL opens a remote COG only when it needs pixels
     from it, rather than all of them when the VRT is opened.
 V4. The DSM COGs store their 5 bands pixel-interleaved, so streaming one DSM
     band moves about 5 times the data of the DTM.
 V5. Reads at reduced resolution go to the COGs' internal overviews through
     the VRT (checked live: a whole 8192 x 16384 tile read into 256 x 256 px
     downloaded 0.15 MB). A full-resolution 512 px block of DTM is ~0.9 MB.
"""

import math
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Tuple

from . import (ATTRIBUTION, CITATION, COG_BLOCK_PX, DATA_LICENCE, PIXEL_M,
               VERTICAL_DATUM, Gem15Error, crs, paths)


@dataclass
class VrtInfo:
    path: str
    epsg: int
    cols: int
    rows: int
    sources: int
    bounds: Tuple[float, float, float, float]   # xmin, ymin, xmax, ymax (m)

    @property
    def uncompressed_mb(self):
        """Size of the whole VRT area as float32, i.e. a clip before compression."""
        return self.cols * self.rows * 4 / 1e6


def _on_grid(v, px):
    return abs(v / px - round(v / px)) < 1e-6


def snap_bounds(bounds, px=PIXEL_M):
    """Grow (xmin, ymin, xmax, ymax) outwards to whole multiples of px."""
    xmin, ymin, xmax, ymax = bounds
    return (px * math.floor(xmin / px + 1e-9), px * math.floor(ymin / px + 1e-9),
            px * math.ceil(xmax / px - 1e-9), px * math.ceil(ymax / px - 1e-9))


def write_vrt(tiles, product, out_path, band_name=None, bounds=None,
              pixel_m=PIXEL_M, path_for=paths.gdal_path):
    """Write a single-band VRT mosaic of `tiles` (all one UTM zone, V1).

    product   : "dtm" or "dsm"
    band_name : dsm only: dsm, dsm_percentile_5/50/95 or dsm_std (default: product)
    bounds    : optional (xmin, ymin, xmax, ymax) in the tiles' CRS, snapped
                outwards to the 15 m grid; default is the union of the tiles.
    path_for  : href -> path GDAL can open (default /vsicurl/ for https).
    Returns a VrtInfo.
    """
    if not tiles:
        raise Gem15Error("No tiles to put in the VRT.")
    epsgs = sorted({t.epsg for t in tiles})
    if len(epsgs) != 1:
        raise Gem15Error(f"Tiles are in several UTM zones {epsgs}; write one VRT per "
                         "zone, or warp them to one CRS (V1).")
    epsg = epsgs[0]
    band_name = band_name or product
    px = float(pixel_m)

    # Check every tile is on the shared 15 m grid, and collect footprints.
    grids = []
    for t in tiles:
        a = t.asset(product)
        a.band_index(band_name)                      # raises if no such band
        if a.epsg != epsg:
            raise Gem15Error(f"{t.cell_id}: asset EPSG {a.epsg} differs from item {epsg}")
        left, _, _, top = a.footprint()
        ta, _, tc, _, te, tf = a.transform
        if abs(ta - px) > 1e-9 or abs(te + px) > 1e-9 or not (
                _on_grid(tc, px) and _on_grid(tf, px)):
            raise Gem15Error(f"{t.cell_id} is not on the {px:g} m grid "
                             f"(transform {a.transform}); the catalogue may have changed.")
        grids.append((t, a, left, top))

    if bounds is None:
        fps = [g[1].footprint() for g in grids]
        bounds = (min(f[0] for f in fps), min(f[1] for f in fps),
                  max(f[2] for f in fps), max(f[3] for f in fps))
    xmin, ymin, xmax, ymax = snap_bounds(bounds, px)
    cols, rows = int(round((xmax - xmin) / px)), int(round((ymax - ymin) / px))
    if cols <= 0 or rows <= 0:
        raise Gem15Error(f"Empty VRT bounds {bounds}")

    root = ET.Element("VRTDataset", rasterXSize=str(cols), rasterYSize=str(rows))
    srs = ET.SubElement(root, "SRS", dataAxisToSRSAxisMapping="1,2")
    srs.text = crs.utm_wkt(epsg)
    ET.SubElement(root, "GeoTransform").text = (
        f"{xmin!r}, {px!r}, 0.0, {ymax!r}, 0.0, {-px!r}")
    md = ET.SubElement(root, "Metadata")
    for k, v in (("SOURCE", "GEM-15 15 m elevation (Google)"),
                 ("ATTRIBUTION", ATTRIBUTION), ("LICENCE", DATA_LICENCE),
                 ("CITATION", CITATION), ("VERTICAL_DATUM", VERTICAL_DATUM),
                 ("PRODUCT", product), ("BAND", band_name)):
        ET.SubElement(md, "MDI", key=k).text = v

    band = ET.SubElement(root, "VRTRasterBand", dataType="Float32", band="1")
    ET.SubElement(band, "Description").text = f"GEM-15 {band_name} (m, EGM96)"
    ET.SubElement(band, "NoDataValue").text = "nan"
    ET.SubElement(band, "UnitType").text = "m"

    n_sources = 0
    for t, a, left, top in grids:
        trows, tcols = a.shape
        col0 = int(round((left - xmin) / px))        # tile origin in VRT pixels
        row0 = int(round((ymax - top) / px))
        dx0, dx1 = max(0, col0), min(cols, col0 + tcols)
        dy0, dy1 = max(0, row0), min(rows, row0 + trows)
        if dx1 <= dx0 or dy1 <= dy0:
            continue                                 # tile entirely outside bounds
        src = ET.SubElement(band, "ComplexSource")
        ET.SubElement(src, "SourceFilename", relativeToVRT="0").text = path_for(a.href)
        ET.SubElement(src, "SourceBand").text = str(a.band_index(band_name))
        ET.SubElement(src, "SourceProperties", RasterXSize=str(tcols),
                      RasterYSize=str(trows), DataType="Float32",
                      BlockXSize=str(COG_BLOCK_PX), BlockYSize=str(COG_BLOCK_PX))
        ET.SubElement(src, "SrcRect", xOff=str(dx0 - col0), yOff=str(dy0 - row0),
                      xSize=str(dx1 - dx0), ySize=str(dy1 - dy0))
        ET.SubElement(src, "DstRect", xOff=str(dx0), yOff=str(dy0),
                      xSize=str(dx1 - dx0), ySize=str(dy1 - dy0))
        ET.SubElement(src, "NODATA").text = "nan"
        n_sources += 1

    if n_sources == 0:
        raise Gem15Error("None of the tiles overlaps the requested bounds.")
    ET.indent(root)
    out_path = os.path.abspath(out_path)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    ET.ElementTree(root).write(out_path, encoding="utf-8")
    return VrtInfo(out_path, epsg, cols, rows, n_sources, (xmin, ymin, xmax, ymax))
