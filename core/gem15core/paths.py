# SPDX-License-Identifier: GPL-2.0-or-later
"""
Turn asset URLs into paths that GDAL, QGIS and ArcGIS Pro can open.

 - GDAL / QGIS: "/vsicurl/https://..." streams the COG with HTTP range reads.
 - ArcGIS Pro cannot open a COG from an https URL, so it goes through a cloud
   storage connection file (.acs): <conn.acs>/<group>/<cell>/<product>/zall/mosaic.tif
   when the connection's folder is "gem-15" (assumption P1).

Assumption P1: the .acs connection points at bucket "climate-and-sustainability".
If it was made with folder "gem-15", paths start at the group; if it was made
with no folder, they start with "gem-15". `cloud_paths` tests which by asking
whether the first path exists.
"""

import os
import urllib.parse
import urllib.request

from . import BUCKET_URL, Gem15Error


def gdal_path(href):
    """Asset href -> path for GDAL: /vsicurl/ for http(s), a local path for file://."""
    if href.startswith(("http://", "https://")):
        return "/vsicurl/" + href
    if href.startswith("file:"):
        return urllib.request.url2pathname(urllib.parse.urlparse(href).path)
    return href


def bucket_parts(url):
    """'https://storage.googleapis.com/climate-and-sustainability/gem-15/a/b.tif'
    -> ['gem-15', 'a', 'b.tif']."""
    if not url.startswith(BUCKET_URL):
        raise Gem15Error(f"Unexpected asset URL (has the catalogue moved?): {url}")
    return url[len(BUCKET_URL):].split("/")


def cloud_paths(acs, asset_urls, exists):
    """Map https asset URLs to paths inside an .acs connection (P1).

    exists : callable path -> bool (arcpy.Exists in ArcGIS).
    Returns (paths, folder_added): folder_added is True when the connection
    has no folder set, so "gem-15" was put in front of every path.
    """
    rels = [bucket_parts(u) for u in asset_urls]
    if not rels:
        return [], False

    def build(parts, keep_folder):
        return os.path.join(acs, *(parts if keep_folder else parts[1:]))

    keep = not exists(build(rels[0], False)) and exists(build(rels[0], True))
    return [build(r, keep) for r in rels], keep
