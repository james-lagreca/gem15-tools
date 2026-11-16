# SPDX-License-Identifier: GPL-2.0-or-later
"""The toolbox must run on Python 3.9 (ArcGIS Pro 3.0-3.2 ship it). It compiles
on 3.9 in CI, but newer library features only fail when the line runs, so
look for the ones most likely to creep in."""
import ast
import pathlib

PYT = pathlib.Path(__file__).resolve().parents[1] / "arcgis" / "GEM15Tools.pyt"

# Keyword arguments and names added to the standard library after Python 3.9.
NEW_KEYWORDS = {"ignore_cleanup_errors": "TemporaryDirectory, 3.10",
                "strict": "zip, 3.10", "kw_only": "dataclass, 3.10",
                "slots": "dataclass, 3.10", "root_dir": "glob, 3.10"}
NEW_NAMES = {"pairwise": "itertools, 3.10", "tomllib": "3.11", "StrEnum": "enum, 3.11",
             "ExceptionGroup": "3.11", "UTC": "datetime, 3.11"}


def test_toolbox_uses_python39_library_only():
    tree = ast.parse(PYT.read_text(encoding="utf-8"))
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg in NEW_KEYWORDS:
            bad.append(f"line {node.value.lineno}: {node.arg}= ({NEW_KEYWORDS[node.arg]})")
        name = node.attr if isinstance(node, ast.Attribute) else getattr(node, "id", None)
        if name in NEW_NAMES:
            bad.append(f"line {node.lineno}: {name} ({NEW_NAMES[name]})")
    assert not bad, "Python 3.10+ only, breaks Pro 3.0-3.2: " + "; ".join(bad)
