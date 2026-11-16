#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Check a release tag against the version everywhere it is written, and write
the release notes from CHANGELOG.md.

    python tools/check_release.py v0.1.0                  # check only
    python tools/check_release.py v0.1.0 --notes notes.md # also write notes

Stops (exit 1) unless the tag (without its "v") equals the version in
core/gem15core/__init__.py, qgis/gem15/metadata.txt and CITATION.cff, and
CHANGELOG.md has a dated heading "## <version> (YYYY-MM-DD)" whose date is
CITATION.cff's date-released. Standard library only (runs on GitHub Actions
before anything is built).
"""

import argparse
import configparser
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def versions():
    """{place: version string} for every place the version is written."""
    out = {}
    m = re.search(r'^__version__\s*=\s*"([^"]+)"', _read("core", "gem15core", "__init__.py"), re.M)
    out["core/gem15core/__init__.py"] = m.group(1) if m else None
    cp = configparser.ConfigParser()
    cp.read_string(_read("qgis", "gem15", "metadata.txt"))
    out["qgis/gem15/metadata.txt"] = cp["general"].get("version")
    m = re.search(r"^version:\s*\"?([^\"\s]+)\"?\s*$", _read("CITATION.cff"), re.M)
    out["CITATION.cff"] = m.group(1) if m else None
    return out


def citation_date():
    m = re.search(r"^date-released:\s*\"?(\d{4}-\d{2}-\d{2})\"?\s*$", _read("CITATION.cff"), re.M)
    return m.group(1) if m else None


def changelog_entry(version):
    """(date, body) of the CHANGELOG.md section for `version`; date is None
    when the heading has no date (e.g. "(unreleased)")."""
    text = _read("CHANGELOG.md")
    head = re.search(rf"^## {re.escape(version)}(?: \(([^)]*)\))?\s*$", text, re.M)
    if not head:
        return None, None
    nxt = re.search(r"^## ", text[head.end():], re.M)
    body = text[head.end():head.end() + nxt.start()] if nxt else text[head.end():]
    date = head.group(1) if head.group(1) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", head.group(1)) else None
    return date, body.strip()


def problems(tag):
    """List of reasons the tag cannot be released (empty when all is well)."""
    want = tag[1:] if tag.startswith("v") else tag
    out = [f"{place} says {v!r}, the tag says {want!r}"
           for place, v in versions().items() if v != want]
    date, body = changelog_entry(want)
    if body is None:
        out.append(f"CHANGELOG.md has no '## {want} (YYYY-MM-DD)' entry")
    elif date is None:
        out.append(f"CHANGELOG.md's {want} entry has no date: '## {want} (YYYY-MM-DD)'")
    elif citation_date() != date:
        out.append(f"CITATION.cff date-released {citation_date()!r} differs from "
                   f"CHANGELOG.md's {date!r}")
    return out


def notes(version):
    """Release notes: which zip is which, then the CHANGELOG entry."""
    _, body = changelog_entry(version)
    return (f"**QGIS:** `gem15-qgis-{version}.zip` (Plugins > Manage and Install "
            "Plugins > Install from ZIP).  \n"
            f"**ArcGIS Pro:** `gem15-arcgis-{version}.zip` (unzip, then Add Toolbox "
            "> GEM15Tools.pyt).  \n"
            "Not the *Source code* zips. Full steps and the caveats: README.\n\n"
            + (body or "") + "\n")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.strip().split("\n\n")[0])
    p.add_argument("tag", help="the release tag, e.g. v0.1.0")
    p.add_argument("--notes", help="write the release notes to this file")
    a = p.parse_args(argv)
    bad = problems(a.tag)
    if bad:
        print("Not releasable:\n- " + "\n- ".join(bad), file=sys.stderr)
        return 1
    if a.notes:
        with open(a.notes, "w", encoding="utf-8") as fh:
            fh.write(notes(a.tag.lstrip("v")))
    print(f"{a.tag}: versions agree; CHANGELOG entry dated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
