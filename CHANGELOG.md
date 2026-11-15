# Changelog

## 0.1.0 (unreleased)

First release: an ArcGIS Pro toolbox and a QGIS plugin that stream Google's
GEM-15 15 m elevation for active-fault mapping, sharing one core.

ArcGIS Pro toolbox:
- **GEM-15 for Current View**: no inputs; adds the elevation for the view
  (mosaic dataset, or a VRT with a Basic licence), a live hillshade and local
  relief model in one group, and the tile edges.
- **Swath Profile**: a band profile across a scarp, with a scarp-height
  estimate; CSV and PNG.
- Step by step: Connect to GEM-15, Add GEM-15 for Extent, Terrain Stack,
  Clip for Analysis.
- Hover help on every tool and parameter.

QGIS plugin:
- Toolbar: DTM, DSM, live LRM and canopy check for the current view; LRM
  radius presets; tile edges.
- Processing: Build GEM-15 VRT, Clip GEM-15 to GeoTIFF, Terrain derivatives
  from a clip, Swath profile across a scarp.

Both:
- Automatic coordinate system: the tiles' own UTM zone, else GDA2020 / GA LCC
  (Australia), NZTM2000 (New Zealand) or a projection centred on the area.
- Hillshade: multidirectional, one sun, or lit across a fault trend; vertical
  exaggeration. Fixed colour ranges for the terrain layers.
- Catalogue lookup cached for 30 days; size limits, cancel, clear errors for
  network trouble; GEM-15 credit and caveats on every layer.
