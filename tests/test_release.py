# SPDX-License-Identifier: GPL-2.0-or-later
"""Release bookkeeping: one version everywhere, a dated CHANGELOG entry, notes."""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import check_release  # noqa: E402


def test_one_version_everywhere():
    found = check_release.versions()
    assert None not in found.values(), found
    assert len(set(found.values())) == 1, f"versions differ: {found}"


def test_current_version_has_changelog_entry():
    version = next(iter(check_release.versions().values()))
    date, body = check_release.changelog_entry(version)
    assert body, f"CHANGELOG.md has no entry for {version}"
    if date:                       # dated (released): CITATION.cff must agree
        assert check_release.citation_date() == date


def test_wrong_tag_is_refused():
    version = next(iter(check_release.versions().values()))
    bad = check_release.problems("v999.0.0")
    assert len(bad) >= 4 and all("999.0.0" in b for b in bad)
    assert check_release.main([f"v{version}x"]) == 1


def test_notes_name_both_zips(tmp_path):
    version = next(iter(check_release.versions().values()))
    text = check_release.notes(version)
    assert f"gem15-qgis-{version}.zip" in text and f"gem15-arcgis-{version}.zip" in text
    assert check_release.changelog_entry(version)[1].splitlines()[0] in text
