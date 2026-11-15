# SPDX-License-Identifier: GPL-2.0-or-later
"""
Help text for the ArcGIS Pro toolbox: shown when you hover over a tool's
Help button or a parameter's (i) button in the Geoprocessing pane.

ArcGIS Pro reads it from GEM15Tools.<ToolClass>.pyt.xml beside the .pyt;
a Python toolbox cannot hold it itself. Edit the text here, then run
    python tools/build_pyt_help.py
to rewrite those files; a test fails if a parameter has no help or the
files are out of date. Keep each entry to a sentence or three.
"""

_SUN = {
    "illumination": "Multidirectional lights the surface from several directions, so no "
                    "scarp orientation is hidden. A single sun, or one shining across a "
                    "fault trend, gives scarps facing it up to three times the contrast.",
    "azimuth": "Direction the sun shines from, in degrees clockwise from north (for a "
               "single sun). 315 (north-west) is usual.",
    "fault_trend": "Trend of the fault you are mapping, in degrees (either direction "
                   "along it, e.g. 30 for a NNE-SSW fault). The sun is put at right angles "
                   "to it, on the side nearer north-west, so relief does not look inverted.",
    "altitude": "Height of the sun above the horizon, in degrees. A low sun brings out low "
                "scarps: about 20 on plains, 30-35 in steep country.",
    "z_factor": "Vertical exaggeration of the hillshade. 1 shows true relief; 2-3 brings "
                "out subtle scarps on Australian plains, along with the DTM's noise.",
}

_RADIUS = ("Each pixel's height minus the mean height within this distance. Smaller radii "
           "show narrow features: 150 m for offset channels and narrow scarps, 300 m for "
           "scarps tens of metres wide, 1000 m for broad scarps and fold scarps (slow "
           "when live).")

_SR = ("Leave blank so tiles in one UTM zone keep their own 15 m grid. Across zones the "
       "tool picks GDA2020 / GA LCC (Australia), NZTM2000 (New Zealand) or a projection "
       "centred on your area. Choose a projected system in metres, never degrees.")

_PRODUCT = ("DTM: the bare-ground surface, predicted by machine learning; use it for "
            "fault mapping. DSM: the top of trees and buildings (five bands, about five "
            "times the data to stream).")

_EXTENT = "Area to cover. Leave blank for the current map view."

_DERIVS = ("Hillshade (lit from the chosen sun), slope in degrees (added switched off) and "
           "local relief model (LRM: height above or below the local mean, blue to red over "
           "+/- 2.5 m).")

HELP = {
    "GEM15ForCurrentView": {
        "summary": ("Add GEM-15 elevation for the current map view in one step: a live "
                    "hillshade and local relief model in a group, and the tile edges. Zoom "
                    "to your area and press Run; nothing is downloaded beyond what is "
                    "drawn."),
        "usage": [
            "Works for views up to 6 x 6 degrees; use Add GEM-15 for Extent for more.",
            "With a Standard or Advanced licence the elevation is a mosaic dataset; with "
            "Basic, a VRT file. Both stream from Google's public bucket; no account needed.",
            "The local relief model is drawn at 1:50,000 and closer: zoom in to see it.",
            "The DTM is predicted by machine learning: unreliable under trees and buildings "
            "(the canopy check shows where). Straight features along tile edges may be "
            "seams, not scarps. Heights are on the EGM96 geoid, not AHD.",
            "Credit: Elevation: GEM-15, produced by Google (CC BY 4.0). Cite Batchu et al. "
            "(2026), doi:10.31223/X55N75.",
        ],
        "params": {
            "product": _PRODUCT,
            "layers": _DERIVS + " Canopy check: green where the DSM is over 3 m above the "
                      "DTM, i.e. where the DTM is the model's guess (streams the DSM too). "
                      "Tile edges: outlines of the tiles, where seams may be.",
            "lrm_radius": _RADIUS,
            "extent": _EXTENT,
            "mode": ("Automatic: a mosaic dataset with a Standard or Advanced licence, a VRT "
                     "file with Basic. A mosaic dataset is quicker to draw; a VRT file is one "
                     "small file that works with any licence."),
            "sr": _SR,
            **_SUN,
            "out_paths": "The elevation source, tile edges and layer files written.",
        },
    },
    "SwathProfile": {
        "summary": ("Summarise the DEM in a band along a line drawn across a scarp (lowest, "
                    "quartiles, median and highest heights every 15 m), and estimate the "
                    "scarp's height from the far-field surfaces either side."),
        "usage": [
            "Draw the line across the scarp, from the upthrown to the downthrown side or "
            "the reverse; distances are measured from its first point.",
            "For the scarp height, give a distance range on each side, away from the scarp "
            "face. Straight lines are fitted to the median there; their separation at the "
            "scarp's middle is reported with a 1-sigma uncertainty (a lower bound).",
            "Only the DEM around the line is read. Works on GEM-15 mosaic datasets, VRTs "
            "and clips in a projected (metre) coordinate system.",
            "Features narrower than about 45 m (3 pixels) or lower than 1-2 m are beyond "
            "what 15 m, machine-learning elevation supports.",
        ],
        "params": {
            "line": "The profile line: draw one on the map, or choose a line layer (its "
                    "first line is used).",
            "in_raster": "Elevation to profile, e.g. a GEM-15 layer added by GEM-15 for "
                         "Current View, or a clip.",
            "width": "Width of the band sampled either side of the line, in metres. Wider "
                     "bands steady the median but blur where the scarp bends.",
            "out_folder": "Folder for the CSV table and PNG plot (default: the gem15\\profiles "
                          "folder in the project's home folder).",
            "out_name": "Name for the output files (.csv and .png are added).",
            "upper_from": "Start of the far-field range on one side, in metres along the "
                          "line from its first point.",
            "upper_to": "End of that range, in metres along the line.",
            "lower_from": "Start of the far-field range on the other side of the scarp, in "
                          "metres along the line.",
            "lower_to": "End of that range, in metres along the line.",
            "out_csv": "Table of the profile: distance and the summary heights at each step.",
            "out_png": "Plot of the profile with the fitted far-field lines.",
        },
    },
    "ConnectToGEM15": {
        "summary": ("Create gem15.acs, an anonymous, read-only connection to the public "
                    "GEM-15 bucket. The other tools make it themselves when needed."),
        "usage": [
            "No account or key is needed: the bucket is public.",
            "Use this to make a connection under another name or folder, or to clear the "
            "saved tile catalogue.",
        ],
        "params": {
            "out_folder": "Folder for the connection file (default: the project's home "
                          "folder, where the other tools look for it).",
            "out_name": "Name of the connection file.",
            "replace": "Make the file again even if it exists.",
            "clear_cache": ("Delete the saved copy of the tile catalogue, so it is fetched "
                            "again. Only needed if Google changes the catalogue."),
            "out_acs": "The connection file.",
        },
    },
    "AddGEM15ForExtent": {
        "summary": ("Add the GEM-15 tiles covering an extent as one mosaic dataset "
                    "(referenced from the cloud), as separate layers, or as a VRT file "
                    "that streams them."),
        "usage": [
            "Mosaic datasets need a Standard or Advanced licence. Zoomed out over more than "
            "about 20 tiles, a mosaic shows tile outlines only: zoom in.",
            "Nothing is downloaded up front: the layers read only what they draw.",
            "Add a hillshade and local relief model with Terrain Stack.",
        ],
        "params": {
            "extent": _EXTENT,
            "product": _PRODUCT,
            "mode": ("Mosaic dataset: one seamless layer, best for large areas (Standard or "
                     "Advanced licence). Separate layers: one layer per 1-degree tile. VRT "
                     "file: one small file streaming all tiles, any licence, slower to draw "
                     "zoomed out."),
            "acs": "Connection to the bucket. Leave blank to use (or make) gem15.acs in "
                   "the project's home folder.",
            "gdb": "Geodatabase for the mosaic dataset (default: the project's).",
            "md_name": ("Leave blank to name it after its coordinate system (e.g. "
                        "GEM15_dtm_UTM55S), so later runs in the same zone add to the same "
                        "mosaic and an area in another zone gets its own."),
            "sr": _SR,
            "vrt_folder": "Folder for the VRT file (default: the gem15 folder in the "
                          "project's home folder).",
            "band": ("Which DSM band: dsm (the surface), its 5th, 50th or 95th percentile, or "
                     "dsm_std (the spread of heights within each pixel)."),
            "out_md": "The mosaic dataset.",
            "out_paths": "The rasters added (tiles or the VRT).",
            "tile_edges": "Also show the tiles' outlines, where seams between acquisitions "
                          "can look like scarps.",
        },
    },
    "TerrainStack": {
        "summary": ("Add live layers over a GEM-15 DEM: hillshade, slope and local relief "
                    "model, computed as you pan with fixed colour ranges. Nothing is "
                    "downloaded beyond what is drawn."),
        "usage": [
            "The LRM is drawn at 1:50,000 and closer. Without Spatial or Image Analyst it "
            "uses a convolution, blank within the radius of any gap (water).",
            "Layer files (.lyrx) are saved too, so the layers can be added to other maps.",
            "Run it again after adding tiles to a mosaic: the layers are rebuilt to cover "
            "them.",
        ],
        "params": {
            "in_raster": "The elevation layer: a GEM-15 mosaic dataset, VRT or clip.",
            "derivatives": _DERIVS,
            "lrm_radius": _RADIUS,
            "out_folder": "Folder for the layer files (default: the gem15 folder in the "
                          "project's home folder).",
            "out_layers": "The layer files written.",
            **_SUN,
        },
    },
    "ClipForAnalysis": {
        "summary": ("Download the GEM-15 pixels for an extent into a local GeoTIFF, after "
                    "estimating the download, with optional hillshade, slope and local "
                    "relief model GeoTIFFs."),
        "usage": [
            "Tiles in one UTM zone keep their native 15 m grid; across zones they are "
            "resampled (bilinear), which smooths scarp faces: measure on a single-zone "
            "clip where you can.",
            "The DTM costs roughly 150 MB per 1-degree cell at Australian latitudes; the "
            "DSM about five times that. Cancel stops the download.",
            "Draw the extent a little larger than you need: near its edge the LRM uses "
            "part of its circle.",
        ],
        "params": {
            "extent": _EXTENT,
            "product": _PRODUCT,
            "band": ("Which DSM band: dsm (the surface), its 5th, 50th or 95th percentile, or "
                     "dsm_std (the spread of heights within each pixel)."),
            "out_tif": "The GeoTIFF to write (in a folder, not a geodatabase).",
            "sr": ("Leave blank to keep the tiles' native UTM grid (one zone) or pick one "
                   "automatically. Choose a projected system in metres, never degrees."),
            "derivatives": "Also write these as GeoTIFFs beside the clip: " + _DERIVS,
            "lrm_radius": _RADIUS,
            "max_mb": ("The tool stops before reading anything if the estimated download "
                       "is larger. 0 means no limit."),
            "out_derivatives": "The derivative GeoTIFFs written.",
            **{k: v for k, v in _SUN.items() if k != "altitude"},
            "altitude": _SUN["altitude"],
        },
    },
}
