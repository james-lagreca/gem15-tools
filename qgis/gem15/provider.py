# SPDX-License-Identifier: GPL-2.0-or-later
"""Processing provider "GEM-15" holding the plugin's algorithms."""

import os

from qgis.core import QgsProcessingProvider
from qgis.PyQt.QtGui import QIcon

from . import algorithms

ICON = os.path.join(os.path.dirname(__file__), "icons", "gem15.svg")


class Gem15Provider(QgsProcessingProvider):

    def id(self):
        return "gem15"

    def name(self):
        return "GEM-15"

    def longName(self):
        return "GEM-15 15 m elevation (Google)"

    def icon(self):
        return QIcon(ICON)

    def svgIconPath(self):
        return ICON

    def loadAlgorithms(self):
        for alg in algorithms.all_algorithms():
            self.addAlgorithm(alg)
