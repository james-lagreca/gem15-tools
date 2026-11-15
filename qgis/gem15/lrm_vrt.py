# SPDX-License-Identifier: GPL-2.0-or-later
"""
Live local relief model (LRM) as a GDAL VRT: DTM minus its mean over a circle.

The VRT holds one KernelFilteredSource whose kernel sums to zero:
1 - 1/N at the centre and -1/N at the other N - 1 cells within the radius
(gem15core.terrain.circle_offsets), 0 outside it. Each pixel is then
DTM - mean(DTM over the circle, centre included), computed by GDAL as QGIS
draws. No GDAL is needed to write the file.

Trial results (2026-11-15, GDAL 3.6.4 in QGIS 3.28.6 and GDAL 3.9.2; see
tests/qgis/test_lrm_live.py):
 L1. Accuracy: matches a numpy reference to 1e-7 m over the whole raster,
     edges included (GDAL repeats edge pixels, as numpy.pad mode "edge").
 L2. Speed: about 0.4 million pixels per second for the 300 m radius
     (41 x 41 kernel, single thread). At 1:25,000 on a 1920 x 1080 screen
     (96 dpi) QGIS reads about 0.4 Mpx: ~1 s per redraw. At 1:40,000 about
     1 Mpx: ~2.5 s. Smaller radii are faster (cost grows with radius squared).
 L3. Zoomed out, GDAL skips the filter: for any read below full resolution
     the VRT returns plain elevation (GDAL 3.9) or values from the source's
     overviews (GDAL 3.6), never the LRM. QGIS reads native pixels when
     zoomed in, so the layer is limited to large scales (settings P3).
 L4. Voids: GDAL treats NaN as an ordinary value here, so any NaN within the
     kernel's square (2r + 1 pixels across) gives NaN: voids and masked water
     grow by the radius (300 m) in the live LRM. The clip-based LRM
     (derivatives.py) ignores NaN neighbours instead.
 L5. Opening the layer makes QGIS ask GDAL for statistics, which GDAL 3.6.4
     samples from reduced-resolution reads (L3) and then fails with "no valid
     pixels". Nominal STATISTICS_* metadata on the band stops that sampling
     (and the network reads it would cost); the values are not used.
"""

import os
import xml.etree.ElementTree as ET

from .gem15core import PIXEL_M, Gem15Error, terrain

# L5 values: not measured, just plausible for an LRM; the plugin styles the
# layer with its own fixed range, so they only stop GDAL sampling the layer.
NOMINAL_STATS = {"STATISTICS_MINIMUM": "-50", "STATISTICS_MAXIMUM": "50",
                 "STATISTICS_MEAN": "0", "STATISTICS_STDDEV": "2",
                 "STATISTICS_APPROXIMATE": "YES"}


def kernel(radius_m=terrain.LRM_RADIUS_M, pixel_m=PIXEL_M):
    """(size, coefficients row by row) of the zero-sum LRM kernel."""
    offs = terrain.circle_offsets(radius_m, pixel_m)
    r = terrain.radius_cells(radius_m, pixel_m)
    size = 2 * r + 1
    n = len(offs)
    coefs = [0.0] * (size * size)
    for dx, dy in offs:
        coefs[(dy + r) * size + (dx + r)] = -1.0 / n
    coefs[r * size + r] += 1.0
    return size, coefs


def write_lrm_vrt(base_vrt, out_path, radius_m=terrain.LRM_RADIUS_M, pixel_m=PIXEL_M):
    """Write an LRM VRT over a single-band elevation VRT (same grid and CRS).

    base_vrt : a VRT written by gem15core (vrt.write_vrt) or by GDAL; its
               size, SRS and geotransform are copied, so no GDAL is needed.
    Returns out_path.
    """
    base = ET.parse(base_vrt).getroot()
    if base.tag != "VRTDataset":
        raise Gem15Error(f"{base_vrt} is not a GDAL VRT")
    size, coefs = kernel(radius_m, pixel_m)
    root = ET.Element("VRTDataset", rasterXSize=base.get("rasterXSize"),
                      rasterYSize=base.get("rasterYSize"))
    for tag in ("SRS", "GeoTransform"):
        el = base.find(tag)
        if el is not None:
            root.append(el)
    md = ET.SubElement(root, "Metadata")
    old = base.find("Metadata")
    if old is not None:
        for item in old.findall("MDI"):
            if item.get("key") != "BAND":
                md.append(item)
    ET.SubElement(md, "MDI", key="DERIVED").text = (
        f"local relief model: elevation minus its mean within {radius_m:g} m")
    ET.SubElement(md, "MDI", key="LRM_RADIUS_M").text = f"{radius_m:g}"

    band = ET.SubElement(root, "VRTRasterBand", dataType="Float32", band="1")
    # L5: nominal statistics, so opening the layer does not make GDAL sample it.
    bmd = ET.SubElement(band, "Metadata")
    for key, val in NOMINAL_STATS.items():
        ET.SubElement(bmd, "MDI", key=key).text = val
    ET.SubElement(band, "Description").text = f"GEM-15 LRM, {radius_m:g} m radius (m)"
    ET.SubElement(band, "NoDataValue").text = "nan"
    ET.SubElement(band, "UnitType").text = "m"
    src = ET.SubElement(band, "KernelFilteredSource")
    out_dir = os.path.dirname(os.path.abspath(out_path))
    base_abs = os.path.abspath(base_vrt)
    if os.path.dirname(base_abs) == out_dir:
        ET.SubElement(src, "SourceFilename", relativeToVRT="1").text = os.path.basename(base_abs)
    else:
        ET.SubElement(src, "SourceFilename", relativeToVRT="0").text = base_abs
    ET.SubElement(src, "SourceBand").text = "1"
    k = ET.SubElement(src, "Kernel", normalized="0")
    ET.SubElement(k, "Size").text = str(size)
    # 17 significant digits, so the coefficients still sum to zero in double precision.
    ET.SubElement(k, "Coefs").text = " ".join(f"{c:.17g}" for c in coefs)
    ET.indent(root)
    os.makedirs(out_dir, exist_ok=True)
    ET.ElementTree(root).write(out_path, encoding="utf-8")
    return out_path


def _source_name(path, out_dir):
    """(text, relativeToVRT) for a source file, relative when beside the VRT."""
    p = os.path.abspath(path)
    return (os.path.basename(p), "1") if os.path.dirname(p) == out_dir else (p, "0")


def write_diff_vrt(minuend_vrt, subtrahend_vrt, out_path, description="DSM minus DTM (m)"):
    """Write a VRT whose band is minuend minus subtrahend (GDAL's built-in
    "diff" pixel function), e.g. DSM - DTM for the canopy check
    (gem15core.terrain T7). Both inputs must share their grid (same size,
    geotransform and CRS); this is checked. Returns out_path."""
    a, b = ET.parse(minuend_vrt).getroot(), ET.parse(subtrahend_vrt).getroot()
    for r in (a, b):
        if r.tag != "VRTDataset":
            raise Gem15Error("write_diff_vrt needs two GDAL VRTs")

    def grid(r):
        return (r.get("rasterXSize"), r.get("rasterYSize"),
                (r.findtext("GeoTransform") or "").replace(" ", ""))
    if grid(a) != grid(b):
        raise Gem15Error("The DSM and DTM VRTs are on different grids; write them with the "
                         "same bounds.")
    root = ET.Element("VRTDataset", rasterXSize=a.get("rasterXSize"),
                      rasterYSize=a.get("rasterYSize"))
    for tag in ("SRS", "GeoTransform"):
        el = a.find(tag)
        if el is not None:
            root.append(el)
    band = ET.SubElement(root, "VRTRasterBand", dataType="Float32", band="1",
                         subClass="VRTDerivedRasterBand")
    bmd = ET.SubElement(band, "Metadata")                 # L5: no sampling on open
    for key, val in NOMINAL_STATS.items():
        ET.SubElement(bmd, "MDI", key=key).text = val
    ET.SubElement(band, "Description").text = description
    ET.SubElement(band, "NoDataValue").text = "nan"
    ET.SubElement(band, "PixelFunctionType").text = "diff"
    out_dir = os.path.dirname(os.path.abspath(out_path))
    for path in (minuend_vrt, subtrahend_vrt):
        src = ET.SubElement(band, "SimpleSource")
        text, rel = _source_name(path, out_dir)
        ET.SubElement(src, "SourceFilename", relativeToVRT=rel).text = text
        ET.SubElement(src, "SourceBand").text = "1"
    ET.indent(root)
    os.makedirs(out_dir, exist_ok=True)
    ET.ElementTree(root).write(out_path, encoding="utf-8")
    return out_path
