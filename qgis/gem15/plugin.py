# SPDX-License-Identifier: GPL-2.0-or-later
"""
The plugin's GUI: a "GEM-15" toolbar button (with a menu) and Raster menu
entries, plus the Processing provider.

  GEM-15 DTM for current view     multidirectional hillshade of the DTM
                                  (the button's default, whatever ran last)
  GEM-15 DSM for current view     the same for the DSM (first band)
  GEM-15 live LRM for current view   local relief model, drawn at large scales
  GEM-15 canopy check for current view   where the DTM is a guess (DSM - DTM)
  LRM radius: 150 / 300 / 1000 m  (presets, gem15core.terrain T2)
  Show tile edges                 (checkable, on: outlines where seams may be)
  Several UTM zones: one layer per zone   (checkable; off = one warped layer)
  Open the VRT folder / About GEM-15
A layer already in the project is not added again.

The catalogue lookup, VRT writing and layer set-up run in a QgsTask
(tasks.py); the GUI only collects the view and adds the finished layers.
"""

import os

from qgis.core import QgsApplication, QgsMessageLog, QgsProject
from qgis.PyQt.QtCore import QCoreApplication, QUrl
from qgis.PyQt.QtGui import QDesktopServices, QIcon
from qgis.PyQt.QtWidgets import QMenu, QMessageBox, QToolButton

try:                                  # Qt6 moved QAction and QActionGroup to QtGui
    from qgis.PyQt.QtGui import QAction, QActionGroup
except ImportError:                   # Qt5
    from qgis.PyQt.QtWidgets import QAction, QActionGroup

from . import network, settings, view
from .gem15core import (ATTRIBUTION, CAVEAT, CITATION, MAP_CREDIT, VERTICAL_DATUM,
                        Gem15Error, terrain)

MENU = "&GEM-15"
LOG_TAG = "GEM-15"
ICON = os.path.join(os.path.dirname(__file__), "icons", "gem15.svg")


def tr(text):
    return QCoreApplication.translate("GEM15", text)


class Gem15Plugin:
    """Created by classFactory when QGIS loads the plugin."""

    def __init__(self, iface):
        self.iface = iface
        self.provider = None
        self.toolbar = None
        self.button = None
        self.actions = []
        self.tasks = set()             # keep running tasks referenced until finished
        self.unloaded = False

    # ---------------------------------------------------------------- set-up
    def initProcessing(self):
        """Register the Processing provider (also called by qgis_process)."""
        from .provider import Gem15Provider
        if self.provider is None:
            provider = Gem15Provider()
            # False if a "gem15" provider is already registered (e.g. a second
            # copy of the plugin): then leave that one alone, here and in unload().
            if QgsApplication.processingRegistry().addProvider(provider):
                self.provider = provider
            else:
                QgsMessageLog.logMessage(tr("A GEM-15 Processing provider is already "
                                            "registered; not adding another."), LOG_TAG)

    def initGui(self):
        from . import gdalsafe
        mode = gdalsafe.configure_remote()                    # gdalsafe S2
        if mode != "scoped":
            QgsMessageLog.logMessage(
                tr("This GDAL cannot limit options to the GEM-15 bucket; some remote-read "
                   "settings were left out, so each tile opens with a few extra requests."),
                LOG_TAG)
        self.initProcessing()

        parent = self.iface.mainWindow()
        icon = QIcon(ICON)

        def action(text, slot, checkable=False):
            a = QAction(icon if not checkable else QIcon(), tr(text), parent)
            a.setCheckable(checkable)
            a.triggered.connect(slot)
            self.actions.append(a)
            return a

        self.act_dtm = action("GEM-15 DTM for current view", lambda: self.for_view("dtm"))
        self.act_dsm = action("GEM-15 DSM for current view", lambda: self.for_view("dsm"))
        self.act_lrm = action("GEM-15 live LRM (DTM) for current view",
                              lambda: self.for_view("lrm"))
        self.act_canopy = action("GEM-15 canopy check (DSM minus DTM) for current view",
                                 lambda: self.for_view("canopy"))
        self.act_edges = action("Show tile edges (where seams may be)", self._set_edges,
                                checkable=True)
        self.act_edges.setChecked(settings.value("tile_edges", True, bool))
        self.act_zone = action("Several UTM zones: one layer per zone (no resampling)",
                               self._set_per_zone, checkable=True)
        self.act_zone.setChecked(settings.value("per_zone", False, bool))
        self.act_folder = action("Open the GEM-15 VRT folder", self.open_folder)
        self.act_about = action("About GEM-15 and credit", self.about)
        for a in (self.act_dtm, self.act_dsm, self.act_lrm, self.act_canopy):
            a.setToolTip(tr(a.text() + ": streams 15 m tiles for the visible area; "
                            "nothing is downloaded up front."))

        menu = QMenu(parent)
        for a in (self.act_dtm, self.act_dsm, self.act_lrm, self.act_canopy):
            menu.addAction(a)
        radius_menu = menu.addMenu(tr("LRM radius"))
        self.radius_group = QActionGroup(parent)
        current = settings.value("lrm_radius", terrain.LRM_RADIUS_M, float)
        for r in terrain.LRM_PRESETS_M:
            a = QAction(tr(f"{r:g} m: {terrain.LRM_PRESET_NOTES[r]}"), parent)
            a.setCheckable(True)
            a.setChecked(abs(r - current) < 1e-9)
            a.triggered.connect(lambda checked, r=r: settings.set_value("lrm_radius", r))
            self.radius_group.addAction(a)
            radius_menu.addAction(a)
        menu.addSeparator()
        for a in (self.act_edges, self.act_zone, self.act_folder, self.act_about):
            menu.addAction(a)

        self.toolbar = self.iface.addToolBar(tr("GEM-15"))
        self.toolbar.setObjectName("Gem15Toolbar")
        self.button = QToolButton(self.toolbar)
        self.button.setMenu(menu)
        self.button.setDefaultAction(self.act_dtm)
        self.button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.toolbar.addWidget(self.button)
        self.menu = menu
        for a in self.actions:
            self.iface.addPluginToRasterMenu(MENU, a)

    def unload(self):
        self.unloaded = True
        for t in list(self.tasks):
            t.cancel()
        for a in self.actions:
            self.iface.removePluginRasterMenu(MENU, a)
        if self.toolbar is not None:
            self.iface.mainWindow().removeToolBar(self.toolbar)
            self.toolbar.deleteLater()
            self.toolbar = None
        if self.provider is not None:
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None
        self.actions = []

    # --------------------------------------------------------------- actions
    def _set_per_zone(self, checked):
        settings.set_value("per_zone", bool(checked))

    def _set_edges(self, checked):
        settings.set_value("tile_edges", bool(checked))

    def for_view(self, kind):
        """Start a background task adding GEM-15 for the visible map area."""
        from .tasks import CurrentViewTask
        bar = self.iface.messageBar()
        canvas = self.iface.mapCanvas()
        try:
            bbox = view.lonlat_bbox(canvas.extent(), canvas.mapSettings().destinationCrs(),
                                    QgsProject.instance().transformContext())
        except Gem15Error as e:
            bar.pushWarning("GEM-15", str(e))
            return
        n = view.cell_count(bbox)
        if n > settings.MAX_VIEW_CELLS:                                   # settings P1
            bar.pushWarning("GEM-15", tr(
                f"The view spans about {n} one-degree cells. Zoom in to at most "
                f"{settings.MAX_VIEW_CELLS} (about 6 x 6 degrees), or use "
                "Processing > GEM-15 > Build GEM-15 VRT for a larger area."))
            return
        # The button keeps DTM as its default: switching to the last action
        # could leave it on the live LRM, which is invisible when zoomed out.
        try:
            folder = settings.output_folder()
        except OSError as e:
            bar.pushCritical("GEM-15", tr(f"Cannot create the VRT folder: {e}"))
            return
        task = CurrentViewTask(bbox, kind, folder, settings.value("per_zone", False, bool),
                               self._task_done, fetch=network.fetcher(),
                               lrm_radius_m=settings.value("lrm_radius",
                                                           terrain.LRM_RADIUS_M, float),
                               tile_edges=settings.value("tile_edges", True, bool))
        self.tasks.add(task)
        QgsApplication.taskManager().addTask(task)

    def _task_done(self, task, ok):
        """Main thread: add the task's layers to the project and report."""
        self.tasks.discard(task)
        if self.unloaded:
            return
        for line in task.log_lines:
            QgsMessageLog.logMessage(line, LOG_TAG)
        bar = self.iface.messageBar()
        if not ok:
            if task.error:
                QgsMessageLog.logMessage(task.error, LOG_TAG)
                bar.pushWarning("GEM-15", task.error.splitlines()[0][:400])
            return
        added = add_layers(task.plan, task.layers)
        if task.edges_path:
            add_edges_layer(task.edges_path)
        for line in task.plan.messages:
            QgsMessageLog.logMessage(line, LOG_TAG)
        text = " ".join(task.plan.messages)
        if task.layers and not added:
            text += tr(" Already in the project: nothing new added.")
        if task.kind in ("lrm", "canopy"):
            text += tr(f" Drawn at 1:{settings.LRM_MAX_SCALE:,.0f} and closer")
            if self.iface.mapCanvas().scale() > settings.LRM_MAX_SCALE:
                text += tr(": zoom in to see it")
            text += "."
        QgsMessageLog.logMessage(CAVEAT, LOG_TAG)
        bar.pushSuccess("GEM-15", text + f" {MAP_CREDIT}.")

    def open_folder(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(settings.output_folder()))

    def about(self):
        QMessageBox.information(
            self.iface.mainWindow(), tr("About GEM-15"),
            tr("GEM-15: 15 m global elevation (DTM and DSM).\n\n"
               f"{ATTRIBUTION}. Licence CC-BY 4.0.\nPlease cite {CITATION}.\n\n"
               f"Heights: {VERTICAL_DATUM}. The DTM is predicted by machine learning: treat "
               "it with care under canopy. Tile overlaps and acquisition seams can look like "
               "scarps.\n\nVRTs are written to:\n" + settings.output_folder()))


def _in_project(source):
    norm = os.path.normcase(os.path.normpath(source.split("|")[0]))
    return any(os.path.normcase(os.path.normpath(lyr.source().split("|")[0])) == norm
               for lyr in QgsProject.instance().mapLayers().values())


def add_layers(plan, layers):
    """Add finished layers to the project, in a group when there are several,
    skipping any whose VRT the project already shows. Returns those added."""
    project = QgsProject.instance()
    layers = [lyr for lyr in layers if not _in_project(lyr.source())]
    if plan.group and len(layers) > 1:
        group = project.layerTreeRoot().insertGroup(0, plan.group)
        for layer in layers:
            project.addMapLayer(layer, False)
            group.addLayer(layer)
    else:
        for layer in layers:
            project.addMapLayer(layer)
    return layers


def add_edges_layer(gpkg_path):
    """Show the tile-edges layer once; later runs only add rows to its file."""
    from . import layers as layers_mod
    for lyr in QgsProject.instance().mapLayers().values():
        if os.path.normcase(lyr.source().split("|")[0]) == os.path.normcase(gpkg_path):
            lyr.dataProvider().reloadData()
            lyr.triggerRepaint()
            return lyr
    try:
        layer = layers_mod.make_edges_layer(gpkg_path)
    except Gem15Error as e:
        QgsMessageLog.logMessage(str(e), LOG_TAG)
        return None
    QgsProject.instance().addMapLayer(layer)
    return layer
