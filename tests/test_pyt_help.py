# SPDX-License-Identifier: GPL-2.0-or-later
"""The toolbox's hover help (arcgis/gem15help.py -> GEM15Tools.*.pyt.xml):
every parameter of every tool has some, and the files are up to date.
Run `python tools/build_pyt_help.py` after changing tools or help text."""
import importlib

import pytest

from conftest import ROOT


@pytest.fixture
def builder(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    import build_pyt_help
    return importlib.reload(build_pyt_help)


def test_every_parameter_has_help(builder):
    files = builder.expected()               # raises SystemExit naming what is missing
    tools = builder.load_tools()
    assert len(files) == len(tools) + 1      # one per tool, plus the toolbox
    for cls_name, _, params in tools:
        assert params, cls_name


def test_help_files_are_current(builder):
    assert builder.check() == [], "run: python tools/build_pyt_help.py"


def test_pro_reads_the_help():
    """arcpy builds a tool's Python help from the same file Pro's dialogs use."""
    arcpy = pytest.importorskip("arcpy")
    tb = arcpy.ImportToolbox(str(ROOT / "arcgis" / "GEM15Tools.pyt"))
    doc = tb.GEM15ForCurrentView.__doc__ or ""
    assert "in one step" in doc                                      # the summary
    assert "machine learning" in doc and "bare-ground" in doc        # usage, a parameter
