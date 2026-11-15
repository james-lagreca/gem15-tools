# Manual checks in the ArcGIS Pro window

Everything below was tested headless with arcpy (Pro 3.4) by
`tests/test_arcpy_tools.py` and `tests/test_arcpy_live.py`. These checks
need the Pro window. Use a small area in Victoria first (UTM 55S), for
example around 144.1 E, 37.5 S.

1. **Toolbox loads.** The toolbox has moved to `arcgis/GEM15Tools.pyt`:
   remove the old one, restart Pro, add the new one. Four tools should
   appear: Connect to GEM-15, Add GEM-15 For Extent, Terrain Stack (live)
   and Clip for Analysis.
2. **Connect to GEM-15** from its dialog. `gem15.acs` should appear in the
   project home folder, and the Catalog pane should browse into it.
3. **Add GEM-15 For Extent, Mosaic dataset**, current map extent, coordinate
   system and mosaic name left blank. Messages should say "WGS 84 / UTM
   zone 55S" and "Mosaic dataset name (automatic): GEM15_dtm_UTM55S". The
   layer should draw. Look along tile overlaps for steps (assumption A3; try
   Mosaic Method = Closest to Center). Then run it again over an area in
   another zone, for example central Australia at 130 E (zone 52), name
   still blank: it should make a second mosaic, GEM15_dtm_UTM52S, without
   an error. (On 2026-11-15 this failed: the name always defaulted to
   GEM15_dtm, so the second area was refused.)
4. **Add GEM-15 For Extent, VRT file** (the open question, A8). Does the VRT
   layer draw in the map view, and how fast at different zooms? Try one
   area across UTM zones too (for example NZ at 174 E, or central Australia
   at 132 E), which gives a warped VRT.
5. **Separate layers**: the layers should arrive with DRA on (Symbology >
   Statistics: DRA).
6. **Terrain Stack (live)** on the mosaic layer, then on the VRT layer.
   You should get a group with LRM on top (40 % transparent), slope
   (switched off) and hillshade. Check that:
   - the stretches follow the view (DRA)
   - flat ground in the hillshade is mid grey (low sun at 30 degrees)
   - the LRM disappears when zoomed out past 1:100,000 (by design, A9)
   - panning speed is acceptable, and zooming out over hillshade and slope
     does not crawl (if it does, Pro is computing from full-resolution
     pixels)
   - the layers still draw after saving, closing and reopening the project
7. **Clip for Analysis**: setting the extent should show a rough MB warning
   before the run, and the run should warn with a closer figure. The
   GeoTIFF and any derivatives should be added to the map. This tool runs
   GDAL (osgeo) inside Pro, so report any crash or DLL error.
8. **Antimeridian** (optional): in a map using a CRS centred near 180 (for
   example EPSG 3460, Fiji Map Grid), Add For Extent should report an
   extent with W > E "(crosses 180 degrees)" and find tiles on both sides.
9. **Basic licence** (only if someone has one; this machine is Advanced).
   Mosaic mode should show a licence error. Without Spatial Analyst or
   Image Analyst, Terrain Stack should warn that the LRM uses a convolution.
