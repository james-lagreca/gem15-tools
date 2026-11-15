#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
r"""
gem15_vrt.py - stream Google GEM-15 (15 m) elevation into QGIS or GDAL
without downloading tiles.

What it does
------------
1. Finds the GEM-15 1 x 1 degree cells that cover your area in the public
   STAC catalogue (cached locally, so repeat runs make no requests).
2. Writes a small GDAL VRT (a few kB of XML) pointing at the remote
   Cloud-Optimised GeoTIFFs via /vsicurl/. QGIS, gdaldem, rasterio and so on
   then read only the blocks and overview levels they need.
3. Optionally writes a local GeoTIFF of just the area (--clip), for analysis
   such as swath profiles where you want a fixed local copy.

Coordinate system (--dst-crs, default "auto")
--------------------------------------------
auto: tiles in one UTM zone keep their native grid (no resampling). Across
zones: Australia -> GDA2020 / GA LCC (EPSG 7845), New Zealand -> NZTM2000
(EPSG 2193), elsewhere a conformal projection centred on the area. Or give
any CRS GDAL understands, e.g. EPSG:7855, or use --per-zone for one
native-grid VRT per zone (no resampling at all).

Requirements
------------
GDAL >= 3.4 Python bindings (osgeo), as shipped with QGIS and ArcGIS Pro:
  * Windows, QGIS: the OSGeo4W Shell
        python gem15_vrt.py --bbox 172.6 -41.95 173.1 -41.65 --out wairau_dtm.vrt
  * Windows, ArcGIS Pro: the Python Command Prompt (same command)
  * Inside the QGIS Python console:
        import sys; sys.path.append(r"<folder containing this script>")
        import gem15_vrt as g
        vrt = g.build_vrt((172.6, -41.95, 173.1, -41.65),
                          r"<output folder>\wairau_dtm.vrt", product="dtm")
        g.add_to_qgis(vrt)          # adds it with a multidirectional hillshade

Data: CC-BY 4.0, "This dataset is produced by Google". Cite Batchu et al.
(2026), EarthArXiv preprint, doi:10.31223/X55N75. Heights are EGM96.
"""

import argparse
import os
import sys

# Find gem15core: ../core in the repository, or alongside this script.
_here = os.path.dirname(os.path.abspath(__file__))
for _cand in (os.path.join(_here, "..", "core"), _here):
    if os.path.isdir(os.path.join(_cand, "gem15core")):
        sys.path.insert(0, os.path.abspath(_cand))
        break

from gem15core import DSM_BANDS, STAC_COLLECTION_URL, Gem15Error, terrain  # noqa: E402
from gem15core.gdalops import build_vrt, clip_to_geotiff  # noqa: E402,F401
from gem15core.stac import Fetcher  # noqa: E402


def add_to_qgis(path, name=None, multidirectional=True,
                azimuth=terrain.HILLSHADE_AZIMUTH, altitude=terrain.HILLSHADE_ALTITUDE):
    """QGIS Python console only: add the raster with a live hillshade renderer."""
    from qgis.core import QgsHillshadeRenderer, QgsProject, QgsRasterLayer
    lyr = QgsRasterLayer(path, name or os.path.basename(path))
    if not lyr.isValid():
        raise RuntimeError(f"QGIS could not open {path}")
    r = QgsHillshadeRenderer(lyr.dataProvider(), 1, azimuth, altitude)
    r.setMultiDirectional(multidirectional)
    r.setZFactor(terrain.HILLSHADE_Z_FACTOR)
    lyr.setRenderer(r)
    QgsProject.instance().addMapLayer(lyr)
    return lyr


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0].strip())
    p.add_argument("--bbox", nargs=4, type=float, required=True,
                   metavar=("W", "S", "E", "N"),
                   help="area in decimal degrees (W > E crosses the antimeridian)")
    p.add_argument("--product", choices=["dtm", "dsm"], default="dtm")
    p.add_argument("--band", default=None, choices=DSM_BANDS,
                   help="dsm only (default dsm)")
    p.add_argument("--dst-crs", default="auto",
                   help="'auto' (default) or any GDAL CRS, e.g. EPSG:2193")
    p.add_argument("--resample", default="bilinear",
                   help="resampling when warping across zones (bilinear, cubic, ...)")
    p.add_argument("--per-zone", action="store_true",
                   help="one native-grid VRT per UTM zone, no resampling")
    p.add_argument("--out", required=True, help="output .vrt path")
    p.add_argument("--clip", default=None,
                   help="also write a local GeoTIFF of the area (not with --per-zone)")
    p.add_argument("--refresh", action="store_true",
                   help="ignore the local catalogue cache")
    p.add_argument("--stac", default=STAC_COLLECTION_URL, help=argparse.SUPPRESS)
    a = p.parse_args(argv)

    if a.per_zone and a.clip:
        p.error("--clip needs a single VRT; leave out --per-zone")
    if a.band and a.product != "dsm":
        p.error("--band applies to --product dsm only")
    try:
        out = build_vrt(tuple(a.bbox), a.out, a.product, a.band, a.dst_crs,
                        a.resample, a.per_zone, fetch=Fetcher(refresh=a.refresh),
                        collection_url=a.stac)
        if a.clip:
            clip_to_geotiff(out, a.clip)
    except Gem15Error as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
