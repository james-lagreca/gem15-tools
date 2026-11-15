# SPDX-License-Identifier: GPL-2.0-or-later
"""
Styled QGIS raster layers for GEM-15 VRTs.

Styles (flagged; defaults shared with the ArcGIS tools via gem15core.terrain):
 R1. "hillshade": QgsHillshadeRenderer, multidirectional, sun altitude 30
     degrees, z factor 1 (x, y and z all in metres). QGIS computes it from
     whatever resolution it reads, so zoomed out it shades the COG overviews.
 R2. "lrm": blue-white-red pseudocolour over +/- settings.LRM_DISPLAY_RANGE_M,
     shown only at 1:settings.LRM_MAX_SCALE and closer (lrm_vrt L3).
 R3. Layers carry the GEM-15 credit, licence and citation in their metadata
     (Layer Properties > Metadata), as CC-BY 4.0 asks.
"""

from qgis.core import (QgsColorRampShader, QgsHillshadeRenderer, QgsLayerMetadata,
                       QgsRasterLayer, QgsRasterShader, QgsSingleBandPseudoColorRenderer)
from qgis.PyQt.QtGui import QColor

from . import settings
from .compat import enum
from .gem15core import ATTRIBUTION, CITATION, DATA_LICENCE, VERTICAL_DATUM, Gem15Error, terrain


def hillshade_renderer(layer, band=1):
    """Multidirectional hillshade renderer for a layer (R1)."""
    r = QgsHillshadeRenderer(layer.dataProvider(), band, terrain.HILLSHADE_AZIMUTH,
                             terrain.HILLSHADE_ALTITUDE)
    r.setMultiDirectional(True)
    r.setZFactor(terrain.HILLSHADE_Z_FACTOR)
    return r


def lrm_renderer(layer, band=1, half_range=None):
    """Diverging pseudocolour renderer for a local relief model (R2)."""
    h = float(half_range or settings.LRM_DISPLAY_RANGE_M)
    ramp = QgsColorRampShader(-h, h)
    ramp.setColorRampType(enum("Qgis.ShaderInterpolationMethod.Linear",
                               "QgsColorRampShader.Type.Interpolated"))
    item = QgsColorRampShader.ColorRampItem
    ramp.setColorRampItemList([
        item(-h, QColor(33, 102, 172), f"{-h:g} m (lower than surroundings)"),
        item(0.0, QColor(247, 247, 247), "0 m"),
        item(h, QColor(178, 24, 43), f"+{h:g} m (higher than surroundings)"),
    ])
    ramp.setClip(False)                        # beyond the range: end colours
    shader = QgsRasterShader()
    shader.setRasterShaderFunction(ramp)
    r = QgsSingleBandPseudoColorRenderer(layer.dataProvider(), band, shader)
    r.setClassificationMin(-h)
    r.setClassificationMax(h)
    return r


def credit(layer, extra=""):
    """Put the GEM-15 credit, licence and citation in a layer's metadata (R3)."""
    md = layer.metadata() if hasattr(layer, "metadata") else QgsLayerMetadata()
    md.setTitle(layer.name())
    md.setAbstract(
        "GEM-15 15 m global elevation, streamed from Google's public Cloud-Optimised "
        f"GeoTIFFs. Heights: {VERTICAL_DATUM}. {extra}".strip())
    md.setRights([ATTRIBUTION, f"Licence: {DATA_LICENCE}", f"Cite: {CITATION}"])
    md.setLicenses([DATA_LICENCE])
    layer.setMetadata(md)


def make_layer(path, name, style):
    """Open a VRT as a styled raster layer. Raises Gem15Error if it will not open.

    Safe to call in a background thread (QgsTask.run); move the layer to the
    main thread before adding it to the project (tasks.py)."""
    layer = QgsRasterLayer(path, name, "gdal")
    if not layer.isValid():
        raise Gem15Error(f"QGIS could not open {path}: {layer.error().summary()}")
    if style == "hillshade":
        layer.setRenderer(hillshade_renderer(layer))
        credit(layer, "Shown as a multidirectional hillshade (sun altitude "
                      f"{terrain.HILLSHADE_ALTITUDE:g} degrees).")
    elif style == "lrm":
        layer.setRenderer(lrm_renderer(layer))
        layer.setScaleBasedVisibility(True)
        layer.setMinimumScale(settings.LRM_MAX_SCALE)   # most zoomed-out scale shown
        layer.setMaximumScale(0.0)
        credit(layer, "Local relief model (elevation minus its local mean), computed "
                      "live; drawn only at 1:{:,.0f} and closer.".format(settings.LRM_MAX_SCALE))
    elif style == "elevation":
        credit(layer)
    else:
        raise Gem15Error(f"Unknown style {style!r}")
    return layer
