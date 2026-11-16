#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Build the QGIS plugin zip for "Plugins > Manage and Install Plugins >
Install from ZIP" (and for plugins.qgis.org).

    python tools/package_qgis_plugin.py            # -> dist/gem15-qgis-<version>.zip
    python tools/package_qgis_plugin.py --out some/folder

The zip holds one top-level folder, gem15/, with the plugin, its vendored
core, the GPL licence (plugins.qgis.org requires a LICENSE file) and the
project README.
Compiled files and caches are left out. The vendored core is checked first:
the script stops if it has drifted from core/gem15core.
"""

import argparse
import configparser
import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN = os.path.join(ROOT, "qgis", "gem15")
SKIP_DIRS = {"__pycache__", ".pytest_cache"}
SKIP_EXT = (".pyc", ".pyo", ".aux.xml")


def version():
    cp = configparser.ConfigParser()
    cp.read(os.path.join(PLUGIN, "metadata.txt"), encoding="utf-8")
    return cp["general"]["version"]


def build(out_dir):
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import vendor_core
    drift = vendor_core.sync(check=True)
    if drift:
        raise SystemExit("Vendored core has drifted; run python tools/vendor_core.py\n"
                         + "\n".join(drift))
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"gem15-qgis-{version()}.zip")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for folder, dirs, files in os.walk(PLUGIN):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
            for name in sorted(files):
                if name.endswith(SKIP_EXT):
                    continue
                full = os.path.join(folder, name)
                rel = os.path.relpath(full, os.path.dirname(PLUGIN))
                z.write(full, rel.replace(os.sep, "/"))
        z.write(os.path.join(ROOT, "LICENSE"), "gem15/LICENSE")
        z.write(os.path.join(ROOT, "README.md"), "gem15/README.md")
    return path


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.strip().split("\n\n")[0])
    p.add_argument("--out", default=os.path.join(ROOT, "dist"), help="output folder (default dist/)")
    a = p.parse_args(argv)
    print(build(a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
