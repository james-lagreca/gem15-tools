# SPDX-License-Identifier: GPL-2.0-or-later
"""Headless QGIS fixtures for the plugin tests.

These tests need QGIS's own Python (qgis.core); elsewhere they are not
collected. Run them with tools/qgis_python.py, e.g.

    python tools/qgis_python.py --pytest-from "<Pro>/bin/Python/envs/arcgispro-py3/Lib/site-packages" -m pytest tests

The plugin package (qgis/gem15) is imported as `gem15`, with its own vendored
gem15core, as QGIS would import it.
"""
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
PLUGIN_PARENT = ROOT / "qgis"

try:
    import qgis.core  # noqa: F401
    HAVE_QGIS = True
except ImportError:
    HAVE_QGIS = False

if not HAVE_QGIS:
    collect_ignore_glob = ["test_*.py"]
else:
    if str(PLUGIN_PARENT) not in sys.path:
        sys.path.insert(0, str(PLUGIN_PARENT))
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qgis_app(tmp_path_factory):
    """One QgsApplication (with GUI classes, offscreen) for the whole session,
    with a throw-away settings profile so tests never touch the user's."""
    from qgis.core import QgsApplication
    profile = tmp_path_factory.mktemp("qgis_profile")
    QgsApplication.setOrganizationName("gem15tools-tests")
    QgsApplication.setApplicationName("gem15tools-tests")
    app = QgsApplication.instance()
    if app is None:
        app = QgsApplication([], True, str(profile))
        app.initQgis()
    yield app


@pytest.fixture
def gdal_exceptions_off():
    """QGIS runs with GDAL exceptions off; the core's test fixtures turn them on.
    Put QGIS's default back for a test, and restore afterwards."""
    from osgeo import gdal
    before = gdal.GetUseExceptions()
    gdal.DontUseExceptions()
    yield
    if before:
        gdal.UseExceptions()


@pytest.fixture
def project(qgis_app):
    """An empty QgsProject.instance(), cleared again after the test."""
    from qgis.core import QgsProject
    p = QgsProject.instance()
    p.clear()
    yield p
    p.clear()


@pytest.fixture(scope="module")
def processing(qgis_app):
    """Processing framework with the GEM-15 provider registered (for one test
    module, so the plugin-loading tests start with no gem15 provider)."""
    from processing.core.Processing import Processing
    from qgis.core import QgsApplication
    Processing.initialize()
    from gem15.provider import Gem15Provider
    reg = QgsApplication.processingRegistry()
    provider = None
    if reg.providerById("gem15") is None:
        provider = Gem15Provider()            # keep a Python reference while registered
        assert reg.addProvider(provider)
    assert reg.algorithmById("gem15:buildvrt") is not None
    import processing as processing_module
    yield processing_module
    if provider is not None:
        reg.removeProvider(provider)


@pytest.fixture
def wait_for(qgis_app):
    """wait_for(predicate, timeout_s): spin the Qt event loop until predicate()
    is true (QgsTask.finished runs on it)."""
    import time

    def wait(predicate, timeout_s=60):
        end = time.time() + timeout_s
        while not predicate():
            qgis_app.processEvents()
            if time.time() > end:
                raise TimeoutError("timed out waiting for QGIS")
            time.sleep(0.01)
    return wait
