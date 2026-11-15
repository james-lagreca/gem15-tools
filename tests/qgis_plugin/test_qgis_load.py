# SPDX-License-Identifier: GPL-2.0-or-later
"""The plugin loads in QGIS the way QGIS loads it (qgis.utils), from the
source folder and from the release zip, and unloads cleanly."""
import os
import pathlib
import subprocess
import sys
import zipfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
PLUGIN_PARENT = ROOT / "qgis"


@pytest.fixture
def iface(qgis_app):
    """A stand-in for QGIS's iface: real main window, canvas and message bar."""
    from unittest import mock
    from qgis.gui import QgisInterface, QgsMapCanvas, QgsMessageBar
    from qgis.PyQt.QtWidgets import QMainWindow, QToolBar

    win = QMainWindow()
    canvas = QgsMapCanvas(win)
    canvas.resize(800, 600)
    bar = QgsMessageBar(win)
    menus = {}
    i = mock.Mock(spec=QgisInterface)
    i.mainWindow.return_value = win
    i.mapCanvas.return_value = canvas
    i.messageBar.return_value = bar

    def add_toolbar(name):
        tb = QToolBar(name, win)
        win.addToolBar(tb)
        return tb

    i.addToolBar.side_effect = add_toolbar
    i.addPluginToRasterMenu.side_effect = lambda m, a: menus.setdefault(m, []).append(a)
    i.removePluginRasterMenu.side_effect = lambda m, a: menus[m].remove(a)
    i.raster_menus = menus
    yield i
    win.deleteLater()


def _load_with_qgis_utils(plugin_dir_parent, iface):
    """Discover, import, start and return the plugin as QGIS Desktop does."""
    import qgis.utils
    from qgis.core import QgsApplication
    for name in [m for m in sys.modules if m == "gem15" or m.startswith("gem15.")]:
        del sys.modules[name]            # import fresh from this folder
    sys.path.insert(0, str(plugin_dir_parent))
    qgis.utils.iface = iface
    qgis.utils.plugin_paths = [str(plugin_dir_parent)]
    qgis.utils.updateAvailablePlugins()
    assert "gem15" in qgis.utils.available_plugins
    assert qgis.utils.pluginMetadata("gem15", "name") == "GEM-15 Elevation"
    assert qgis.utils.loadPlugin("gem15")
    assert qgis.utils.startPlugin("gem15"), "classFactory() or initGui() failed"
    plugin = qgis.utils.plugins["gem15"]
    assert os.path.dirname(sys.modules["gem15"].__file__) == os.path.join(
        str(plugin_dir_parent), "gem15")
    return plugin, QgsApplication.processingRegistry()


def _check_loaded(plugin, registry, iface):
    assert plugin.toolbar is not None and plugin.toolbar.objectName() == "Gem15Toolbar"
    texts = [a.text() for a in plugin.actions]
    assert "GEM-15 DTM for current view" in texts and "GEM-15 DSM for current view" in texts
    assert len(iface.raster_menus["&GEM-15"]) == len(plugin.actions)
    prov = registry.providerById("gem15")
    assert prov is not None
    assert sorted(a.name() for a in prov.algorithms()) == ["buildvrt", "clip", "derivatives"]


def _unload(iface):
    import qgis.utils
    from qgis.core import QgsApplication
    assert qgis.utils.unloadPlugin("gem15")
    assert QgsApplication.processingRegistry().providerById("gem15") is None
    assert iface.raster_menus["&GEM-15"] == []


def test_loads_and_unloads_from_source(iface, gdal_exceptions_off):
    from osgeo import gdal
    plugin, reg = _load_with_qgis_utils(PLUGIN_PARENT, iface)
    try:
        _check_loaded(plugin, reg, iface)
        assert gdal.GetUseExceptions() == 0, "loading must not turn GDAL exceptions on"
        # Remote options are limited to the bucket, not set process-wide (gdalops G4).
        assert gdal.GetConfigOption("GDAL_DISABLE_READDIR_ON_OPEN") is None
    finally:
        _unload(iface)


def test_loads_from_release_zip(iface, tmp_path, gdal_exceptions_off):
    sys.path.insert(0, str(ROOT / "tools"))
    import package_qgis_plugin
    zpath = package_qgis_plugin.build(str(tmp_path / "dist"))
    with zipfile.ZipFile(zpath) as z:
        names = z.namelist()
        assert all(n.startswith("gem15/") for n in names)
        assert "gem15/LICENSE" in names and "gem15/metadata.txt" in names
        assert "gem15/gem15core/stac.py" in names, "vendored core must be in the zip"
        assert not [n for n in names if "__pycache__" in n or n.endswith(".pyc")]
        z.extractall(tmp_path / "plugins")
    plugin, reg = _load_with_qgis_utils(tmp_path / "plugins", iface)
    try:
        _check_loaded(plugin, reg, iface)
    finally:
        _unload(iface)


def test_processing_only_start(qgis_app):
    """qgis_process starts processing plugins with initProcessing() only."""
    import qgis.utils
    from qgis.core import QgsApplication
    sys.path.insert(0, str(PLUGIN_PARENT))
    qgis.utils.plugin_paths = [str(PLUGIN_PARENT)]
    qgis.utils.updateAvailablePlugins()
    assert qgis.utils.loadPlugin("gem15")
    assert qgis.utils.startProcessingPlugin("gem15")
    try:
        assert QgsApplication.processingRegistry().providerById("gem15") is not None
    finally:
        qgis.utils.unloadPlugin("gem15")


def test_import_leaves_gdal_exceptions_alone():
    """In a fresh interpreter: importing the plugin's GDAL module (which imports
    the core's gdalops) must not change GDAL's process-wide exception flag."""
    code = ("import sys; sys.path.insert(0, sys.argv[1]);"
            "from osgeo import gdal; before = gdal.GetUseExceptions();"
            "import gem15.gdalsafe as g; mode = g.configure_remote();"
            "print(before, gdal.GetUseExceptions(), mode,"
            " gdal.GetConfigOption('CPL_VSIL_CURL_ALLOWED_EXTENSIONS'))")
    out = subprocess.run([sys.executable, "-c", code, str(PLUGIN_PARENT)],
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == ["0", "0", "scoped", "None"]
