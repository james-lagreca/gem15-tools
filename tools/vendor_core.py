#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Copy the shared core (core/gem15core) into the ArcGIS toolbox and QGIS
plugin folders, so each can be installed on its own.

    python tools/vendor_core.py           # copy
    python tools/vendor_core.py --check   # report drift, change nothing (exit 1 if any)

Only edit core/gem15core; the copies are overwritten. tests/test_vendor.py
fails if a copy has drifted.
"""

import argparse
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, "core", "gem15core")
TARGETS = [os.path.join(ROOT, "arcgis", "gem15core"),
           os.path.join(ROOT, "qgis", "gem15", "gem15core")]
NOTE_NAME = "VENDORED.txt"
NOTE = ("This folder is a copy of core/gem15core made by tools/vendor_core.py.\n"
        "Do not edit it here: edit core/gem15core and run the script again.\n")


def _sources():
    return sorted(f for f in os.listdir(SOURCE) if f.endswith(".py"))


def _read(path):
    with open(path, "rb") as fh:
        return fh.read()


def sync(check=False, targets=None):
    """Copy (or with check=True, only compare). Returns a list of problems found."""
    problems = []
    names = _sources()
    for target in targets or TARGETS:
        rel = os.path.relpath(target, ROOT)
        if not check:
            os.makedirs(target, exist_ok=True)
        present = set(os.listdir(target)) if os.path.isdir(target) else set()
        for name in names:
            src, dst = os.path.join(SOURCE, name), os.path.join(target, name)
            if name not in present or _read(src) != _read(dst):
                problems.append(f"{rel}/{name} differs from core")
                if not check:
                    shutil.copyfile(src, dst)
        for name in sorted(present):
            if name.endswith(".py") and name not in names:
                problems.append(f"{rel}/{name} is not in core")
                if not check:
                    os.remove(os.path.join(target, name))
        note = os.path.join(target, NOTE_NAME)
        if NOTE_NAME not in present or _read(note) != NOTE.encode("utf-8"):
            problems.append(f"{rel}/{NOTE_NAME} missing or changed")
            if not check:
                with open(note, "wb") as fh:
                    fh.write(NOTE.encode("utf-8"))
    return problems


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.strip().split("\n\n")[0])
    p.add_argument("--check", action="store_true", help="compare only; exit 1 on drift")
    a = p.parse_args(argv)
    problems = sync(check=a.check)
    for line in problems:
        print(("DRIFT " if a.check else "updated ") + line)
    if a.check and problems:
        print("Run: python tools/vendor_core.py")
        return 1
    if not problems:
        print("All copies match core/gem15core.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
