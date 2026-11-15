# SPDX-License-Identifier: GPL-2.0-or-later
"""
Background task for "GEM-15 for current view", so QGIS never freezes.

run() (worker thread): catalogue lookup, VRT writing, and opening and styling
the layers. Opening a layer already reads a little (QGIS samples it for
default statistics), which is network traffic, so it happens here too. Each
layer is then moved to the main thread, as QGIS requires before a layer is
added to a project (assumption T1: QgsRasterLayer and its data provider move
together, as the provider is the layer's child object).

finished() (main thread): hands the layers to the callback, which adds them.
"""

import traceback

from qgis.core import QgsApplication, QgsTask

from . import layers as layers_mod
from . import live
from .compat import enum
from .gem15core import Gem15Error


class CurrentViewTask(QgsTask):
    """Find tiles for a lon/lat box, write VRTs and build styled layers."""

    def __init__(self, bbox, kind, folder, per_zone, on_done, fetch=None,
                 collection_url=None):
        super().__init__(f"GEM-15 {kind.upper()} for current view",
                         enum("QgsTask.Flag.CanCancel"))
        self.bbox, self.kind, self.folder, self.per_zone = bbox, kind, folder, per_zone
        self.fetch, self.collection_url = fetch, collection_url
        self.on_done = on_done
        self.plan = None
        self.layers = []
        self.error = None
        self.log_lines = []

    def run(self):
        try:
            self.setProgress(5)
            self.plan = live.plan_layers(
                self.bbox, self.kind, self.folder, per_zone=self.per_zone,
                fetch=self.fetch, collection_url=self.collection_url,
                log=self.log_lines.append, is_cancelled=self.isCanceled)
            if self.isCanceled():
                return False
            self.setProgress(60)
            main = QgsApplication.instance().thread()
            for i, spec in enumerate(self.plan.layers):
                layer = layers_mod.make_layer(spec.path, spec.name, spec.style)
                layer.moveToThread(main)                                   # T1
                self.layers.append(layer)
                self.setProgress(60 + 40 * (i + 1) / len(self.plan.layers))
                if self.isCanceled():
                    return False
            return True
        except Gem15Error as e:
            self.error = str(e)
        except Exception:                      # report, never crash QGIS
            self.error = "Unexpected error:\n" + traceback.format_exc()
        return False

    def finished(self, ok):
        self.on_done(self, ok)
