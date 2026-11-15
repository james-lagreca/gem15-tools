# SPDX-License-Identifier: GPL-2.0-or-later
"""
gem15core - shared, dependency-free core of the GEM-15 tools.

Used by the ArcGIS Pro toolbox, the QGIS plugin and the command-line VRT
builder. Every module here uses the Python standard library only and runs on
Python 3.9 or later (QGIS 3.28 ships 3.9), except `gdalops`, which needs the
GDAL Python bindings, and `profile`, which needs numpy; both ship with ArcGIS
Pro and QGIS and are only imported by code that needs them.

Modules
-------
cells    : parse and format GEM-15 group and cell names
extent   : lon/lat bounding-box helpers, including the antimeridian
stac     : find the tiles for an area in the public STAC catalogue (cached)
paths    : asset URL -> GDAL /vsicurl/ path or ArcGIS .acs path
crs      : UTM helpers and the automatic choice of a projected CRS
terrain  : default settings for hillshade, slope and local relief
vrt      : write a GDAL VRT straight from catalogue metadata (no GDAL needed)
download : estimate how much a clip will download, before reading anything
gdalops  : GDAL-based warp and clip (optional; needs osgeo)
profile  : swath profiles across a scarp and scarp height (optional; needs numpy)

Copies of this package live inside the ArcGIS toolbox and QGIS plugin
folders. Edit `core/gem15core/` only, then run `python tools/vendor_core.py`.
"""

__version__ = "0.1.0"           # keep in step with qgis/gem15/metadata.txt

# Where the data live (checked against the live catalogue on 2026-11-15).
BUCKET = "climate-and-sustainability"
BUCKET_URL = "https://storage.googleapis.com/climate-and-sustainability/"
DATA_FOLDER = "gem-15"
STAC_COLLECTION_URL = BUCKET_URL + "gem-15/stac/gem-15/collection.json"

# Grid facts: every tile is float32 at 15 m on a WGS 84 / UTM grid whose
# origin is a multiple of 15 m, with NaN as nodata. Tile sizes vary (8192 x
# 8192 and 8192 x 16384 px seen), so always use the item's proj:shape. Tiles
# are larger than their 1-degree cell, so neighbours overlap.
PIXEL_M = 15.0
COG_BLOCK_PX = 512          # internal tile size of the COGs (checked on three tiles)
PRODUCTS = ("dtm", "dsm")
DSM_BANDS = ("dsm", "dsm_percentile_5", "dsm_percentile_50",
             "dsm_percentile_95", "dsm_std")
DTM_BANDS = ("dtm",)

# Heights, licence and how to credit the data.
VERTICAL_DATUM = "EGM96 geoid (EPSG:5773)"
DATA_LICENCE = "CC-BY-4.0"
ATTRIBUTION = "This dataset is produced by Google"
CITATION = ("Batchu et al. (2026), GEM-15, EarthArXiv preprint, "
            "doi:10.31223/X55N75")
CITATION_URL = "https://doi.org/10.31223/X55N75"
MAP_CREDIT = "Elevation: GEM-15, produced by Google (CC BY 4.0)"

# Sent as the HTTP User-Agent. Deliberately generic: no user details.
USER_AGENT = "gem15-tools/" + __version__

# Shown where a user might be misled (tool messages, layer descriptions).
CAVEAT = ("GEM-15's DTM is predicted by machine learning: unreliable under trees and "
          "buildings. Straight features along tile edges or UTM zone boundaries may be "
          "seams, not scarps. Heights are on the EGM96 geoid, not AHD.")


class Gem15Error(Exception):
    """A problem a user can act on (bad extent, no coverage, network failure)."""
