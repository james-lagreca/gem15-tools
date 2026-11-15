"""UTM helpers and the automatic CRS choice. The GDAL and arcpy checks are
skipped where those libraries are missing."""
import pytest

from gem15core import Gem15Error, crs


def test_utm_helpers():
    assert crs.utm_zone_of_epsg(32759) == (59, "S")
    assert crs.utm_zone_of_epsg(32612) == (12, "N")
    assert crs.utm_zone_of_epsg(7845) is None
    assert crs.utm_epsg(55, True) == 32755 and crs.utm_epsg(12, False) == 32612
    with pytest.raises(Gem15Error):
        crs.utm_epsg(61, True)
    assert crs.central_meridian(55) == 147 and crs.central_meridian(1) == -177
    assert crs.zone_distance(60, 1) == 1 and crs.zone_distance(55, 52) == 3


def test_utm_zone_for_including_exceptions():
    assert crs.utm_zone_for(147, -37) == 55
    assert crs.utm_zone_for(129.9, -25) == 52
    assert crs.utm_zone_for(172.5, -41) == 59
    assert crs.utm_zone_for(-111, 30) == 12
    assert crs.utm_zone_for(180, 0) == 1           # 180 E is -180
    assert crs.utm_zone_for(5, 60) == 32           # Norway
    assert crs.utm_zone_for(15, 78) == 33          # Svalbard
    assert crs.utm_epsg_for(147, -37) == 32755


def test_auto_single_zone_keeps_native_grid():
    c = crs.auto_crs((144.0, -37.6, 144.3, -37.4), [32755, 32755])
    assert (c.kind, c.epsg, c.gdal_string()) == ("utm", 32755, "EPSG:32755")
    assert c.max_scale_error < 0.001
    assert "UTM zone 55S" in c.describe()


def test_auto_australia_and_new_zealand():
    au = crs.auto_crs((129.0, -26.0, 140.0, -20.0), [32752, 32753, 32754])
    assert (au.kind, au.epsg) == ("regional", 7845)
    # GA LCC's parallels (18 S, 36 S) are far apart: ~1.2 % scale error midway.
    assert 0.005 < au.max_scale_error < 0.015
    nz = crs.auto_crs((173.8, -41.8, 174.2, -41.6), [32759, 32760])
    assert (nz.kind, nz.epsg) == ("regional", 2193)


def test_auto_custom_projections():
    tall = crs.auto_crs((-121.0, 32.0, -116.0, 42.0), [32610, 32611])   # California
    assert tall.kind == "custom" and tall.projection.kind == "tmerc"
    assert tall.projection.k0 < 1 and tall.max_scale_error < 0.002
    assert tall.projection.lon0 == -118.5
    wide = crs.auto_crs((-110.0, 35.0, -80.0, 45.0), [32612, 32613, 32617])  # USA
    assert wide.projection.kind == "lcc" and wide.max_scale_error < 0.01
    eq = crs.auto_crs((95.0, -8.0, 140.0, 6.0), [32647, 32653])           # Indonesia
    assert eq.projection.kind in ("merc", "lcc") and eq.max_scale_error < 0.01
    fiji = crs.auto_crs((177.0, -19.0, -178.0, -16.0), [32760, 32701])    # crosses 180
    assert fiji.kind == "custom" and fiji.projection.lon0 == 179.5
    assert fiji.max_scale_error < 0.001
    assert fiji.esri_wkt().startswith('PROJCS["GEM-15_custom_')


def test_auto_errors():
    with pytest.raises(Gem15Error):
        crs.auto_crs((-100, 0, 0, 10), [32610, 32630])     # 100 degrees wide
    with pytest.raises(Gem15Error):
        crs.auto_crs((1, 1, 2, 2), [])


def test_secant_tm_balances_error():
    p = crs.custom_projection((10.0, 40.0, 20.0, 50.0))
    centre_err = abs(p.scale(p.lon0, 45) - 1)
    edge_err = max(abs(p.scale(lon, lat) - 1) for lon in (10, 20) for lat in (40, 50))
    assert abs(centre_err - edge_err) < 0.2 * edge_err


# --------------------------------------------------------------------------
# The CRS strings must mean the same thing to GDAL (and arcpy)
# --------------------------------------------------------------------------
SAMPLES = [
    ((-121.0, 32.0, -116.0, 42.0), (-117.3, 36.1)),
    ((-110.0, 35.0, -80.0, 45.0), (-95.0, 41.0)),
    ((95.0, -8.0, 140.0, 6.0), (120.0, -2.0)),
    ((177.0, -19.0, -178.0, -16.0), (-179.2, -17.5)),
]


def _osr():
    osr = pytest.importorskip("osgeo.osr")
    return osr


def _xy(osr, wkt_or_proj, lon, lat, esri=False):
    s = osr.SpatialReference()
    if esri:
        assert s.ImportFromESRI([wkt_or_proj]) == 0
    else:
        assert s.ImportFromProj4(wkt_or_proj) == 0
    s.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    g = osr.SpatialReference()
    g.ImportFromEPSG(4326)
    g.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return osr.CoordinateTransformation(g, s).TransformPoint(lon, lat)[:2]


def test_proj_and_esri_strings_agree_in_gdal():
    osr = _osr()
    for bbox, (lon, lat) in SAMPLES:
        p = crs.custom_projection(bbox)
        a = _xy(osr, p.proj4(), lon, lat)
        b = _xy(osr, p.esri_wkt("t"), lon, lat, esri=True)
        assert a == pytest.approx(b, abs=0.001), (p, a, b)


def test_utm_wkt_matches_epsg():
    osr = _osr()
    for epsg in (32759, 32612, 32701, 32660):
        s = osr.SpatialReference(wkt=crs.utm_wkt(epsg))
        e = osr.SpatialReference()
        e.ImportFromEPSG(epsg)
        assert s.IsSame(e), epsg
        assert s.GetAuthorityCode(None) == str(epsg)


def test_region_constants_match_epsg():
    osr = _osr()
    for r in crs.REGIONS:
        e = osr.SpatialReference()
        e.ImportFromEPSG(r.epsg)
        p = r.projection
        if p.kind == "lcc":
            assert e.GetProjParm("standard_parallel_1") == p.sp1
            assert e.GetProjParm("standard_parallel_2") == p.sp2
        else:
            assert e.GetProjParm("scale_factor") == p.k0
        assert e.GetProjParm("central_meridian") == p.lon0


@pytest.mark.arcpy
def test_esri_wkt_loads_in_arcpy_and_projects_like_gdal():
    osr = _osr()
    arcpy = pytest.importorskip("arcpy")
    wgs = arcpy.SpatialReference(4326)
    for bbox, (lon, lat) in SAMPLES:
        p = crs.custom_projection(bbox)
        sr = arcpy.SpatialReference()
        sr.loadFromString(p.esri_wkt("GEM15_test"))
        assert sr.type == "Projected"
        pt = arcpy.PointGeometry(arcpy.Point(lon, lat), wgs).projectAs(sr).firstPoint
        assert (pt.X, pt.Y) == pytest.approx(_xy(osr, p.proj4(), lon, lat), abs=0.01), p
