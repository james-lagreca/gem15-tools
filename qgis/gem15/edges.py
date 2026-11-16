# SPDX-License-Identifier: GPL-2.0-or-later
"""
GEM-15 tile edges: the true outline of every tile the plugin has shown, in a
small GeoPackage beside the VRTs, so straight features along them can be
recognised as possible seams rather than scarps.

Uses OGR/OSR directly (no QGIS objects), so it can run in a background task.

Assumptions (flagged):
 G1. Outlines are each tile's proj:shape x proj:transform extent (larger than
     its 1-degree cell), densified and projected to WGS 84 lon/lat.
 G2. One GeoPackage per output folder ("gem15_tile_edges.gpkg", layer
     "tile_edges"); tiles already in it are not added again.
"""

import os

from .gem15core import Gem15Error, crs, stac

FILE_NAME = "gem15_tile_edges.gpkg"
LAYER = "tile_edges"


def update_tile_edges(tiles, product, folder):
    """Add the tiles' outlines (G1) to <folder>/gem15_tile_edges.gpkg (G2).
    Returns the GeoPackage path."""
    from osgeo import ogr, osr
    path = os.path.join(folder, FILE_NAME)
    wgs84 = osr.SpatialReference()
    wgs84.ImportFromEPSG(4326)
    wgs84.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    drv = ogr.GetDriverByName("GPKG")
    ds = drv.Open(path, 1) if os.path.exists(path) else drv.CreateDataSource(path)
    if ds is None:
        raise Gem15Error(f"Could not open or create {path}")
    lyr = ds.GetLayerByName(LAYER)
    if lyr is None:
        lyr = ds.CreateLayer(LAYER, wgs84, ogr.wkbPolygon)
        lyr.CreateField(ogr.FieldDefn("cell_id", ogr.OFTString))
        lyr.CreateField(ogr.FieldDefn("utm_zone", ogr.OFTString))
    have = {f.GetField("cell_id") for f in lyr}
    lyr.ResetReading()
    for t in tiles:
        if t.cell_id in have:
            continue
        epsg, ring = stac.tile_outline(t, product)
        src = osr.SpatialReference()
        src.ImportFromEPSG(int(epsg))
        src.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        ct = osr.CoordinateTransformation(src, wgs84)
        r = ogr.Geometry(ogr.wkbLinearRing)
        for x, y in ring:
            lon, lat = ct.TransformPoint(x, y)[:2]
            r.AddPoint_2D(lon, lat)
        poly = ogr.Geometry(ogr.wkbPolygon)
        poly.AddGeometry(r)
        feat = ogr.Feature(lyr.GetLayerDefn())
        feat.SetGeometry(poly)
        feat.SetField("cell_id", t.cell_id)
        feat.SetField("utm_zone", crs.zone_label(epsg))
        lyr.CreateFeature(feat)
        have.add(t.cell_id)
    ds = None
    return path
