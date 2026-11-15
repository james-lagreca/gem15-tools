# SPDX-License-Identifier: GPL-2.0-or-later
"""
Small helpers that keep the plugin working across QGIS 3.28 to 4.x.

QGIS moves enums around between releases (for example
QgsProcessingParameterNumber.Double became
Qgis.ProcessingNumberParameterType.Double in 3.36), and Qt6 only accepts
scoped enum names (Qt.AlignmentFlag.AlignLeft, not the short form).
`enum()` looks up the newest name first and falls back to older ones, so the
same code runs on each version without version checks.

Assumption (flagged): names given here exist in at least one of the QGIS
versions supported (3.28 and later); checked on 3.28.6 only, as QGIS 4 was
not available to test.
"""

from qgis import core as _core


def enum(*dotted):
    """First of the dotted names that resolves in qgis.core, e.g.
    enum("Qgis.ProcessingNumberParameterType.Double",
         "QgsProcessingParameterNumber.Type.Double")."""
    for name in dotted:
        obj = _core
        try:
            for part in name.split("."):
                obj = getattr(obj, part)
        except AttributeError:
            continue
        return obj
    raise AttributeError(f"None of {dotted} exists in this QGIS version")


def flags_or(*values):
    """Combine enum flags (works for sip and Python enums alike)."""
    out = values[0]
    for v in values[1:]:
        out = out | v
    return out
