#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Write the ArcGIS Pro help files for the toolbox from arcgis/gem15help.py:

    python tools/build_pyt_help.py          write arcgis/GEM15Tools*.pyt.xml
    python tools/build_pyt_help.py --check  exit 1 if they are missing or out of date

Pro shows a tool's <summary> on its Help button and each parameter's
<dialogReference> on the parameter's (i) button; the files sit beside the
.pyt and are named GEM15Tools.<ToolClass>.pyt.xml (a Python toolbox cannot
carry help itself; checked on Pro 3.4, 2026-11-15).

The toolbox is loaded with a small stand-in for arcpy, so this needs no
ArcGIS: parameter names come from each tool's getParameterInfo(). Pro may
rewrite the files once when it opens the toolbox (SyncOnce), so --check
compares their text content, not their bytes.
"""

import argparse
import importlib.machinery
import importlib.util
import os
import sys
import types
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARCGIS = os.path.join(ROOT, "arcgis")
PYT = os.path.join(ARCGIS, "GEM15Tools.pyt")
STEM = "GEM15Tools"


# --------------------------------------------------------------------------
# A stand-in for arcpy, enough for getParameterInfo()
# --------------------------------------------------------------------------
class _Filter:
    def __init__(self):
        self.type, self.list = None, []


class _Parameter:
    def __init__(self, displayName="", name="", datatype="", parameterType="Required",
                 direction="Input", multiValue=False, **_):
        self.displayName, self.name, self.datatype = displayName, name, datatype
        self.parameterType, self.direction, self.multiValue = parameterType, direction, multiValue
        self.filter, self.value, self.values = _Filter(), None, None
        self.category, self.enabled = None, True


def _stub_arcpy():
    m = types.ModuleType("arcpy")
    m.Parameter = _Parameter
    m.ProductInfo = lambda: "ArcInfo"

    class _NoProject:
        def __init__(self, *a, **k):
            raise RuntimeError("no project (help builder)")
    m.mp = types.SimpleNamespace(ArcGISProject=_NoProject)
    return m


def load_tools():
    """[(class name, tool label, [(name, label, type, direction, datatype)])]."""
    saved = {k: sys.modules.get(k) for k in ("arcpy",)}
    sys.modules["arcpy"] = _stub_arcpy()
    try:
        loader = importlib.machinery.SourceFileLoader("gem15pyt_help", PYT)
        mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("gem15pyt_help",
                                                                              loader))
        loader.exec_module(mod)
        out = []
        for cls in mod.Toolbox().tools:
            tool = cls()
            params = [(p.name, p.displayName, p.parameterType, p.direction,
                       p.datatype if isinstance(p.datatype, str) else p.datatype[0])
                      for p in tool.getParameterInfo()]
            out.append((cls.__name__, tool.label, params))
        return out
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def load_help():
    sys.path.insert(0, ARCGIS)
    try:
        import gem15help
        return gem15help
    finally:
        sys.path.remove(ARCGIS)


# --------------------------------------------------------------------------
# XML
# --------------------------------------------------------------------------
def _para(parent, tag, text):
    el = ET.SubElement(parent, tag)
    ET.SubElement(el, "para").text = text
    return el


def tool_xml(cls_name, label, params, help_):
    """ElementTree for one tool's metadata."""
    root = ET.Element("metadata", {"xml:lang": "en"})
    esri = ET.SubElement(root, "Esri")
    ET.SubElement(esri, "ArcGISFormat").text = "1.0"
    ET.SubElement(esri, "SyncOnce").text = "TRUE"
    tool = ET.SubElement(root, "tool", name=cls_name, displayname=label, toolboxalias="gem15",
                         xmlns="")
    ps = ET.SubElement(tool, "parameters")
    for name, plabel, ptype, direction, dtype in params:
        p = ET.SubElement(ps, "param", name=name, displayname=plabel, type=ptype,
                          direction=direction, datatype=dtype)
        _para(p, "dialogReference", help_["params"][name])
    _para(tool, "summary", help_["summary"])
    usage = ET.SubElement(tool, "usage")
    for item in help_.get("usage", []):
        ET.SubElement(usage, "bullet_item").text = item
    info = ET.SubElement(root, "dataIdInfo")
    cit = ET.SubElement(info, "idCitation")
    ET.SubElement(cit, "resTitle").text = label
    ET.SubElement(info, "idPurp").text = help_["summary"]
    keys = ET.SubElement(info, "searchKeys")
    for kw in ("GEM-15", "elevation", "DEM", "active faults", "hillshade"):
        ET.SubElement(keys, "keyword").text = kw
    dist = ET.SubElement(ET.SubElement(root, "distInfo"), "distFormat")
    ET.SubElement(dist, "formatName").text = "ArcToolbox Tool"
    ET.SubElement(ET.SubElement(root, "mdHrLv"), "ScopeCd", value="005")
    return ET.ElementTree(root)


def content(path):
    """What Pro shows from a help file: summary, usage and each parameter's text."""
    root = ET.parse(path).getroot()
    tool = root.find("tool")
    if tool is None:
        return {"purpose": (root.findtext("dataIdInfo/idPurp") or "").strip()}
    return {
        "summary": (tool.findtext("summary/para") or "").strip(),
        "usage": [(b.text or "").strip() for b in tool.findall("usage/bullet_item")],
        "params": {p.get("name"): (p.findtext("dialogReference/para") or "").strip()
                   for p in tool.findall("parameters/param")},
    }


def expected():
    """{path: ElementTree} for every tool's help file, from the toolbox and
    gem15help. (The toolbox's own GEM15Tools.pyt.xml is left to Pro: it
    rewrites it with the date on every load, and tool help does not need it.)"""
    h = load_help()
    files = {}
    problems = []
    for cls_name, label, params in load_tools():
        entry = h.HELP.get(cls_name)
        if entry is None:
            problems.append(f"{cls_name}: no entry in gem15help.HELP")
            continue
        names = [p[0] for p in params]
        missing = [n for n in names if not entry["params"].get(n)]
        extra = sorted(set(entry["params"]) - set(names))
        if missing or extra:
            problems.append(f"{cls_name}: no help for {missing}; help for unknown {extra}")
            continue
        files[os.path.join(ARCGIS, f"{STEM}.{cls_name}.pyt.xml")] = tool_xml(
            cls_name, label, params, entry)
    if problems:
        raise SystemExit("Help is incomplete:\n  " + "\n  ".join(problems))
    return files


def check():
    """List of out-of-date or missing help files (empty when all are current)."""
    stale = []
    for path, tree in expected().items():
        tmp = ET.tostring(tree.getroot(), encoding="unicode")
        want = content_from_string(tmp)
        try:
            have = content(path)
        except (OSError, ET.ParseError):
            have = None
        if have != want:
            stale.append(os.path.relpath(path, ROOT))
    return stale


def content_from_string(text):
    import io
    tmp = io.StringIO(text)
    return content(tmp)


def serialise(tree):
    """The text ArcGIS writes back when it loads the toolbox (tab indents, CRLF,
    double-quoted declaration, "/>" without a space; seen with arcpy, Pro 3.4),
    so neither this script nor Pro leaves the files changed in git."""
    ET.indent(tree, space="\t")
    body = ET.tostring(tree.getroot(), encoding="unicode").replace(" />", "/>")
    text = '<?xml version="1.0" encoding="UTF-8"?>\n' + body + "\n"
    return text.replace("\r\n", "\n").replace("\n", "\r\n")


def write():
    written = []
    for path, tree in expected().items():
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(serialise(tree))
        written.append(os.path.relpath(path, ROOT))
    return written


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="only check; exit 1 if stale")
    a = ap.parse_args(argv)
    if a.check:
        stale = check()
        for s in stale:
            print("out of date:", s)
        return 1 if stale else 0
    for w in write():
        print("wrote", w)
    return 0


if __name__ == "__main__":
    sys.exit(main())
