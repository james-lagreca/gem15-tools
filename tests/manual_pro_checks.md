# Manual checks in the ArcGIS Pro window

Everything below is tested headless with arcpy (Pro 3.4) by
`tests/test_arcpy_tools.py` and `tests/test_arcpy_live.py`. These checks
need the Pro window. Start in Victoria (UTM 55S), around 144.1 E, 37.5 S.

Checked in Pro 3.4 (Advanced licence), 2026-11-15: the toolbox loads;
Connect to GEM-15; Add for Extent as a mosaic dataset, including a second
area in another zone; a VRT draws, more slowly than the mosaic.

After updating the toolbox folder: right-click the toolbox > Refresh. Six
tools should show: GEM-15 for Current View and Swath Profile at the top, the
other four in a "Step by step" toolset.

1. **Hover help.** Open each tool: hovering over the Help button shows a
   summary, and each parameter's (i) shows its help. (Headless, arcpy reads
   the help; the dialogs themselves can only be checked in the window.)
2. **GEM-15 for Current View**, zoomed to about 1:100,000, no inputs: a
   group "GEM-15 terrain: ..." with the LRM (40 % transparent) on top, the
   hillshade, and the elevation switched off at the bottom; the tile edges
   dashed and labelled. How long does it take (headless: about 15 s)? Zoom to
   1:50,000: the LRM appears. Its colours should not change as you pan
   (fixed ranges, A13). Turn the hillshade off and the elevation on: it
   draws in grey. The legends read
   "0 (shadow) / 255 (lit)" and "-2.5 m / +2.5 m", not +/-3.40282e+38.
3. **Live layers built on a stand-in (A15).** The terrain layers are made on
   an empty stand-in raster and pointed at the real source. Headless they read
   identical pixels; check they draw everywhere in the tiles, not just in one
   corner, and still draw after saving, closing and reopening the project.
4. **Run Current View again** after panning to a neighbouring tile in the
   same zone: one group, rebuilt, covering both tiles; no second elevation
   layer.
5. **Illumination.** In Current View, Hillshade > Across a fault trend,
   trend 30: the hillshade is lit from 300 degrees. Try vertical
   exaggeration 2.
6. **Canopy check** (Layers > Canopy check) over farmland with tree lines:
   green over trees, nothing over bare paddocks.
7. **Swath Profile**: draw a line across a scarp (the Profile Line parameter
   lets you sketch on the map), give the four distances, run. Check the CSV
   and PNG in the project's gem15\profiles folder and the height in the
   messages.
8. **Add GEM-15 for Extent, Mosaic dataset** with the extent blank (current
   view): the mosaic layer arrives once, draws, and has DRA (Symbology >
   Statistics: Dynamic range adjustment). Run it again: still one layer. Look along tile overlaps for steps (A3; try Mosaic Method = Closest
   to Center).
9. **Add GEM-15 for Extent, VRT file** across zones (central Australia,
   131.5-132.5 E, 25 S): the layer covers whole tiles, named "GEM-15 DTM
   (GDA2020 / GA LCC, n tiles)". Note how long the first draw takes.
10. **Terrain Stack** on a mosaic: how long (headless about 30 s for
    hillshade and LRM)? Panning speed; zooming out over hillshade and slope
    should not crawl.
11. **Clip for Analysis** with the extent blank: the progress bar moves and
    Cancel stops it (no half-written file). The estimate is a message, a
    warning only near the limit. Derivatives with Across a fault trend.
12. **A coordinate system in degrees** (e.g. WGS 84) in Clip or the VRT
    output: an error before running, not a 1-pixel raster.
13. **Antimeridian** (optional): in a map using EPSG 3460 (Fiji), Add for
    Extent should report "(crosses 180 degrees)" and find tiles on both sides.
    A polar map (EPSG 3995) around Svalbard should find the northern tiles.
14. **Basic licence** (needs a machine with a Basic licence):
    Current View should make a VRT; Add for Extent should default to VRT
    file. Without Spatial or Image Analyst, Terrain Stack warns that the LRM
    uses a convolution.
