# SPDX-License-Identifier: GPL-2.0-or-later
"""
Processing algorithms: Build GEM-15 VRT, Clip GEM-15 to GeoTIFF, and
Terrain derivatives from a clip.

All three run in Processing's background thread. They never turn GDAL
exceptions on (gdalsafe S1), and they reach the network only through the
cached catalogue lookup and GDAL's range reads of the public COGs.
"""

import os
import uuid

from qgis.core import (QgsCoordinateTransform, QgsProcessingAlgorithm,
                       QgsProcessingContext, QgsProcessingException,
                       QgsProcessingLayerPostProcessorInterface,
                       QgsProcessingOutputMultipleLayers, QgsProcessingParameterBoolean,
                       QgsProcessingParameterCrs, QgsProcessingParameterEnum,
                       QgsProcessingParameterExtent, QgsProcessingParameterFeatureSource,
                       QgsProcessingParameterFileDestination,
                       QgsProcessingParameterNumber, QgsProcessingParameterRasterDestination,
                       QgsProcessingParameterRasterLayer, QgsProcessingParameterString,
                       QgsProcessingUtils, QgsRasterLayer)
from qgis.PyQt.QtCore import QCoreApplication

from . import estimate, network, settings, view
from . import layers as layers_mod
from .compat import enum
from .gem15core import (ATTRIBUTION, CITATION, DATA_LICENCE_URL, DISCLAIMER, MAP_CREDIT,
                        PIXEL_M, STAC_COLLECTION_URL, Gem15Error, crs, stac, terrain)

# (product, band, label) offered for every algorithm.
LAYER_CHOICES = [
    ("dtm", "dtm", "DTM (bare earth)"),
    ("dsm", "dsm", "DSM (surface)"),
    ("dsm", "dsm_percentile_5", "DSM 5th percentile"),
    ("dsm", "dsm_percentile_50", "DSM median (50th percentile)"),
    ("dsm", "dsm_percentile_95", "DSM 95th percentile"),
    ("dsm", "dsm_std", "DSM standard deviation"),
]
RESAMPLING = ["bilinear", "cubic", "near"]
# Labels for gem15core.terrain.ILLUMINATIONS, in the same order.
ILLUMINATIONS = ["Multidirectional", "Single sun (azimuth below)",
                 "Across a fault trend (trend below)"]
CREDIT = (f"<p>Data: GEM-15, <a href=\"{DATA_LICENCE_URL}\">CC BY 4.0</a>. \"{ATTRIBUTION}\". "
          f"Please cite {CITATION}. Heights are EGM96 orthometric (EPSG:5773).</p>"
          f"<p>{DISCLAIMER}</p>")
AUTO_CRS_HELP = (
    "<p><b>Automatic CRS</b>: tiles in one UTM zone keep their native 15 m grid (nothing "
    "is resampled). Across zones: GDA2020 / GA LCC (EPSG:7845) in Australia, NZTM2000 "
    "(EPSG:2193) in New Zealand, else a conformal projection centred on the area. GA LCC's "
    "scale error runs from -1.2 % in central Australia to about +3 % in the far north and "
    "Tasmania: fine for display, check it before measuring distances.</p>")

# Post-processors must outlive processAlgorithm (QGIS calls them afterwards
# on the main thread), so references are kept here.
_KEEP = []


def tr(text):
    return QCoreApplication.translate("GEM15", text)


class _Styler(QgsProcessingLayerPostProcessorInterface):
    """Applies a layers.py style to an output layer once it is loaded."""

    def __init__(self, style):
        super().__init__()
        self.style = style

    def postProcessLayer(self, layer, context, feedback):
        if not isinstance(layer, QgsRasterLayer):
            return
        from . import settings
        if self.style == "hillshade":
            layer.setRenderer(layers_mod.hillshade_renderer(layer))
        elif self.style == "spread":                   # dsm_std: not a surface (R5)
            layer.setRenderer(layers_mod.spread_renderer(layer))
        elif self.style == "lrm":
            renderer = layers_mod.lrm_renderer(layer)
            renderer.setOpacity(settings.LRM_OPACITY)
            layer.setRenderer(renderer)
            if layer.source().lower().endswith(".vrt"):          # live LRM: large scales only
                layer.setScaleBasedVisibility(True)
                layer.setMinimumScale(settings.LRM_MAX_SCALE)
        layers_mod.credit(layer)
        layer.triggerRepaint()


def _load(context, path, name, style=None):
    """Ask Processing to add `path` to the project when the algorithm ends."""
    context.addLayerToLoadOnCompletion(
        path, QgsProcessingContext.LayerDetails(name, context.project(), name))
    if style:
        styler = _Styler(style)
        _KEEP.append(styler)
        del _KEEP[:-50]
        context.layerToLoadOnCompletionDetails(path).setPostProcessor(styler)


def _crs_string(qcrs):
    """A CRS string GDAL understands for a QgsCoordinateReferenceSystem."""
    authid = qcrs.authid()
    if authid.upper().startswith("EPSG:"):
        return authid
    return qcrs.toWkt()


class _Base(QgsProcessingAlgorithm):
    """Shared parameters and helpers."""

    # No group: the provider is already "GEM-15", and a group of the same name
    # showed the tools under GEM-15 > GEM-15 in the Processing Toolbox.
    def group(self):
        return ""

    def groupId(self):
        return ""

    def createInstance(self):
        return type(self)()

    def _add_area_and_layer(self):
        self.addParameter(QgsProcessingParameterExtent("EXTENT", tr("Area")))
        self.addParameter(QgsProcessingParameterEnum(
            "LAYER", tr("GEM-15 layer"), options=[c[2] for c in LAYER_CHOICES], defaultValue=0))

    def _add_collection(self):
        p = QgsProcessingParameterString(
            "COLLECTION_URL", tr("STAC collection URL (for mirrors and testing)"),
            defaultValue=STAC_COLLECTION_URL)
        p.setFlags(p.flags() | enum("Qgis.ProcessingParameterFlag.Advanced",
                                    "QgsProcessingParameterDefinition.Flag.FlagAdvanced"))
        self.addParameter(p)

    def _area(self, parameters, context):
        rect = self.parameterAsExtent(parameters, "EXTENT", context)
        rcrs = self.parameterAsExtentCrs(parameters, "EXTENT", context)
        try:
            return view.lonlat_bbox(rect, rcrs, context.transformContext())
        except Gem15Error as e:
            raise QgsProcessingException(str(e))

    def _tiles(self, parameters, context, feedback, bbox, product):
        url = self.parameterAsString(parameters, "COLLECTION_URL", context) or STAC_COLLECTION_URL
        n = view.cell_count(bbox)
        feedback.pushInfo(tr(f"Area (lon/lat W, S, E, N): {', '.join(f'{v:.4f}' for v in bbox)}"))
        if n > 100:
            feedback.pushInfo(tr(f"The area spans {n} one-degree cells; the catalogue "
                                 "lookup may take a while the first time (it is cached)."))
        try:
            tiles = stac.find_tiles(
                bbox, product, collection_url=url, fetch=network.fetcher(),
                max_cells=settings.MAX_PROCESSING_CELLS, is_cancelled=feedback.isCanceled,
                progress=lambda done, total: feedback.setProgress(10.0 * done / total))
        except Gem15Error as e:
            raise QgsProcessingException(str(e))
        if not tiles:
            raise QgsProcessingException(tr("No GEM-15 tiles cover this area. GEM-15 covers "
                                            "land between about 61 S and 84 N."))
        feedback.pushInfo(tr(f"{len(tiles)} tile(s): {', '.join(t.cell_id for t in tiles)}"))
        return tiles


# --------------------------------------------------------------------------
class BuildVrtAlgorithm(_Base):
    """Build a streaming VRT (and optionally a live LRM VRT) for an area."""

    CRS_MODES = ["Automatic", "One VRT per UTM zone (no resampling)",
                 "The CRS below (resampled)"]

    def name(self):
        return "buildvrt"

    def displayName(self):
        return tr("Build GEM-15 VRT")

    def shortHelpString(self):
        return tr(
            "<p>Writes a small GDAL VRT (XML) that streams GEM-15 tiles for the area from "
            "Google's public Cloud-Optimised GeoTIFFs. Nothing is downloaded up front: QGIS "
            "reads only the blocks and overview levels it draws.</p>" + AUTO_CRS_HELP +
            "<p><b>Live LRM</b> also writes a local relief model VRT (elevation minus its "
            "mean within the radius), computed by GDAL as QGIS draws. It is drawn only at "
            "large scales (1:50,000 and closer), where QGIS reads native pixels; zoomed out "
            "GDAL would show plain elevation. Voids grow by the radius in it. A 300 m radius "
            "costs about 1 s per redraw at 1:25,000 on a full-HD screen.</p>" + CREDIT)

    def initAlgorithm(self, config=None):
        self._add_area_and_layer()
        self.addParameter(QgsProcessingParameterEnum(
            "CRS_MODE", tr("Coordinate system"), options=self.CRS_MODES, defaultValue=0))
        self.addParameter(QgsProcessingParameterCrs(
            "TARGET_CRS", tr("CRS (used with 'The CRS below')"), optional=True))
        self.addParameter(QgsProcessingParameterEnum(
            "RESAMPLING", tr("Resampling when warping"), options=RESAMPLING, defaultValue=0))
        self.addParameter(QgsProcessingParameterBoolean(
            "HILLSHADE", tr("Style the elevation layer as a multidirectional hillshade"),
            defaultValue=True))
        self.addParameter(QgsProcessingParameterBoolean(
            "LIVE_LRM", tr("Also write a live local relief model (LRM) VRT"), defaultValue=False))
        self.addParameter(QgsProcessingParameterNumber(
            "LRM_RADIUS", tr("LRM radius (m)"),
            type=enum("Qgis.ProcessingNumberParameterType.Double",
                      "QgsProcessingParameterNumber.Type.Double"),
            defaultValue=terrain.LRM_RADIUS_M, minValue=terrain.LRM_RADIUS_MIN_M))
        self._add_collection()
        self.addParameter(QgsProcessingParameterFileDestination(
            "OUTPUT", tr("VRT file"), fileFilter="GDAL VRT (*.vrt)"))
        self.addOutput(QgsProcessingOutputMultipleLayers("OUTPUT_LAYERS", tr("VRT layers")))

    def processAlgorithm(self, parameters, context, feedback):
        from . import gdalsafe, lrm_vrt
        gdalsafe.configure_remote()
        product, band, label = LAYER_CHOICES[self.parameterAsEnum(parameters, "LAYER", context)]
        mode = self.parameterAsEnum(parameters, "CRS_MODE", context)
        out = self.parameterAsFileOutput(parameters, "OUTPUT", context)
        if not out.lower().endswith(".vrt"):
            out = os.path.splitext(out)[0] + ".vrt"
        bbox = self._area(parameters, context)
        tiles = self._tiles(parameters, context, feedback, bbox, product)
        resample = RESAMPLING[self.parameterAsEnum(parameters, "RESAMPLING", context)]
        kw = dict(product=product, band_name=band, resample=resample, tiles=tiles,
                  log=feedback.pushInfo)
        if mode == 1:
            kw["per_zone"] = True
        elif mode == 2:
            target = self.parameterAsCrs(parameters, "TARGET_CRS", context)
            if not target.isValid():
                raise QgsProcessingException(tr("Choose a CRS, or use the automatic choice."))
            if target.isGeographic():
                raise QgsProcessingException(tr(
                    "The CRS uses degrees, which would give a raster one pixel wide. Choose "
                    "a projected CRS in metres, or use the automatic choice."))
            kw["dst_crs"] = _crs_string(target)
        try:
            result = gdalsafe.call("Building the VRT", gdalsafe.gdalops.build_vrt, bbox, out, **kw)
        except Gem15Error as e:
            raise QgsProcessingException(str(e))
        paths = result if isinstance(result, list) else [result]
        hill = self.parameterAsBool(parameters, "HILLSHADE", context)
        outputs = []
        style = "spread" if band == "dsm_std" else ("hillshade" if hill else "elevation")
        for p in paths:
            name = f"GEM-15 {label} ({os.path.splitext(os.path.basename(p))[0]})"
            _load(context, p, name, style)
            outputs.append(p)
        if self.parameterAsBool(parameters, "LIVE_LRM", context):
            radius = self.parameterAsDouble(parameters, "LRM_RADIUS", context)
            for p in paths:
                lp = os.path.splitext(p)[0] + f"_lrm_r{radius:g}.vrt"
                lrm_vrt.write_lrm_vrt(p, lp, radius)
                _load(context, lp, f"GEM-15 {label} live LRM {radius:g} m", "lrm")
                outputs.append(lp)
                feedback.pushInfo(tr(f"Wrote {lp}"))
        feedback.pushInfo(tr(f"Credit: {ATTRIBUTION} (CC-BY 4.0)."))
        return {"OUTPUT": paths[0], "OUTPUT_LAYERS": outputs}


# --------------------------------------------------------------------------
class ClipAlgorithm(_Base):
    """Download just the area to a compressed local GeoTIFF."""

    CRS_MODES = ["Automatic", "The CRS below (resampled)"]

    def name(self):
        return "clip"

    def displayName(self):
        return tr("Clip GEM-15 to GeoTIFF")

    def shortHelpString(self):
        return tr(
            "<p>Copies GEM-15 for the area to a local, compressed GeoTIFF for analysis "
            "(profiles, derivatives, measurements). Only the area is downloaded, in whole "
            "512 x 512 px blocks, so the download is a little larger than the clip.</p>"
            "<p>The estimated download is reported before starting. A DTM block is about "
            "0.9 MB; a DSM block about 4.5 MB, because the 5 DSM bands are stored together. "
            "The tool stops if the estimate exceeds the limit (0 = no limit).</p>"
            + AUTO_CRS_HELP + CREDIT)

    def initAlgorithm(self, config=None):
        self._add_area_and_layer()
        self.addParameter(QgsProcessingParameterEnum(
            "CRS_MODE", tr("Coordinate system"), options=self.CRS_MODES, defaultValue=0))
        self.addParameter(QgsProcessingParameterCrs(
            "TARGET_CRS", tr("CRS (used with 'The CRS below')"), optional=True))
        self.addParameter(QgsProcessingParameterEnum(
            "RESAMPLING", tr("Resampling when warping"), options=RESAMPLING, defaultValue=0))
        self.addParameter(QgsProcessingParameterNumber(
            "MAX_DOWNLOAD_MB", tr("Stop if the estimated download exceeds (MB, 0 = no limit)"),
            type=enum("Qgis.ProcessingNumberParameterType.Double",
                      "QgsProcessingParameterNumber.Type.Double"),
            defaultValue=2000.0, minValue=0.0))
        self._add_collection()
        self.addParameter(QgsProcessingParameterRasterDestination(
            "OUTPUT", tr("Clipped GeoTIFF")))

    def processAlgorithm(self, parameters, context, feedback):
        from . import gdalsafe
        gdalsafe.configure_remote()
        product, band, label = LAYER_CHOICES[self.parameterAsEnum(parameters, "LAYER", context)]
        out = self.parameterAsOutputLayer(parameters, "OUTPUT", context)
        bbox = self._area(parameters, context)
        tiles = self._tiles(parameters, context, feedback, bbox, product)
        zones = stac.group_by_epsg(tiles)
        if self.parameterAsEnum(parameters, "CRS_MODE", context) == 1:
            target = self.parameterAsCrs(parameters, "TARGET_CRS", context)
            if not target.isValid():
                raise QgsProcessingException(tr("Choose a CRS, or use the automatic choice."))
            dst = _crs_string(target)
        else:
            choice = crs.auto_crs(bbox, list(zones))
            feedback.pushInfo(tr("CRS: " + choice.describe()))
            dst = "auto"

        gdalops = gdalsafe.gdalops
        mb = estimate.download_mb(
            tiles, product, lambda epsg: gdalsafe.call(
                "Projecting the area", gdalops.aoi_bounds_in, f"EPSG:{epsg}", bbox))
        msg = tr(f"Estimated download: about {mb:,.0f} MB "
                 f"({'DSM blocks hold all 5 bands' if product == 'dsm' else 'DTM'}).")
        limit = self.parameterAsDouble(parameters, "MAX_DOWNLOAD_MB", context)
        if limit and mb > limit:
            raise QgsProcessingException(
                msg + tr(f" That is over the {limit:,.0f} MB limit: choose a smaller area or "
                         "raise the limit."))
        (feedback.pushWarning if mb > 500 else feedback.pushInfo)(msg)

        tmp_vrt = os.path.join(QgsProcessingUtils.tempFolder(), f"gem15_clip_{uuid.uuid4().hex[:8]}.vrt")
        resample = RESAMPLING[self.parameterAsEnum(parameters, "RESAMPLING", context)]
        try:
            gdalsafe.call("Building the VRT", gdalops.build_vrt, bbox, tmp_vrt, product=product,
                          band_name=band, dst_crs=dst, resample=resample, tiles=tiles,
                          log=feedback.pushInfo)

            def progress(fraction, _msg, _data):
                feedback.setProgress(100 * fraction)
                return 0 if feedback.isCanceled() else 1

            gdalsafe.call("Clipping", gdalops.clip_to_geotiff, tmp_vrt, out,
                          log=feedback.pushInfo, callback=progress)
        except Gem15Error as e:
            raise QgsProcessingException(str(e))
        if feedback.isCanceled():
            raise QgsProcessingException(tr("Cancelled; the GeoTIFF is incomplete."))
        ds = gdalsafe.gdal.Open(out)                 # clip_to_geotiff does not check the result
        if ds is None or ds.RasterXSize == 0:
            raise QgsProcessingException(tr(f"The clip was not written: "
                                             f"{gdalsafe.gdal.GetLastErrorMsg()}"))
        feedback.pushInfo(tr(f"Wrote {out} ({ds.RasterXSize} x {ds.RasterYSize} px). "
                             f"Credit: {ATTRIBUTION} (CC-BY 4.0)."))
        ds = None
        return {"OUTPUT": out}


# --------------------------------------------------------------------------
class DerivativesAlgorithm(_Base):
    """Hillshade, slope and LRM from a local DEM (e.g. a GEM-15 clip)."""

    def name(self):
        return "derivatives"

    def displayName(self):
        return tr("Terrain derivatives from a clip")

    def shortHelpString(self):
        return tr(
            "<p>Makes a multidirectional hillshade, slope in degrees and a local relief model "
            "(LRM: elevation minus its mean within the radius) from a DEM, such as a GEM-15 "
            "clip. Leave an output blank (Skip output) to skip it.</p>"
            "<p>The DEM must be in a projected CRS in metres. Hillshade and slope use GDAL "
            "(gdaldem, Horn's method). The LRM leaves voids and the raster's edge out of the "
            "mean, so voids do not grow. Use this rather than the live layers on slow "
            "connections or for print maps.</p>" + CREDIT)

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterRasterLayer("INPUT", tr("DEM (projected, metres)")))
        dbl = enum("Qgis.ProcessingNumberParameterType.Double",
                   "QgsProcessingParameterNumber.Type.Double")
        self.addParameter(QgsProcessingParameterNumber(
            "ALTITUDE", tr("Hillshade sun altitude (degrees)"), type=dbl,
            defaultValue=terrain.HILLSHADE_ALTITUDE, minValue=0.0, maxValue=90.0))
        self.addParameter(QgsProcessingParameterEnum(
            "ILLUMINATION", tr("Hillshade illumination"), options=ILLUMINATIONS,
            defaultValue=0))
        self.addParameter(QgsProcessingParameterNumber(
            "AZIMUTH", tr("Sun azimuth (degrees; for a single sun)"), type=dbl,
            defaultValue=terrain.HILLSHADE_AZIMUTH, minValue=0.0, maxValue=360.0))
        self.addParameter(QgsProcessingParameterNumber(
            "FAULT_TREND", tr("Fault trend (degrees; to light across it)"), type=dbl,
            optional=True, minValue=0.0, maxValue=360.0))
        self.addParameter(QgsProcessingParameterNumber(
            "Z_FACTOR", tr("Vertical exaggeration (z factor)"), type=dbl,
            defaultValue=terrain.HILLSHADE_Z_FACTOR, minValue=0.1, maxValue=20.0))
        self.addParameter(QgsProcessingParameterNumber(
            "LRM_RADIUS", tr("LRM radius (m): 150 narrow scarps, 300 default, 1000 broad"),
            type=dbl, defaultValue=terrain.LRM_RADIUS_M, minValue=terrain.LRM_RADIUS_MIN_M))
        for key, text in (("OUTPUT_HILLSHADE", "Hillshade"), ("OUTPUT_SLOPE", "Slope (degrees)"),
                          ("OUTPUT_LRM", "Local relief model")):
            self.addParameter(QgsProcessingParameterRasterDestination(
                key, tr(text), optional=True, createByDefault=True))

    def processAlgorithm(self, parameters, context, feedback):
        from . import derivatives
        layer = self.parameterAsRasterLayer(parameters, "INPUT", context)
        if layer is None:
            raise QgsProcessingException(tr("No input DEM."))
        if layer.crs().isGeographic():
            raise QgsProcessingException(tr(
                "The DEM is in a geographic CRS (degrees). Clip GEM-15 in its automatic CRS, "
                "or warp the DEM to a projected CRS in metres first."))
        metres = enum("Qgis.DistanceUnit.Meters", "QgsUnitTypes.DistanceUnit.DistanceMeters")
        if layer.crs().mapUnits() != metres:
            feedback.pushWarning(tr("The DEM's CRS is not in metres; results assume x, y and z "
                                    "share units."))
        src = layer.source()
        if "/vsicurl/" in src or src.lower().endswith(".vrt"):
            feedback.pushWarning(tr("The input is a VRT or remote file: it will be read over "
                                    "the network. Clip it first for speed."))
        if abs(layer.rasterUnitsPerPixelX() - layer.rasterUnitsPerPixelY()) > 1e-6 * PIXEL_M:
            feedback.pushWarning(tr("Pixels are not square; the LRM radius uses the x size."))

        jobs = [(k, self.parameterAsOutputLayer(parameters, k, context))
                for k in ("OUTPUT_HILLSHADE", "OUTPUT_SLOPE", "OUTPUT_LRM")]
        jobs = [(k, p) for k, p in jobs if p]
        if not jobs:
            raise QgsProcessingException(tr("All outputs are skipped: nothing to do."))
        results = {}
        for i, (key, path) in enumerate(jobs):
            if feedback.isCanceled():
                break

            def progress(f, i=i):
                feedback.setProgress(100 * (i + f) / len(jobs))

            try:
                if key == "OUTPUT_HILLSHADE":
                    azimuth = self._azimuth(parameters, context)
                    feedback.pushInfo(tr("Hillshade..." if azimuth is None else
                                         f"Hillshade, one sun at azimuth {azimuth:g} degrees..."))
                    derivatives.hillshade(src, path, self.parameterAsDouble(parameters, "ALTITUDE", context),
                                          self.parameterAsDouble(parameters, "Z_FACTOR", context),
                                          progress=progress, is_cancelled=feedback.isCanceled,
                                          azimuth=azimuth)
                elif key == "OUTPUT_SLOPE":
                    feedback.pushInfo(tr("Slope..."))
                    derivatives.slope(src, path, progress=progress, is_cancelled=feedback.isCanceled)
                else:
                    radius = self.parameterAsDouble(parameters, "LRM_RADIUS", context)
                    feedback.pushInfo(tr(f"Local relief model, radius {radius:g} m..."))
                    derivatives.lrm(src, path, radius, progress=progress,
                                    is_cancelled=feedback.isCanceled)
                    if context.willLoadLayerOnCompletion(path):
                        styler = _Styler("lrm")
                        _KEEP.append(styler)
                        context.layerToLoadOnCompletionDetails(path).setPostProcessor(styler)
            except Gem15Error as e:
                raise QgsProcessingException(str(e))
            results[key] = path
        return results

    def _azimuth(self, parameters, context):
        """Sun azimuth, or None for multidirectional (gem15core.terrain T1)."""
        how = terrain.ILLUMINATIONS[self.parameterAsEnum(parameters, "ILLUMINATION", context)]
        trend = (None if parameters.get("FAULT_TREND") in (None, "")
                 else self.parameterAsDouble(parameters, "FAULT_TREND", context))
        try:
            return terrain.sun_azimuth(how, self.parameterAsDouble(parameters, "AZIMUTH",
                                                                   context), trend)
        except ValueError as e:
            raise QgsProcessingException(tr(str(e)))


# --------------------------------------------------------------------------
class SwathProfileAlgorithm(_Base):
    """Swath profile across a scarp, with an optional scarp-height estimate."""

    BOUNDS = (("UPPER_FROM", "Upper side from (m along the line)"),
              ("UPPER_TO", "Upper side to (m)"),
              ("LOWER_FROM", "Lower side from (m)"),
              ("LOWER_TO", "Lower side to (m)"))

    def name(self):
        return "swathprofile"

    def displayName(self):
        return tr("Swath profile across a scarp")

    def shortHelpString(self):
        return tr(
            "<p>Samples the DEM in a band along the first line of the layer (or the first "
            "selected line) every pixel, and summarises each step across the band: lowest, "
            "quartiles, median, highest. Single-pixel profiles of this machine-learning DTM "
            "are noisy; the band's median is steadier.</p>"
            "<p>Give two distance ranges along the line, on the far fields either side of "
            "the scarp, to estimate its height: straight lines are fitted to the median in "
            "each, and their separation at the scarp's middle is reported with a 1-sigma "
            "uncertainty (a lower bound, as neighbouring steps are correlated).</p>"
            "<p>Works on any DEM QGIS reads through GDAL: a GEM-15 VRT (only the window "
            "around the line is downloaded) or a clip. The DEM must be projected, in "
            "metres.</p>" + CREDIT)

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(
            "LINE", tr("Profile line"), types=[enum("Qgis.ProcessingSourceType.VectorLine",
                                                    "QgsProcessing.SourceType.TypeVectorLine",
                                                    "QgsProcessing.TypeVectorLine")]))
        self.addParameter(QgsProcessingParameterRasterLayer("DEM", tr("DEM")))
        dbl = enum("Qgis.ProcessingNumberParameterType.Double",
                   "QgsProcessingParameterNumber.Type.Double")
        self.addParameter(QgsProcessingParameterNumber(
            "WIDTH", tr("Swath width (m)"), type=dbl, defaultValue=300.0, minValue=15.0,
            maxValue=5000.0))
        for key, text in self.BOUNDS:
            self.addParameter(QgsProcessingParameterNumber(key, tr(text), type=dbl,
                                                           optional=True, minValue=0.0))
        self.addParameter(QgsProcessingParameterFileDestination(
            "OUTPUT_CSV", tr("Profile table"), fileFilter="CSV (*.csv)"))
        self.addParameter(QgsProcessingParameterFileDestination(
            "OUTPUT_PNG", tr("Profile plot"), fileFilter="PNG (*.png)", optional=True))

    def processAlgorithm(self, parameters, context, feedback):
        import numpy as np

        from . import gdalsafe
        from .gem15core import CAVEAT, profile
        source = self.parameterAsSource(parameters, "LINE", context)
        dem = self.parameterAsRasterLayer(parameters, "DEM", context)
        if source is None or dem is None:
            raise QgsProcessingException(tr("Give a line layer and a DEM."))
        if dem.crs().isGeographic():
            raise QgsProcessingException(tr("The DEM is in degrees; use a projected (metre) "
                                            "GEM-15 layer or clip."))
        geom = next((f.geometry() for f in source.getFeatures()
                     if f.hasGeometry() and not f.geometry().isEmpty()), None)
        if geom is None:
            raise QgsProcessingException(tr("The line layer has no line."))
        geom.transform(QgsCoordinateTransform(source.sourceCrs(), dem.crs(),
                                              context.transformContext()))
        parts = geom.asMultiPolyline() if geom.isMultipart() else [geom.asPolyline()]
        line_xy = [(p.x(), p.y()) for p in parts[0]]
        bounds = [None if parameters.get(k) in (None, "")
                  else self.parameterAsDouble(parameters, k, context) for k, _ in self.BOUNDS]
        if any(b is not None for b in bounds) and None in bounds:
            raise QgsProcessingException(tr("Give all four distances for the scarp height, "
                                             "or none."))
        width = self.parameterAsDouble(parameters, "WIDTH", context)
        try:
            gdalsafe.configure_remote()
            ds = gdalsafe.open_raster(dem.source())
            gt = ds.GetGeoTransform()
            win = profile.window_for(line_xy, width, gt, ds.RasterXSize, ds.RasterYSize)
            band = ds.GetRasterBand(1)
            arr = gdalsafe.check(band.ReadAsArray(*win), "Reading the DEM")
            arr = arr.astype(float)
            nd = band.GetNoDataValue()
            if nd is not None and not np.isnan(nd):
                arr[arr == nd] = np.nan
            sw = profile.sample_swath(arr, profile.window_geotransform(gt, win), line_xy,
                                      width, step_m=abs(gt[1]))
            fit = None
            if bounds[0] is not None:
                fit = profile.scarp_height(sw, (bounds[0], bounds[1]), (bounds[2], bounds[3]))
        except (Gem15Error, ValueError) as e:
            raise QgsProcessingException(str(e))
        out_csv = self.parameterAsFileOutput(parameters, "OUTPUT_CSV", context)
        profile.to_csv(sw, out_csv, fit, source=f"{dem.source()} ({dem.crs().authid()})")
        out_png = self.parameterAsFileOutput(parameters, "OUTPUT_PNG", context)
        png = profile.plot_png(sw, out_png, fit,
                               title=f"Swath profile, {width:g} m wide ({MAP_CREDIT})") \
            if out_png else None
        feedback.pushInfo(tr(f"{sw.length_m:,.0f} m long, {len(sw.distance)} steps. "
                             f"Wrote {out_csv}" + (f" and {png}" if png else "")))
        for line in (fit.describe() if fit else []):
            feedback.pushInfo(line)
        feedback.pushInfo(CAVEAT)
        return {"OUTPUT_CSV": out_csv, "OUTPUT_PNG": png or ""}


def all_algorithms():
    return [BuildVrtAlgorithm(), ClipAlgorithm(), DerivativesAlgorithm(),
            SwathProfileAlgorithm()]

