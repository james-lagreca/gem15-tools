# SPDX-License-Identifier: GPL-2.0-or-later
"""Group and cell names (pure Python)."""
from gem15core import cells


def test_parse_group_live_examples():
    # Names seen in the live catalogue on 2026-11-15.
    assert cells.parse_group("m50_p170") == (170, -50, 180, -40)
    assert cells.parse_group("p30_m120") == (-120, 30, -110, 40)
    assert cells.parse_group("p0_p0") == (0, 0, 10, 10)
    assert cells.parse_group("m10_p0") == (0, -10, 10, 0)
    assert cells.parse_group("p0_m10") == (-10, 0, 0, 10)
    assert cells.parse_group("catalog") is None
    assert cells.parse_group("") is None


def test_parse_cell_live_examples():
    assert cells.parse_cell("w172sm42") == (172, -42, 173, -41)
    assert cells.parse_cell("wm111s30") == (-111, 30, -110, 31)
    assert cells.parse_cell("gem-15_w170sm43") == (170, -43, 171, -42)
    assert cells.parse_cell("w0s0") == (0, 0, 1, 1)
    assert cells.parse_cell("wm1sm1") == (-1, -1, 0, 0)
    assert cells.parse_cell("w172") is None


def test_names_round_trip_worldwide():
    for lon in range(-180, 180, 7):
        for lat in range(-61, 84, 5):
            x, y = lon + 0.3, lat + 0.6
            w, s, e, n = cells.parse_cell(cells.cell_name(x, y))
            assert w <= x < e and s <= y < n and e - w == 1
            w, s, e, n = cells.parse_group(cells.group_name(x, y))
            assert w <= x < e and s <= y < n and e - w == 10


def test_names_at_edges():
    assert cells.cell_name(172.5, -41.5) == "w172sm42"
    assert cells.group_name(172.5, -41.5) == "m50_p170"
    assert cells.cell_name(180, 0.5) == "w179s0"          # 180 belongs to the last cell
    assert cells.cell_name(-0.5, -0.5) == "wm1sm1"
    assert cells.group_name(-0.5, -0.5) == "m10_m10"
    assert cells.group_name(0, 0) == "p0_p0"


def test_cell_names_for_bbox():
    assert cells.cell_names_for_bbox((172.6, -41.95, 173.2, -41.65)) == ["w172sm42", "w173sm42"]
    # Touching along an edge does not count.
    assert cells.cell_names_for_bbox((172, -42, 173, -41)) == ["w172sm42"]
