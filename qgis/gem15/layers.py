# SPDX-License-Identifier: GPL-2.0-or-later
"""
Styled QGIS raster layers for GEM-15 VRTs.

Styles (flagged; defaults shared with the ArcGIS tools via gem15core.terrain):
 R1. "hillshade": QgsHillshadeRenderer, multidirectional, sun altitude 30
     degrees, z factor 1 (x, y and z all in metres). QGIS computes it from
     whatever resolution it reads, so zoomed out it shades the COG overviews.
 R2. "lrm": blue-white-red pseudocolour over +/- settings.LRM_DISPLAY_RANGE_M,
     shown only at 1:settings.LRM_MAX_SCALE and closer (lrm_vrt L3).
 R3. Layers carry the GEM-15 credit, licence, citation and the caveat about
     the data in their metadata (Layer Properties > Metadata), as CC-BY 4.0
     asks and so the caveat travels with the layer.
 R4. "canopy": green, half transparent, where DSM minus DTM exceeds
     terrain.CANOPY_THRESHOLD_M; elsewhere transparent. Drawn at
     1:LRM_MAX_SCALE and closer, as it streams the DSM.
 R5. "spread" (the DSM's dsm_std band, the spread of surface heights in a
     pixel): white (0 m) to purple (5 m or more). It is not a surface, so it
     is not hillshaded.
 R6. "edges": the tiles' true outlines, dashed, labelled with cell and zone:
     straight features on them may be seams, not scarps.
"""

from qgis.core import (QgsColorRampShader, QgsHillshadeRenderer, QgsLayerMetadata,
                       QgsPalLayerSettings, QgsRasterLayer, QgsRasterShader,
                       QgsSingleBandPseudoColorRenderer, QgsSymbol, QgsVectorLayer,
                       QgsVectorLayerSimpleLabeling)
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor

from . import settings
from .compat import enum
from .gem15core import (ATTRIBUTION, CAVEAT, CITATION, DATA_LICENCE, MAP_CREDIT,
                        VERTICAL_DATUM, Gem15Error, terrain)


def hillshade_renderer(layer, band=1, azimuth=None, altitude=terrain.HILLSHADE_ALTITUDE,
                       z_factor=terrain.HILLSHADE_Z_FACTOR):
    """Hillshade renderer (R1): multidirectional, or one sun at `azimuth`.
    The sun and z factor can be changed live in Layer Properties > Symbology."""
    r = QgsHillshadeRenderer(layer.dataProvider(), band,
                             terrain.HILLSHADE_AZIMUTH if azimuth is None else float(azimuth),
                             float(altitude))
    r.setMultiDirectional(azimuth is None)
    r.setZFactor(float(z_factor))
    return r


def _pseudocolour(layer, items, interpolated=True, band=1):
    """Single-band pseudocolour renderer from (value, QColor, label) items."""
    ramp = QgsColorRampShader(items[0][0], items[-1][0] if interpolated else items[0][0])
    ramp.setColorRampType(enum(
        "Qgis.ShaderInterpolationMethod.Linear" if interpolated
        else "Qgis.ShaderInterpolationMethod.Discrete",
        "QgsColorRampShader.Type.Interpolated" if interpolated
        else "QgsColorRampShader.Type.Discrete"))
    ramp.setColorRampItemList([QgsColorRampShader.ColorRampItem(v, c, t) for v, c, t in items])
    ramp.setClip(False)
    shader = QgsRasterShader()
    shader.setRasterShaderFunction(ramp)
    r = QgsSingleBandPseudoColorRenderer(layer.dataProvider(), band, shader)
    r.setClassificationMin(items[0][0])
    r.setClassificationMax(items[-1][0] if interpolated else items[0][0])
    return r


def canopy_renderer(layer):
    """Green where DSM - DTM > the threshold, transparent elsewhere (R4)."""
    t = terrain.CANOPY_THRESHOLD_M
    return _pseudocolour(layer, [
        (t, QColor(0, 0, 0, 0), f"under {t:g} m: bare ground"),
        (float("inf"), QColor(27, 158, 119, 255),
         f"over {t:g} m: trees or buildings; the DTM is a guess"),
    ], interpolated=False)


def spread_renderer(layer):
    """White (0 m) to purple (5 m) for the DSM's dsm_std band (R5)."""
    return _pseudocolour(layer, [(0.0, QColor(255, 255, 255), "0 m"),
                                 (5.0, QColor(84, 39, 143), "5 m or more")])


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
        f"GeoTIFFs. Heights: {VERTICAL_DATUM}. {extra} {CAVEAT}".strip())
    md.setRights([MAP_CREDIT, ATTRIBUTION, f"Licence: {DATA_LICENCE}", f"Cite: {CITATION}"])
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
        renderer = lrm_renderer(layer)
        renderer.setOpacity(settings.LRM_OPACITY)       # the hillshade below shows through
        layer.setRenderer(renderer)
        layer.setScaleBasedVisibility(True)
        layer.setMinimumScale(settings.LRM_MAX_SCALE)   # most zoomed-out scale shown
        layer.setMaximumScale(0.0)
        credit(layer, "Local relief model (elevation minus its local mean), computed "
                      "live; drawn only at 1:{:,.0f} and closer.".format(settings.LRM_MAX_SCALE))
    elif style == "canopy":
        renderer = canopy_renderer(layer)
        renderer.setOpacity(0.5)
        layer.setRenderer(renderer)
        layer.setScaleBasedVisibility(True)
        layer.setMinimumScale(settings.LRM_MAX_SCALE)
        layer.setMaximumScale(0.0)
        credit(layer, f"Canopy check: DSM minus DTM over {terrain.CANOPY_THRESHOLD_M:g} m, "
                      "where the DTM is the model's guess at the ground.")
    elif style == "spread":
        layer.setRenderer(spread_renderer(layer))
        credit(layer, "Spread (standard deviation) of surface heights within each pixel.")
    elif style == "elevation":
        credit(layer)
    else:
        raise Gem15Error(f"Unknown style {style!r}")
    return layer


def make_edges_layer(gpkg_path, layer_name="tile_edges"):
    """The tile-edges layer from its GeoPackage, styled (R6)."""
    layer = QgsVectorLayer(f"{gpkg_path}|layername={layer_name}",
                           "GEM-15 tile edges (straight features on these may be seams)", "ogr")
    if not layer.isValid():
        raise Gem15Error(f"QGIS could not open {gpkg_path}")
    sym = QgsSymbol.defaultSymbol(layer.geometryType())
    sl = sym.symbolLayer(0)
    sl.setFillColor(QColor(0, 0, 0, 0))
    sl.setStrokeColor(QColor(217, 95, 2))
    sl.setStrokeWidth(0.5)
    sl.setStrokeStyle(Qt.PenStyle.DashLine)
    layer.renderer().setSymbol(sym)
    pal = QgsPalLayerSettings()
    pal.fieldName = "cell_id || ' (' || utm_zone || ')'"
    pal.isExpression = True
    layer.setLabeling(QgsVectorLayerSimpleLabeling(pal))
    layer.setLabelsEnabled(True)
    return layer
