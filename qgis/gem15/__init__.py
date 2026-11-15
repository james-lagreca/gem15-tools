# SPDX-License-Identifier: GPL-2.0-or-later
"""
GEM-15 Elevation: a QGIS plugin that streams Google's GEM-15 15 m global
elevation (DTM and DSM) from its public Cloud-Optimised GeoTIFFs, for
mapping active faults without downloading tiles.

QGIS 3.28 or later (Python 3.9+). All Qt access goes through qgis.PyQt with
scoped enum names, so the same code is meant to run on Qt6 builds (QGIS 4);
that is untested (see the README's list). The shared, dependency-free core is
vendored in ./gem15core: edit core/gem15core in the repository instead, then
run tools/vendor_core.py.

Data: CC-BY 4.0, "This dataset is produced by Google".
"""


def classFactory(iface):
    """QGIS calls this to create the plugin when it is enabled."""
    from .plugin import Gem15Plugin
    return Gem15Plugin(iface)
