#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Run QGIS's own Python with the environment QGIS Desktop uses, for headless
tests of the QGIS plugin. Windows (OSGeo4W standalone installer) only.

    python tools/qgis_python.py -c "import qgis.core"
    python tools/qgis_python.py --pytest-from "<site-packages with pytest>" -m pytest tests

Any Python 3 can launch it (ArcGIS Pro's, the system one). Everything after
the options is passed to QGIS's python.exe.

How the environment is found (assumptions flagged):
 Q1. The QGIS install is given by --qgis-root, else the QGIS_ROOT variable,
     else the newest "QGIS 3.*" (or "QGIS 4.*") folder under %ProgramFiles%.
 Q2. The install has bin/<app>-bin.env (KEY=VALUE lines), the file
     qgis/__init__.py itself reads to set up QGIS Desktop's environment, with
     <app> = qgis-ltr or qgis. We read it the same way and add QGIS's Python
     folders to PYTHONPATH, as bin/python-qgis-ltr.bat does.
 Q3. QGIS's Python has no pytest. --pytest-from copies the pure-Python
     packages pytest needs (no compiled code) from another Python's
     site-packages (e.g. ArcGIS Pro's) into a per-user scratch folder and puts
     that folder on PYTHONPATH. Nothing is downloaded.
"""

import argparse
import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile

# Pure-Python packages pytest 7 needs on Python 3.9 (Q3).
PYTEST_PARTS = ("pytest", "_pytest", "pluggy", "iniconfig", "packaging", "colorama",
                "exceptiongroup", "tomli", "py.py")


def find_qgis_root(explicit=None):
    """Folder of a QGIS standalone install (Q1)."""
    root = explicit or os.environ.get("QGIS_ROOT")
    if root:
        return os.path.abspath(root)
    base = os.environ.get("ProgramFiles", r"C:\Program Files")
    found = glob.glob(os.path.join(base, "QGIS 3.*")) + glob.glob(os.path.join(base, "QGIS 4.*"))

    def version(path):
        return tuple(int(v) for v in re.findall(r"\d+", os.path.basename(path)))

    if not found:
        sys.exit("No QGIS install found: pass --qgis-root or set QGIS_ROOT.")
    return max(found, key=version)


def qgis_environment(root):
    """Environment dict for QGIS's Python (Q2)."""
    envs = sorted(glob.glob(os.path.join(root, "bin", "*-bin.env")))
    # Prefer the QGIS app's file over GRASS or others.
    envs.sort(key=lambda p: (not os.path.basename(p).startswith("qgis"), p))
    if not envs:
        sys.exit(f"No bin/*-bin.env in {root}; is this an OSGeo4W QGIS install?")
    env = dict(os.environ)
    with open(envs[0], encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k] = v
    app = os.path.basename(envs[0])[:-len("-bin.env")]
    py_dir = os.path.join(root, "apps", app, "python")
    extra = [py_dir, os.path.join(py_dir, "plugins")]
    env["PYTHONPATH"] = os.pathsep.join(extra + [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p])
    env["PYTHONNOUSERSITE"] = "1"         # ignore packages in the user's own site folder
    env.setdefault("QT_QPA_PLATFORM", "offscreen")   # no window needed for tests
    return env


def borrow_pytest(site_packages, dest=None):
    """Copy pytest's pure-Python parts to a scratch folder; return the folder (Q3)."""
    dest = dest or os.path.join(tempfile.gettempdir(), "gem15tools-pytest-py39")
    os.makedirs(dest, exist_ok=True)
    for name in os.listdir(site_packages):
        base = name.split("-")[0].lower()
        if name in PYTEST_PARTS or (name.endswith(".dist-info") and base in PYTEST_PARTS):
            src, dst = os.path.join(site_packages, name), os.path.join(dest, name)
            if os.path.exists(dst):
                continue
            if os.path.isdir(src):
                shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__"))
            else:
                shutil.copyfile(src, dst)
    missing = [p for p in PYTEST_PARTS if not os.path.exists(os.path.join(dest, p))]
    if missing:
        sys.exit(f"{site_packages} lacks {missing}; point --pytest-from elsewhere.")
    return dest


OWN_OPTIONS = ("--qgis-root", "--pytest-from", "--pytest-dir")


def split_args(argv):
    """Launcher options come first; everything from the first other argument
    (or after '--') goes to QGIS's python unchanged, e.g. '-X faulthandler'."""
    own, i = [], 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--":
            i += 1
            break
        if arg in ("-h", "--help"):
            own.append(arg)
            i += 1
        elif arg in OWN_OPTIONS and i + 1 < len(argv):
            own += argv[i:i + 2]
            i += 2
        elif arg.split("=", 1)[0] in OWN_OPTIONS:
            own.append(arg)
            i += 1
        else:
            break
    return own, argv[i:]


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    p = argparse.ArgumentParser(
        description=__doc__.strip().split("\n\n")[0],
        epilog="Arguments after these options go to QGIS's python.exe.")
    p.add_argument("--qgis-root", help="QGIS install folder (default: QGIS_ROOT or newest found)")
    p.add_argument("--pytest-from", metavar="SITE_PACKAGES",
                   help="site-packages folder to borrow pytest from (Q3)")
    p.add_argument("--pytest-dir", help="use pytest already copied to this folder")
    own, rest = split_args(argv)
    a = p.parse_args(own)
    a.args = rest

    root = find_qgis_root(a.qgis_root)
    env = qgis_environment(root)
    extra = []
    if a.pytest_dir:
        extra.append(os.path.abspath(a.pytest_dir))
    elif a.pytest_from:
        extra.append(borrow_pytest(a.pytest_from))
    if extra:
        env["PYTHONPATH"] = os.pathsep.join(extra + [env["PYTHONPATH"]])
    exe = os.path.join(root, "bin", "python.exe")
    if not os.path.exists(exe):
        sys.exit(f"No python.exe in {root}\\bin")
    return subprocess.call([exe] + a.args, env=env)


if __name__ == "__main__":
    sys.exit(main())
