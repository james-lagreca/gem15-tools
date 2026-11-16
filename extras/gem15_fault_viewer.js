// SPDX-License-Identifier: GPL-2.0-or-later
// Part of the GEM-15 tools: https://github.com/james-lagreca/gem15-tools
/**** GEM-15 fault-mapping viewer - Google Earth Engine Code Editor ****
 *
 * Status: syntax-checked only; not yet run in the Code Editor.
 * Data: GEM-15, CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/).
 * "This dataset is produced by Google". Cite Batchu et al. (2026),
 * doi:10.31223/X55N75. Credit it on any map or export. An independent
 * script, not affiliated with or endorsed by Google.
 *
 * Paste into https://code.earthengine.google.com and press Run.
 * Everything is computed on Google's servers; nothing is downloaded.
 * (The Code Editor only runs JavaScript; the same calls work in Python
 * via the earthengine-api / geemap packages.)
 *
 * Choices and assumptions (flagged):
 *  - CENTRE defaults to near St Arnaud, NZ (Alpine-Wairau Fault area), where
 *    LINZ 1 m lidar exists to compare against. Change it to your site; the
 *    script fails if GEM-15 has no tile at CENTRE.
 *  - Derivatives are forced onto the native 15 m UTM grid of the tile under
 *    CENTRE (.reproject at the END of each chain), so they do not change with
 *    zoom. Cost: zoom to ~12 or closer, or EE will be slow or refuse
 *    ("too many pixels"). Areas in a different UTM zone are resampled.
 *  - Multidirectional hillshade = unweighted mean of azimuths 225/270/315/360
 *    at SUN_ELEV. A simple choice, not the USGS weighted MDOW method.
 *  - Local relief model (LRM) = DTM minus a circular focal mean of radius
 *    LRM_RADIUS_M. Tune to roughly 2-5x the landform width you are after.
 *  - DTM - Copernicus GLO-30 mixes EGM96 (GEM-15) and EGM2008 (Copernicus)
 *    geoids, which differ locally (check the difference for your area), so
 *    use this layer for spotting seams and artefacts, not absolute offsets.
 */

// ---------- User settings ----------
var CENTRE = ee.Geometry.Point([172.84, -41.80]);  // [lon, lat]
var ZOOM = 13;
var SUN_ELEV = 30;          // degrees above horizon
var LRM_RADIUS_M = 300;     // metres
var EXPORT_HALF_WIDTH_M = 10000;  // half-width of optional export box (m)

// ---------- Data ----------
var gem = ee.ImageCollection('projects/climate-and-sustainability/assets/gem-15_v1');
var proj = gem.filterBounds(CENTRE).first().select('dtm').projection(); // native UTM, 15 m
var mosaic = gem.mosaic().setDefaultProjection(proj);

var dtm = mosaic.select('dtm');
var dsm = mosaic.select('dsm');
var dsmStd = mosaic.select('dsm_std');
var cop = ee.ImageCollection('COPERNICUS/DEM/GLO30_2024_1').mosaic().select('DEM');

// ---------- Derivatives (all on the native 15 m grid) ----------
function multiHillshade(dem) {
  var azimuths = [225, 270, 315, 360];
  return ee.ImageCollection(azimuths.map(function (az) {
    return ee.Terrain.hillshade(dem, az, SUN_ELEV).toFloat();
  })).mean().reproject(proj);
}

var hsDtm = multiHillshade(dtm);
var hsDsm = multiHillshade(dsm);
var slope = ee.Terrain.slope(dtm).reproject(proj);                       // degrees
var lrm = dtm.subtract(dtm.focalMean(LRM_RADIUS_M, 'circle', 'meters'))  // metres
             .reproject(proj);
var dsmMinusDtm = dsm.subtract(dtm).reproject(proj);  // canopy/buildings: DTM is ML-inferred here
var dtmMinusCop = dtm.subtract(cop).reproject(proj);  // seams, voids, artefacts

// ---------- Map ----------
Map.centerObject(CENTRE, ZOOM);
Map.setOptions('SATELLITE');
var div = ['#2166ac', '#67a9cf', '#f7f7f7', '#ef8a62', '#b2182b'];

Map.addLayer(hsDtm, {min: 60, max: 230}, 'DTM multidirectional hillshade', true);
Map.addLayer(hsDsm, {min: 60, max: 230}, 'DSM multidirectional hillshade', false);
Map.addLayer(lrm, {min: -5, max: 5, palette: div, opacity: 0.6}, 'DTM local relief (+/-5 m)', false);
Map.addLayer(slope, {min: 0, max: 40, palette: ['white', 'black']}, 'DTM slope (0-40 deg)', false);
Map.addLayer(dsmMinusDtm, {min: 0, max: 30, palette: ['white', 'darkgreen']}, 'DSM - DTM (0-30 m)', false);
Map.addLayer(dsmStd.reproject(proj), {min: 0, max: 5, palette: ['white', 'purple']}, 'dsm_std (0-5 m)', false);
Map.addLayer(dtmMinusCop, {min: -10, max: 10, palette: div}, 'DTM - Copernicus GLO-30 (+/-10 m)', false);

print('Native projection used for derivatives:', proj);
print('Tip: Inspector tab (top right) reads values from every layer at a click.');

// ---------- Optional: export a clip to Google Drive (not your PC) ----------
// Uncomment, Run, then start the task in the Tasks tab. Writes float32 GeoTIFF
// (credit it: "Elevation: GEM-15 (CC BY 4.0). This dataset is produced by Google")
// on the native UTM grid, so it lines up with the source pixels.
/*
var box = CENTRE.buffer(EXPORT_HALF_WIDTH_M).bounds();
Export.image.toDrive({
  image: dtm.toFloat(),
  description: 'GEM15_dtm_clip',
  region: box,
  crs: proj.crs(),
  crsTransform: proj.getInfo().transform,
  maxPixels: 1e9
});
*/
