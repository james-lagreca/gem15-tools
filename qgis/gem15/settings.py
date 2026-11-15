# SPDX-License-Identifier: GPL-2.0-or-later
"""
Plugin settings, limits and the folder where VRTs are written.

Choices (flagged; change here if they do not suit):
 P1. "For current view" refuses views covering more than MAX_VIEW_CELLS
     1 x 1 degree cells (6 x 6 degrees), before making any request. Each
     visible tile is opened over the network, and a lookup of a huge view
     fetches hundreds of catalogue documents. The Processing algorithms
     refuse more than MAX_PROCESSING_CELLS (about Australia's bounding box,
     as in the ArcGIS toolbox), also before any request, and can be
     cancelled while the catalogue is read.
 P2. VRTs go to, in order: the folder set in the plugin's settings
     (gem15/output_folder); "<project folder>/<project name>_gem15" when the
     project has been saved; else a per-user folder in the QGIS profile,
     which outlives the session so a project saved later still finds them.
     VRTs are a few kB of XML each; they hold no elevation data.
 P3. The live LRM layer is drawn only at 1:LRM_MAX_SCALE and closer. GDAL
     applies a VRT kernel filter only to full-resolution reads; zoomed out
     it would show raw elevation instead (checked on GDAL 3.6.4 and 3.9.2).
     At 1:40,000 a screen pixel is 10.6 m at 96 dpi (finer than the 15 m
     grid down to 68 dpi), so QGIS always reads native pixels.
 P4. The LRM colour ramp spans +/- LRM_DISPLAY_RANGE_M, blue (low) to red
     (high); values beyond take the end colours.
"""

import os

from qgis.core import QgsApplication, QgsProject, QgsSettings

KEY = "gem15/"
MAX_VIEW_CELLS = 36
MAX_PROCESSING_CELLS = 1500
LRM_MAX_SCALE = 40000.0
LRM_DISPLAY_RANGE_M = 2.5


def value(name, default=None, type_=str):
    """Read a plugin setting (QgsSettings, key gem15/<name>)."""
    v = QgsSettings().value(KEY + name, default)
    if v is None:
        return default
    if type_ is bool:
        return v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes")
    try:
        return type_(v)
    except (TypeError, ValueError):
        return default


def set_value(name, v):
    QgsSettings().setValue(KEY + name, v)


def output_folder(project=None):
    """Folder for the plugin's VRTs (P2); created if missing."""
    project = project or QgsProject.instance()
    folder = value("output_folder", "")
    if not folder:
        home = project.absolutePath()            # "" until the project is saved
        if home and project.baseName():
            folder = os.path.join(home, project.baseName() + "_gem15")
        else:
            folder = os.path.join(QgsApplication.qgisSettingsDirPath(), "gem15tools", "vrt")
    os.makedirs(folder, exist_ok=True)
    return folder
