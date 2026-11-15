#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Build the ArcGIS Pro toolbox zip for a release.

    python tools/package_arcgis_toolbox.py         # -> dist/gem15-arcgis-<version>.zip

The zip holds one top-level folder, gem15-arcgis/, with GEM15Tools.pyt, its
help files (.pyt.xml), its vendored gem15core and the GPL licence. Unzip it
to a permanent folder and add GEM15Tools.pyt in Pro. The script stops if the
vendored core has drifted from core/gem15core or the help files are stale.
"""

import argparse
import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARCGIS = os.path.join(ROOT, "arcgis")
SKIP_DIRS = {"__pycache__", ".pytest_cache"}
SKIP_EXT = (".pyc", ".pyo", ".aux.xml")


def version():
    sys.path.insert(0, os.path.join(ROOT, "core"))
    import gem15core
    return gem15core.__version__


def build(out_dir):
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import build_pyt_help
    import vendor_core
    drift = vendor_core.sync(check=True)
    if drift:
        raise SystemExit("Vendored core has drifted; run python tools/vendor_core.py\n"
                         + "\n".join(drift))
    stale = build_pyt_help.check()
    if stale:
        raise SystemExit("Help files are stale; run python tools/build_pyt_help.py\n"
                         + "\n".join(stale))
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"gem15-arcgis-{version()}.zip")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for folder, dirs, files in os.walk(ARCGIS):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
            for name in sorted(files):
                if name.endswith(SKIP_EXT):
                    continue
                full = os.path.join(folder, name)
                rel = os.path.relpath(full, ARCGIS).replace(os.sep, "/")
                z.write(full, "gem15-arcgis/" + rel)
        z.write(os.path.join(ROOT, "LICENSE"), "gem15-arcgis/LICENSE")
    return path


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.strip().split("\n\n")[0])
    p.add_argument("--out", default=os.path.join(ROOT, "dist"), help="output folder (default dist/)")
    a = p.parse_args(argv)
    print(build(a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
