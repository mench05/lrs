# -*- coding: utf-8 -*-
import pytest

try:
    from qgis.PyQt.QtCore import QVariant
    from qgis.core import QgsFeature, QgsField, QgsGeometry, QgsProject, QgsVectorLayer
    from lrs.lrscalib import LrsCalib
    from lrs.error.lrserror import LrsError
    QGIS_AVAILABLE = True
except ImportError:
    QGIS_AVAILABLE = False

pytestmark = pytest.mark.skipif(not QGIS_AVAILABLE, reason="PyQGIS runtime is not available")


def make_line_layer(features):
    layer = QgsVectorLayer("LineString?crs=EPSG:25831", "Arcs Graf", "memory")
    pr = layer.dataProvider()
    pr.addAttributes([
        QgsField("CODIVIA", QVariant.String),
        QgsField("DIRECCIO", QVariant.String),
        QgsField("ID", QVariant.String),
        QgsField("IDLRS", QVariant.String),
        QgsField("POSICIOINI", QVariant.Double),
        QgsField("POSICIOFIN", QVariant.Double),
    ])
    layer.updateFields()
    out = []
    for attrs, wkt in features:
        f = QgsFeature(layer.fields())
        f.setGeometry(QgsGeometry.fromWkt(wkt))
        for name, value in attrs.items():
            f[name] = value
        out.append(f)
    pr.addFeatures(out)
    return layer


def make_point_layer(features):
    layer = QgsVectorLayer("Point?crs=EPSG:25831", "PKs", "memory")
    pr = layer.dataProvider()
    pr.addAttributes([
        QgsField("CODIVIA", QVariant.String),
        QgsField("DIRECCIO", QVariant.String),
        QgsField("IDPK", QVariant.String),
        QgsField("VALORPK", QVariant.Double),
    ])
    layer.updateFields()
    out = []
    for attrs, wkt in features:
        f = QgsFeature(layer.fields())
        f.setGeometry(QgsGeometry.fromWkt(wkt))
        for name, value in attrs.items():
            f[name] = value
        out.append(f)
    pr.addFeatures(out)
    return layer


def calibrate(lines, points, **kwargs):
    crs = QgsProject.instance().crs()
    options = dict(crs=crs,
                   useCompositeRouteId=True, compositeRouteFields="CODIVIA,DIRECCIO",
                   useOfficialArcMeasures=True, strictDirection=True,
                   allowSharedGeometryDirections=True, tolerantMode=True,
                   specialRamalHandling=True, specialRoundaboutHandling=True,
                   generateDiagnostics=True)
    options.update(kwargs)
    lrs = LrsCalib(lines, "CODIVIA", points, "CODIVIA", "VALORPK", **options)
    lrs.calibrate()
    return lrs


def error_types(lrs):
    return [e.type for e in lrs.getErrors()]


def test_linear_route_with_official_measures():
    lines = make_line_layer([({
        "CODIVIA": "C-1", "DIRECCIO": "Creixent", "IDLRS": "1",
        "POSICIOINI": 0, "POSICIOFIN": 100,
    }, "LINESTRING(0 0, 100 0)")])
    points = make_point_layer([])

    lrs = calibrate(lines, points)

    assert len(lrs.getParts()) == 1
    assert lrs.getParts()[0].records[0].milestoneFrom == 0
    assert lrs.getParts()[0].records[0].milestoneTo == 100


def test_same_location_pks_different_direction_are_not_deduplicated():
    lines = make_line_layer([
        ({"CODIVIA": "C-1", "DIRECCIO": "Creixent", "IDLRS": "1", "POSICIOINI": 0, "POSICIOFIN": 100},
         "LINESTRING(0 0, 100 0)"),
        ({"CODIVIA": "C-1", "DIRECCIO": "Decreixent", "IDLRS": "1", "POSICIOINI": 100, "POSICIOFIN": 0},
         "LINESTRING(0 0, 100 0)"),
    ])
    points = make_point_layer([
        ({"CODIVIA": "C-1", "DIRECCIO": "Creixent", "IDPK": "PK0C", "VALORPK": 0}, "POINT(0 0)"),
        ({"CODIVIA": "C-1", "DIRECCIO": "Decreixent", "IDPK": "PK0D", "VALORPK": 0}, "POINT(0 0)"),
    ])

    lrs = calibrate(lines, points)

    assert len(lrs.points) == 2
    assert LrsError.DUPLICATE_POINT not in error_types(lrs)
    assert LrsError.PK_SAME_LOCATION_DIFFERENT_DIRECTION in error_types(lrs)


def test_closed_roundabout_is_warning_not_global_failure():
    lines = make_line_layer([({
        "CODIVIA": "C-2", "DIRECCIO": "Creixent", "IDLRS": "R",
        "POSICIOINI": 0, "POSICIOFIN": 20,
    }, "LINESTRING(0 0, 10 0, 10 10, 0 0)")])
    points = make_point_layer([])

    lrs = calibrate(lines, points)

    assert len(lrs.getParts()) == 1
    assert LrsError.CLOSED_RING_OR_ROUNDABOUT in error_types(lrs)


def test_reversed_and_zero_delta_measures_are_warnings():
    lines = make_line_layer([
        ({"CODIVIA": "C-3", "DIRECCIO": "Creixent", "IDLRS": "1", "POSICIOINI": 100, "POSICIOFIN": 0},
         "LINESTRING(0 0, 100 0)"),
        ({"CODIVIA": "C-4", "DIRECCIO": "Creixent", "IDLRS": "1", "POSICIOINI": 0, "POSICIOFIN": 0},
         "LINESTRING(0 1, 100 1)"),
    ])
    points = make_point_layer([])

    lrs = calibrate(lines, points)

    assert LrsError.REVERSED_MEASURES in error_types(lrs)
    assert LrsError.MEASURE_ZERO_WITH_GEOMETRY in error_types(lrs)


def test_single_pk_branch_is_kept_with_warning():
    lines = make_line_layer([({
        "CODIVIA": "N-340_10930-DA", "DIRECCIO": "Creixent", "IDLRS": "1",
        "POSICIOINI": None, "POSICIOFIN": None,
    }, "LINESTRING(0 0, 100 0)")])
    points = make_point_layer([({
        "CODIVIA": "N-340_10930-DA", "DIRECCIO": "Creixent",
        "IDPK": "PK10", "VALORPK": 10,
    }, "POINT(0 0)")])

    lrs = calibrate(lines, points, useOfficialArcMeasures=False)

    assert len(lrs.getParts()) == 1
    assert lrs.getParts()[0].records[0].milestoneFrom == 10
    assert lrs.getParts()[0].records[0].milestoneTo > 10
    assert any(e.type == LrsError.NOT_ENOUGH_MILESTONES and e.severity == 'WARNING'
               for e in lrs.getErrors())


def test_not_enough_points_keeps_route_context():
    lines = make_line_layer([({
        "CODIVIA": "C-5", "DIRECCIO": "Creixent", "IDLRS": "42",
        "POSICIOINI": None, "POSICIOFIN": None,
    }, "LINESTRING(0 0, 100 0)")])
    points = make_point_layer([])

    lrs = calibrate(lines, points, useOfficialArcMeasures=False)

    error = next(e for e in lrs.getErrors() if e.type == LrsError.NOT_ENOUGH_MILESTONES)
    assert error.codivia == "C-5"
    assert error.direccio == "Creixent"
    assert error.idlrs == "42"


def test_endpoints_are_segmented_without_explicit_boundary_pks():
    lines = make_line_layer([({
        "CODIVIA": "C-6", "DIRECCIO": "Creixent", "IDLRS": "6",
        "POSICIOINI": None, "POSICIOFIN": None,
    }, "LINESTRING(0 0, 100 0)")])
    points = make_point_layer([
        ({"CODIVIA": "C-6", "DIRECCIO": "Creixent", "IDPK": "PK20", "VALORPK": 20}, "POINT(20 0)"),
        ({"CODIVIA": "C-6", "DIRECCIO": "Creixent", "IDPK": "PK80", "VALORPK": 80}, "POINT(80 0)"),
    ])

    lrs = calibrate(lines, points, useOfficialArcMeasures=False, extrapolate=False)

    route = lrs.getRouteIfExists("C-6_Creixent")
    assert route is not None
    assert len(route.parts) == 1
    records = route.parts[0].records
    assert len(records) == 3
    assert records[0].milestoneFrom == 0
    assert records[0].milestoneTo == 20
    assert records[-1].milestoneFrom == 80
    assert records[-1].milestoneTo == 100


def test_event_lookup_accepts_small_endpoint_offset():
    lines = make_line_layer([({
        "CODIVIA": "T-333", "DIRECCIO": "Creixent", "IDLRS": "333",
        "POSICIOINI": 26.238, "POSICIOFIN": 26.538,
    }, "LINESTRING(0 0, 300 0)")])
    points = make_point_layer([])

    lrs = calibrate(lines, points)

    point, error = lrs.eventPointXY("T-333_Creixent", 26.13, lrs.defaultMeasureTolerance(150.0))
    assert point is not None
    assert error is None


def test_point_measure_snaps_near_route_endpoint():
    lines = make_line_layer([({
        "CODIVIA": "T-330", "DIRECCIO": "Creixent", "IDLRS": "330",
        "POSICIOINI": 26.1529, "POSICIOFIN": 26.4529,
    }, "LINESTRING(0 0, 300 0)")])
    points = make_point_layer([])

    lrs = calibrate(lines, points)

    snapped = lrs.snapMeasureToRoute("T-330_Creixent", 26.16)
    assert snapped == 26.1529


def test_endpoint_pk_from_other_codivia_can_anchor_terminal_arc():
    lines = make_line_layer([
        ({"CODIVIA": "C-1", "DIRECCIO": "Creixent", "IDLRS": "1", "POSICIOINI": None, "POSICIOFIN": None},
         "LINESTRING(0 0, 100 0)"),
        ({"CODIVIA": "C-2", "DIRECCIO": "Creixent", "IDLRS": "2", "POSICIOINI": None, "POSICIOFIN": None},
         "LINESTRING(100 0, 200 0)"),
    ])
    points = make_point_layer([
        ({"CODIVIA": "C-1", "DIRECCIO": "Creixent", "IDPK": "PK0", "VALORPK": 0}, "POINT(0 0)"),
        ({"CODIVIA": "C-2", "DIRECCIO": "Creixent", "IDPK": "PK100", "VALORPK": 100}, "POINT(100 0)"),
    ])

    lrs = calibrate(lines, points, useOfficialArcMeasures=False, allowForeignEndpointMilestones=True)

    route = lrs.getRouteIfExists("C-1_Creixent")
    assert route is not None
    assert len(route.parts) == 1
    assert route.parts[0].records
    assert route.parts[0].records[0].milestoneFrom == 0
    assert route.parts[0].records[0].milestoneTo == 100
