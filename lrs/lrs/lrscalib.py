# -*- coding: utf-8 -*-
"""
/***************************************************************************
 LrsPlugin
                                 A QGIS plugin
 Linear reference system builder and editor
                              -------------------
        begin                : 2013-10-02
        copyright            : (C) 2013 by Radim Blažek
        email                : radim.blazek@gmail.com
 ***************************************************************************/

/***************************************************************************
 *                                                                         *
 *   This program is free software; you can redistribute it and/or modify  *
 *   it under the terms of the GNU General Public License as published by  *
 *   the Free Software Foundation; either version 2 of the License, or     *
 *   (at your option) any later version.                                   *
 *                                                                         *
 ***************************************************************************/
"""
# Import the PyQt and QGIS libraries
# from PyQt4.QtGui import *
from .error.lrserror import *
from .lrsbase import LrsBase
from .lrsline import LrsLine
from .lrspoint import LrsPoint
from .lrscalibroute import LrsCalibRoute


# Main class to keep all data and process them
class LrsCalib(LrsBase):
    progressChanged = pyqtSignal(str, float, name='progressChanged')
    updateErrors = pyqtSignal(dict, name='updateErrors')
    edited = pyqtSignal(name='edited')

    # progress counts
    CURRENT = 1  # current progress count
    TOTAL = 2  # sum of steps to do
    NLINES = 3  # number of lines
    NPOINTS = 4  # number of points
    NROUTES = 5  # number of routes

    # calibration steps
    REGISTERING_LINES = 1
    REGISTERING_POINTS = 2
    CALIBRATING_ROUTES = 3

    stateLabels = {
        REGISTERING_LINES: 'Registering lines',
        REGISTERING_POINTS: 'Registering points',
        CALIBRATING_ROUTES: 'Calibrating routes',
    }

    def __init__(self, lineLayer, lineRouteField, pointLayer, pointRouteField, pointMeasureField, **kwargs):
        super(LrsCalib, self).__init__(**kwargs)

        self.lineLayer = lineLayer
        self.lineRouteField = lineRouteField
        self.pointLayer = pointLayer
        self.pointRouteField = pointRouteField
        self.pointMeasureField = pointMeasureField
        # selectionMode: all,include,exclude selection
        self.selectionMode = kwargs.get('selectionMode', 'all')
        # selection is list of route ids to be included/excluded
        self.selection = []
        for routeId in kwargs.get('selection', []):
            self.selection.append(normalizeRouteId(routeId))
        # max lines gaps snap
        self.snap = kwargs.get('snap', 0.0)
        # threshold - max distance between point and line in canvas CRS units
        self.threshold = kwargs.get('threshold', 10.0)
        self.parallelMode = kwargs.get('parallelMode', 'error')
        self.crs = kwargs.get('crs')
        self.useCompositeRouteId = kwargs.get('useCompositeRouteId', False)
        self.compositeRouteFields = kwargs.get('compositeRouteFields', ['CODIVIA', 'DIRECCIO'])
        if isinstance(self.compositeRouteFields, str):
            self.compositeRouteFields = [f.strip() for f in self.compositeRouteFields.split(',') if f.strip()]
        self.useOfficialArcMeasures = kwargs.get('useOfficialArcMeasures', False)
        self.tolerantMode = kwargs.get('tolerantMode', True)
        self.strictDirection = kwargs.get('strictDirection', False)
        self.allowSharedGeometryDirections = kwargs.get('allowSharedGeometryDirections', True)
        self.specialRamalHandling = kwargs.get('specialRamalHandling', True)
        self.specialRoundaboutHandling = kwargs.get('specialRoundaboutHandling', True)
        self.generateDiagnostics = kwargs.get('generateDiagnostics', True)
        self.roundaboutMode = kwargs.get('roundaboutMode', 'independent')
        self.codiviaField = kwargs.get('codiviaField', 'CODIVIA')
        self.directionField = kwargs.get('directionField', 'DIRECCIO')
        self.idlrsField = kwargs.get('idlrsField', 'IDLRS')
        self.lineFallbackIdField = kwargs.get('lineFallbackIdField', 'ID')
        self.idpkField = kwargs.get('idpkField', 'IDPK')
        self.positionPkField = kwargs.get('positionPkField', 'POSICIOPK')
        self.lineMeasureFromField = kwargs.get('lineMeasureFromField', 'POSICIOINI')
        self.lineMeasureToField = kwargs.get('lineMeasureToField', 'POSICIOFIN')
        self.officialArcMeasureScale = kwargs.get('officialArcMeasureScale', 'auto')
        self.officialArcMeasureScaleResolved = 1.0
        self.officialArcMeasureTransforms = {}
        self.ramalSuffixes = kwargs.get('ramalSuffixes', None)


        self.distanceArea = QgsDistanceArea()
        # QgsDistanceArea.setSourceCrs( QgsCoordinateReferenceSystem ) is missing in SIP in at least QGIS 2.0
        self.distanceArea.setSourceCrs(self.crs, QgsProject.instance().transformContext())
        if self.crs.mapUnits() == QgsUnitTypes.DistanceDegrees:
            ellipsoid = self.crs.ellipsoidAcronym()
            if not ellipsoid: ellipsoid = "WGS84"
            self.distanceArea.setEllipsoid(ellipsoid)

        # extrapolate LRS before/after calibration points
        self.extrapolate = kwargs.get('extrapolate', False)

        # stored line route id QgsField to know type
        self.routeField = None

        self.pointLayer.editingStarted.connect(self.pointLayerEditingStarted)
        self.pointLayer.editingStopped.connect(self.pointLayerEditingStopped)
        self.pointEditBuffer = None
        if self.pointLayer.editBuffer():  # layer is already in editing mode
            self.pointLayerEditingStarted()

        self.lineLayer.editingStarted.connect(self.lineLayerEditingStarted)
        self.lineLayer.editingStopped.connect(self.lineLayerEditingStopped)
        self.lineEditBuffer = None
        if self.lineLayer.editBuffer():
            self.lineLayerEditingStarted()

        self.lines = {}  # dict of LrsLine with fid as key
        self.points = {}  # dict of LrsPoint with fid as key

        self.errors = []  # LrsError list
        self.lineGeometryDirections = {}
        self.pointLocationDirections = {}
        self.routeDirections = {}
        self._sharedGeometryDiagnostics = set()
        self._pkLocationDiagnostics = set()

        self.progressCounts = {}

        # Numbers of features/lines/points currently not used because did not 
        # correspond well to list/layer of errors
        self.stats = {}  # statistics
        self.statsNames = (
            # ( 'lineFeatures', 'Total number of line features' ), # may be multilinestrings
            # ( 'lineFeaturesIncluded', 'Number of included line features' ), # selected
            # ( 'lines', 'Total number of line strings' ), # may be parts of multi
            # ( 'linesIncluded', 'Number of included line strings' ),
            # ( 'pointFeatures', 'Total number of point features' ), #may be multipoint
            # ( 'pointFeaturesIncluded', 'Number of included point features' ), # selected
            # ( 'points', 'Total number of points' ), # may be parts of multi
            # ( 'pointsIncluded', 'Number of included points' ), # selected
            # ( 'pointsOk', 'Number of included points successfully used in LRS' ),
            # ( 'pointsError', 'Number of included points with error' ),
            ('length', 'Total length of all lines'),
            ('lengthIncluded', 'Length of included lines'),
            ('lengthOk', 'Length of successfully created LRS'),
            ('routes', 'Logical routes created'),
            ('warnings', 'Non fatal warnings'),
            ('fatalErrors', 'Fatal errors'),
            ('ramals', 'Branch arcs'),
            ('closedRings', 'Closed rings/roundabouts'),
            ('reversedMeasures', 'Arcs with reversed measures'),
            ('sharedGeometryDirections', 'Shared geometries by direction'),
            ('sameLocationDifferentDirectionPk', 'PKs same location by direction'),
            # ( 'lengthError', 'Length of included lines without LRS' ),
        )

        self.lineTransform = None
        if self.crs and self.crs != lineLayer.crs():
            self.lineTransform = QgsCoordinateTransform(lineLayer.crs(), self.crs,
                                                        QgsProject.instance())

        self.pointTransform = None
        if self.crs and self.crs != pointLayer.crs():
            self.pointTransform = QgsCoordinateTransform(pointLayer.crs(), self.crs,
                                                         QgsProject.instance())

        self.wasEdited = False  # true if layers were edited since calibration

        QgsProject.instance().layersWillBeRemoved.connect(self.layersWillBeRemoved)

    def __del__(self):
        self.disconnect()

    def pointLayerDisconnect(self):
        if not self.pointLayer: return
        self.pointLayerEditingDisconnect()
        self.pointLayer.editingStarted.disconnect(self.pointLayerEditingStarted)
        self.pointLayer.editingStopped.disconnect(self.pointLayerEditingStopped)

    def lineLayerDisconnect(self):
        if not self.lineLayer: return
        self.lineLayerEditingDisconnect()
        self.lineLayer.editingStarted.disconnect(self.lineLayerEditingStarted)
        self.lineLayer.editingStopped.disconnect(self.lineLayerEditingStopped)

    def disconnect(self):
        QgsProject.instance().layersWillBeRemoved.disconnect(self.layersWillBeRemoved)
        self.pointLayerDisconnect()
        self.lineLayerDisconnect()

    def layersWillBeRemoved(self, layerIdList):
        project = QgsProject.instance()
        for id in layerIdList:
            if self.pointLayer and self.pointLayer.id() == id:
                self.pointLayerDisconnect()
                self.pointLayer = None
            if self.lineLayer and self.lineLayer.id() == id:
                self.lineLayerDisconnect()
                self.lineLayer = None

    # ------------------- COMMON -------------------
    # get route by id, create it if does not exist
    # routeId does not have to be normalized
    def getRoute(self, routeId):
        normalId = normalizeRouteId(routeId)
        # debug ( 'normalId = %s orig type = %s' % (normalId, type(routeId) ) )
        if normalId not in self.routes:
            self.routes[normalId] = LrsCalibRoute(self.lineLayer, routeId, self.snap, self.threshold, self.crs,
                                                  self.measureUnit, self.distanceArea, parallelMode=self.parallelMode,
                                                  useOfficialArcMeasures=self.useOfficialArcMeasures,
                                                  tolerantMode=self.tolerantMode,
                                                  strictDirection=self.strictDirection,
                                                  allowSharedGeometryDirections=self.allowSharedGeometryDirections,
                                                  specialRamalHandling=self.specialRamalHandling,
                                                  specialRoundaboutHandling=self.specialRoundaboutHandling,
                                                  roundaboutMode=self.roundaboutMode)
        return self.routes[normalId]


    # test if process route according to selectionMode and selection
    def routeIdSelected(self, routeId):
        routeId = normalizeRouteId(routeId)
        if self.selectionMode == 'all':
            return True
        elif self.selectionMode == 'include':
            return routeId in self.selection
        elif self.selectionMode == 'exclude':
            return routeId not in self.selection

    def getFeatureAttrs(self, feature):
        attrs = {}
        for field in feature.fields():
            attrs[field.name()] = feature[field.name()]
        return attrs

    def geometrySignature(self, geo):
        if not geo:
            return None
        box = geo.boundingBox()
        # Avoid using the full WKB as a per-feature dictionary key. On official
        # road layers that can be very large and makes Registering lines crawl.
        return (
            round(box.xMinimum(), 6), round(box.yMinimum(), 6),
            round(box.xMaximum(), 6), round(box.yMaximum(), 6),
            round(geo.length(), 6)
        )

    def featureRouteId(self, feature, routeFieldName):
        if self.useCompositeRouteId:
            routeId = buildCompositeRouteId(feature, routeFieldName,
                                            self.compositeRouteFields,
                                            [self.lineFallbackIdField, 'ID'])
        else:
            routeId = feature[routeFieldName]
            if self.strictDirection:
                direction = cleanRouteIdPart(featureValue(feature, self.directionField, None))
                base = cleanRouteIdPart(routeId)
                if base and direction:
                    routeId = '%s_%s' % (base, direction)
        if routeId == '' or routeId == NULL:
            routeId = None
        return routeId

    def addDiagnostic(self, type, geo, **kwargs):
        if not self.generateDiagnostics:
            return
        severity = kwargs.pop('severity', 'WARNING')
        routeId = kwargs.pop('routeId', None)
        self.errors.append(LrsError(type, geo, routeId=routeId, severity=severity, **kwargs))

    def countErrorStats(self):
        warnings = 0
        errors = 0
        reversedMeasures = 0
        closedRings = 0
        ramals = 0
        shared = 0
        samePk = 0
        for error in self.getErrors():
            if error.severity == 'ERROR':
                errors += 1
            elif error.severity == 'WARNING':
                warnings += 1
            if error.type == LrsError.REVERSED_MEASURES:
                reversedMeasures += 1
            elif error.type == LrsError.CLOSED_RING_OR_ROUNDABOUT:
                closedRings += 1
            elif error.type == LrsError.RAMAL_INDEPENDENT_ROUTE:
                ramals += 1
            elif error.type == LrsError.SHARED_GEOMETRY_MULTIPLE_DIRECTIONS:
                shared += 1
            elif error.type == LrsError.PK_SAME_LOCATION_DIFFERENT_DIRECTION:
                samePk += 1
        self.stats['warnings'] = warnings
        self.stats['fatalErrors'] = errors
        self.stats['reversedMeasures'] = reversedMeasures
        self.stats['closedRings'] = closedRings
        self.stats['ramals'] = ramals
        self.stats['sharedGeometryDirections'] = shared
        self.stats['sameLocationDifferentDirectionPk'] = samePk

    def logSummary(self):
        logInfo('LRS calibration: %s arcs processed, %s PKs processed, %s logical routes created' %
                (len(self.lines), len(self.points), len(self.routes)))
        directions = {}
        for routeId, direction in self.routeDirections.items():
            directions[direction] = directions.get(direction, 0) + 1
        logInfo('LRS calibration: routes by direction %s' % directions)
        logInfo('LRS calibration: %s shared geometries, %s PK same location/different direction, '
                '%s branches, %s closed rings, %s reversed measures, %s warnings, %s errors' %
                (self.stats.get('sharedGeometryDirections', 0),
                 self.stats.get('sameLocationDifferentDirectionPk', 0),
                 self.stats.get('ramals', 0),
                 self.stats.get('closedRings', 0),
                 self.stats.get('reversedMeasures', 0),
                 self.stats.get('warnings', 0),
                 self.stats.get('fatalErrors', 0)))

    def officialMeasureFieldsAvailable(self):
        return fieldExists(self.lineLayer, self.lineMeasureFromField) and fieldExists(self.lineLayer,
                                                                                     self.lineMeasureToField)

    def resolveOfficialArcMeasureScale(self):
        if self.officialArcMeasureScale != 'auto':
            try:
                return float(self.officialArcMeasureScale)
            except (TypeError, ValueError):
                return 1.0

        # Keep official arc measures in their native unit unless explicitly set.
        # VALORPK/POSICIOPK is handled below as a route transform, not a plain
        # offset, because VALORPK=48 and POSICIOPK=48000 need scale 0.001.
        return 1.0

    def median(self, values):
        values = sorted(values)
        if not values:
            return None
        return values[len(values) // 2]

    def inferPkTransform(self, pairs):
        # pairs are (position measure, selected PK measure)
        if not pairs:
            return 1.0, 0.0

        scales = []
        for i in range(len(pairs) - 1):
            p1, v1 = pairs[i]
            for j in range(i + 1, len(pairs)):
                p2, v2 = pairs[j]
                dp = p2 - p1
                dv = v2 - v1
                if not doubleNear(dp, 0.0):
                    scales.append(dv / dp)

        scale = self.median(scales)
        if scale is None:
            position, selected = pairs[0]
            if abs(position) > 100 * max(abs(selected), 1):
                scale = 0.001
            else:
                scale = 1.0

        offsets = [selected - scale * position for position, selected in pairs]
        return scale, self.median(offsets) or 0.0

    def buildOfficialArcMeasureTransforms(self):
        transforms = {}
        if not self.useOfficialArcMeasures:
            return transforms
        if self.pointMeasureField == self.positionPkField:
            return transforms
        if not fieldExists(self.pointLayer, self.positionPkField):
            return transforms

        pairsByRoute = {}
        for feature in self.pointLayer.getFeatures():
            routeId = self.featureRouteId(feature, self.pointRouteField)
            if routeId is None:
                continue
            selectedMeasure = toFloatOrNone(featureValue(feature, self.pointMeasureField, None))
            positionMeasure = toFloatOrNone(featureValue(feature, self.positionPkField, None))
            if selectedMeasure is None or positionMeasure is None:
                continue
            pairsByRoute.setdefault(normalizeRouteId(routeId), []).append((positionMeasure, selectedMeasure))

        for routeId, pairs in pairsByRoute.items():
            transforms[routeId] = self.inferPkTransform(pairs)

        if transforms:
            logInfo('LRS calibration: applying route transforms from %s against %s for official arc measures' %
                    (self.pointMeasureField, self.positionPkField))
        return transforms

    def detectSameArcMultiplePkDirections(self):
        if not self.allowSharedGeometryDirections:
            return
        seen = {}
        sqrThreshold = self.threshold * self.threshold
        lineIndex = QgsSpatialIndex()
        lineByIndexFid = {}
        idxFid = 1
        for line in self.lines.values():
            if not line.geo:
                continue
            feature = QgsFeature(idxFid)
            feature.setGeometry(line.geo)
            lineIndex.insertFeature(feature)
            lineByIndexFid[idxFid] = line
            idxFid += 1

        for point in self.points.values():
            if not point.geo:
                continue
            searchRect = point.geo.boundingBox()
            searchRect.grow(self.threshold)
            for idx in lineIndex.intersects(searchRect):
                line = lineByIndexFid[idx]
                if line.codivia is not None and point.codivia is not None and line.codivia != point.codivia:
                    continue
                pts = [point.geo.asPoint()] if QgsWkbTypes.isSingleType(point.geo.wkbType()) else point.geo.asMultiPoint()
                for pnt in pts:
                    sqDist, nearestPnt, afterVertex, leftOf = line.geo.closestSegmentWithContext(pnt, 0)
                    if sqDist > sqrThreshold:
                        continue
                    directions = seen.setdefault(line.fid, {})
                    directions.setdefault(point.direccio, []).append(point.fid)
                    if len([d for d in directions if d is not None]) > 1:
                        diag_key = (line.fid, point.direccio)
                        if getattr(self, '_sameArcDirectionDiagnostics', None) is None:
                            self._sameArcDirectionDiagnostics = set()
                        if diag_key not in self._sameArcDirectionDiagnostics:
                            self._sameArcDirectionDiagnostics.add(diag_key)
                            self.addDiagnostic(LrsError.SAME_ARC_MULTIPLE_PK_DIRECTIONS, point.geo,
                                               routeId=point.routeId, severity='WARNING', elementType='PK',
                                               codivia=point.codivia, direccio=point.direccio, idpk=point.idpk,
                                               measure=point.measure,
                                               message='Same arc has PKs from multiple directions')

    # ------------------- GENERATE (CALIBRATE) -------------------

    def updateProgressTotal(self):
        cnts = self.progressCounts
        cnts[self.TOTAL] = cnts[self.NLINES]
        cnts[self.TOTAL] += cnts[self.NPOINTS]
        cnts[self.TOTAL] += cnts[self.NROUTES]  # calibrate routes
        # debug ("%s" % cnts )

    # increase progress, called after each step (line, point...)
    def progressStep(self, state):
        self.progressCounts[self.CURRENT] = self.progressCounts.get(self.CURRENT, 0) + 1
        percent = 100 * self.progressCounts[self.CURRENT] / self.progressCounts[self.TOTAL]
        # debug ( "percent = %s %s / %s" % (percent, self.progressCounts[self.CURRENT], self.progressCounts[self.TOTAL] ) )
        self.progressChanged.emit(self.stateLabels[state], percent)

    def calibrate(self):
        # debug ( 'Lrs.calibrate' )
        self.progressChanged.emit(self.stateLabels[self.REGISTERING_LINES], 0)

        self.points = {}
        self.lines = {}
        self.errors = []  # reset
        self.lineGeometryDirections = {}
        self.pointLocationDirections = {}
        self.routeDirections = {}
        self._sharedGeometryDiagnostics = set()
        self._pkLocationDiagnostics = set()
        self._sameArcDirectionDiagnostics = set()
        self.officialArcMeasureTransforms = {}

        self.stats = {}
        for s in self.statsNames:
            self.stats[s[0]] = 0

        if self.crs and self.crs.mapUnits() == QgsUnitTypes.DistanceDegrees:
            self.addDiagnostic(LrsError.CRS_IN_DEGREES, QgsGeometry(), severity='WARNING',
                               elementType='PROJECT',
                               message='Project CRS uses degrees; distance thresholds are angular')

        if self.useOfficialArcMeasures and not self.officialMeasureFieldsAvailable():
            self.useOfficialArcMeasures = False
            logWarning('LRS calibration: official arc measures disabled because POSICIOINI/POSICIOFIN fields were not found')

        if self.useOfficialArcMeasures and self.pointMeasureField != self.positionPkField and fieldExists(
                self.pointLayer, self.positionPkField):
            self.useOfficialArcMeasures = False
            logWarning('LRS calibration: official arc measures disabled because selected PK measure field is %s, not %s' %
                       (self.pointMeasureField, self.positionPkField))

        officialArcMeasureScale = self.resolveOfficialArcMeasureScale()
        self.officialArcMeasureScaleResolved = officialArcMeasureScale
        self.officialArcMeasureTransforms = self.buildOfficialArcMeasureTransforms()
        field = self.lineLayer.fields().field(self.lineRouteField)
        self.routeField = QgsField(field.name(), field.type(), field.typeName(), field.length(), field.precision())
        if self.useCompositeRouteId or self.strictDirection:
            self.routeField = QgsField(field.name(), QVariant.String, "string")

        self.progressCounts = {}
        # we don't know progressTotal at the beginning, but we can estimate it
        self.progressCounts[self.NLINES] = self.lineLayer.featureCount()
        self.progressCounts[self.NPOINTS] = self.pointLayer.featureCount()
        # estimation (precise later when routes are built)
        self.progressCounts[self.NROUTES] = self.progressCounts[self.NLINES]
        self.updateProgressTotal()

        self.registerLines(officialArcMeasureScale)
        self.registerPoints()
        self.detectSameArcMultiplePkDirections()
        for route in list(self.routes.values()):
            route.calibrate(self.extrapolate)
            self.progressStep(self.CALIBRATING_ROUTES)
            QgsApplication.processEvents()

            # count stats
        for route in self.routes.values():
            # self.stats['pointsOk'] += len ( route.getGoodMilestones() )
            self.stats['lengthOk'] += route.getGoodLength()

            # self.stats['pointsError'] = self.stats['pointsIncluded'] - self.stats['pointsOk']
        self.stats['routes'] = len(self.routes)
        self.countErrorStats()
        self.logSummary()

    def isCalibrated(self):
        return len(self.routes) > 0

    # -------------------------------- register / unregister features ----------------------

    def registerLineFeature(self, feature, officialArcMeasureScale=1.0):
        routeId = self.featureRouteId(feature, self.lineRouteField)
        # debug ( "fid = %s routeId = %s" % ( feature.id(), routeId ) )

        if not self.routeIdSelected(routeId):
            return None

        geo = feature.geometry()
        if geo:
            if self.lineTransform:
                geo.transform(self.lineTransform)

        codivia = featureValue(feature, self.codiviaField, feature[self.lineRouteField])
        direccio = featureValue(feature, self.directionField, None)
        idlrs = featureValueFromAny(feature, [self.idlrsField, self.lineFallbackIdField], None)
        measureFrom = toFloatOrNone(featureValue(feature, self.lineMeasureFromField, None))
        measureTo = toFloatOrNone(featureValue(feature, self.lineMeasureToField, None))
        if measureFrom is not None:
            measureFrom *= officialArcMeasureScale
        if measureTo is not None:
            measureTo *= officialArcMeasureScale
        scale, offset = self.officialArcMeasureTransforms.get(normalizeRouteId(routeId), (1.0, 0.0))
        if measureFrom is not None:
            measureFrom = measureFrom * scale + offset
        if measureTo is not None:
            measureTo = measureTo * scale + offset
        isRamal = isRamalCode(codivia, self.ramalSuffixes)

        if routeId is None:
            self.addDiagnostic(LrsError.ROUTE_ID_NULL, geo or QgsGeometry(), severity='ERROR',
                               elementType='ARC', codivia=codivia, direccio=direccio, idlrs=idlrs,
                               message='Line route id is null')

        route = self.getRoute(routeId)
        line = LrsLine(feature.id(), routeId, geo, measureFrom=measureFrom,
                       measureTo=measureTo, codivia=codivia, direccio=direccio, idlrs=idlrs, isRamal=isRamal)
        self.lines[feature.id()] = line
        route.addLine(line)

        if direccio:
            self.routeDirections[normalizeRouteId(routeId)] = direccio

        if geo and self.allowSharedGeometryDirections:
            key = self.geometrySignature(geo)
            directions = self.lineGeometryDirections.setdefault(key, {})
            directions.setdefault(direccio, []).append(feature.id())
            diag_key = (key, direccio)
            if len([d for d in directions if d is not None]) > 1 and diag_key not in self._sharedGeometryDiagnostics:
                self._sharedGeometryDiagnostics.add(diag_key)
                self.addDiagnostic(LrsError.SHARED_GEOMETRY_MULTIPLE_DIRECTIONS, geo, routeId=routeId,
                                   severity='WARNING', elementType='ARC', codivia=codivia, direccio=direccio,
                                   idlrs=idlrs,
                                   message='Same arc geometry participates in multiple logical directions')

        return line

    def unregisterLineByFid(self, fid):
        line = self.lines[fid]
        route = self.getRoute(line.routeId)
        route.removeLine(fid)
        del self.lines[fid]

    def registerLines(self, officialArcMeasureScale=1.0):
        self.routes = {}
        count = 0
        for feature in self.lineLayer.getFeatures():
            line = self.registerLineFeature(feature, officialArcMeasureScale)
            # self.stats['lineFeatures'] += 1
            length = 0
            if feature.geometry():
                length = self.distanceArea.measureLength(feature.geometry())
            self.stats['length'] += length
            if line:
                # self.stats['lineFeaturesIncluded'] += 1
                # self.stats['linesIncluded'] += line.getNumParts()
                self.stats['lengthIncluded'] += length
            self.progressStep(self.REGISTERING_LINES)
            count += 1
            if count % 250 == 0:
                QgsApplication.processEvents()
            # precise number of routes
        self.progressCounts[self.NROUTES] = len(self.routes)
        self.updateProgressTotal()

    # returns LrsPoint
    def registerPointFeature(self, feature):
        routeId = self.featureRouteId(feature, self.pointRouteField)

        if not self.routeIdSelected(routeId):
            return None

        measure = feature[self.pointMeasureField]
        if measure == NULL: measure = None
        if measure is not None:
            # convert to float to don't care later about operations with integers
            measure = float(measure)
        # debug ( "fid = %s routeId = %s measure = %s" % ( feature.id(), routeId, measure ) )
        geo = feature.geometry()
        if geo:
            if self.pointTransform:
                geo.transform(self.pointTransform)

        codivia = featureValue(feature, self.codiviaField, feature[self.pointRouteField])
        direccio = featureValue(feature, self.directionField, None)
        idpk = featureValue(feature, self.idpkField, None)

        if routeId is None:
            self.addDiagnostic(LrsError.ROUTE_ID_NULL, geo or QgsGeometry(), severity='ERROR',
                               elementType='PK', codivia=codivia, direccio=direccio, idpk=idpk,
                               message='Point route id is null')

        point = LrsPoint(feature.id(), routeId, measure, geo, codivia=codivia, direccio=direccio, idpk=idpk)
        self.points[feature.id()] = point
        route = self.getRoute(routeId)
        route.addPoint(point)

        if geo and self.allowSharedGeometryDirections:
            pts = [geo.asPoint()] if QgsWkbTypes.isSingleType(geo.wkbType()) else geo.asMultiPoint()
            for pnt in pts:
                ph = pointHash(pnt)
                directions = self.pointLocationDirections.setdefault(ph, {})
                directions.setdefault(direccio, []).append(feature.id())
                diag_key = (ph, direccio)
                if len([d for d in directions if d is not None]) > 1 and diag_key not in self._pkLocationDiagnostics:
                    self._pkLocationDiagnostics.add(diag_key)
                    self.addDiagnostic(LrsError.PK_SAME_LOCATION_DIFFERENT_DIRECTION, geo, routeId=routeId,
                                       severity='WARNING', elementType='PK', codivia=codivia, direccio=direccio,
                                       idpk=idpk, measure=measure,
                                       message='PKs share coordinates but belong to different directions')
        return point

    def unregisterPointByFid(self, fid):
        point = self.points[fid]
        route = self.getRoute(point.routeId)
        route.removePoint(fid)
        del self.points[fid]

    def registerPoints(self):
        count = 0
        for feature in self.pointLayer.getFeatures():
            point = self.registerPointFeature(feature)
            # self.stats['pointFeatures'] += 1
            # if point:
            # self.stats['pointFeaturesIncluded'] += 1
            # self.stats['pointsIncluded'] += point.getNumParts()
            self.progressStep(self.REGISTERING_POINTS)
            count += 1
            if count % 250 == 0:
                QgsApplication.processEvents()
            # route total may increase (e.g. orphans)
        self.progressCounts[self.NROUTES] = len(self.routes)
        self.updateProgressTotal()

    # -------------------33

    def getRouteIds(self):
        ids = []
        for route in self.routes.values():
            if route.routeId is not None:
                ids.append(route.routeId)

        ids.sort()
        return ids

    def getErrors(self):
        errors = list(self.errors)
        for route in self.routes.values():
            errors.extend(route.getErrors())
        return errors

    def getParts(self):
        parts = []
        for route in self.routes.values():
            parts.extend(route.parts)
        return parts

    def getSegments(self):
        segments = []
        for route in self.routes.values():
            segments.extend(route.getSegments())
        return segments

    def getQualityFeatures(self):
        features = []
        for route in self.routes.values():
            features.extend(route.getQualityFeatures())
        return features

    # ----------------------------- Editing ---------------------------------
    def pointLayerEditingStarted(self):
        self.pointEditBuffer = self.pointLayer.editBuffer()
        self.pointEditBuffer.featureAdded.connect(self.pointFeatureAdded)
        self.pointEditBuffer.featureDeleted.connect(self.pointFeatureDeleted)
        # some versions of PyQt fail (Win build) with new style connection if the signal has multiple params
        # self.pointEditBuffer.geometryChanged.connect( self.pointGeometryChanged )
        # QObject.connect(self.pointEditBuffer, SIGNAL("geometryChanged(QgsFeatureId, QgsGeometry &)"),
        #                self.pointGeometryChanged)
        # Not working:
        #self.pointEditBuffer.geometryChanged["QgsFeatureId, QgsGeometry"].connect(self.pointGeometryChanged)
        # Trying simple connect hoping that it works also on Windows
        self.pointEditBuffer.geometryChanged.connect(self.pointGeometryChanged)
        self.pointEditBuffer.attributeValueChanged.connect(self.pointAttributeValueChanged)

    def pointLayerEditingStopped(self):
        self.pointEditBuffer = None

    def pointLayerEditingDisconnect(self):
        if self.pointEditBuffer:
            self.pointEditBuffer.featureAdded.disconnect(self.pointFeatureAdded)
            self.pointEditBuffer.featureDeleted.disconnect(self.pointFeatureDeleted)
            self.pointEditBuffer.geometryChanged.disconnect(self.pointGeometryChanged)
            self.pointEditBuffer.attributeValueChanged.disconnect(self.pointAttributeValueChanged)

    def lineLayerEditingStarted(self):
        self.lineEditBuffer = self.lineLayer.editBuffer()
        self.lineEditBuffer.featureAdded.connect(self.lineFeatureAdded)
        self.lineEditBuffer.featureDeleted.connect(self.lineFeatureDeleted)
        # some versions of PyQt fail (Win build) with new style connection if the signal has multiple params
        # self.lineEditBuffer.geometryChanged.connect( self.lineGeometryChanged )
        # QObject.connect(self.lineEditBuffer, SIGNAL("geometryChanged(QgsFeatureId, QgsGeometry &)"),
        #                self.lineGeometryChanged)
        # Not working:
        #self.lineEditBuffer.geometryChanged["QgsFeatureId, QgsGeometry"].connect(self.lineGeometryChanged)
        # Trying simple connect hoping that it works also on Windows
        self.lineEditBuffer.geometryChanged.connect(self.lineGeometryChanged)
        self.lineEditBuffer.attributeValueChanged.connect(self.lineAttributeValueChanged)

    def lineLayerEditingStopped(self):
        self.lineEditBuffer = None

    def lineLayerEditingDisconnect(self):
        if self.lineEditBuffer:
            self.lineEditBuffer.featureAdded.disconnect(self.lineFeatureAdded)
            self.lineEditBuffer.featureDeleted.disconnect(self.lineFeatureDeleted)
            self.lineEditBuffer.geometryChanged.disconnect(self.lineGeometryChanged)
            self.lineEditBuffer.attributeValueChanged.disconnect(self.lineAttributeValueChanged)

    def setEdited(self):
        self.wasEdited = True
        self.edited.emit()

    def getEdited(self):
        return self.wasEdited

    def emitUpdateErrors(self, errorUpdates):
        errorUpdates['crs'] = self.crs
        self.updateErrors.emit(errorUpdates)

    # Warning: featureAdded is called first with temporary (negative fid)
    # then, when changes are commited, featureDeleted is called with that 
    # temporary id and featureAdded with real new id,
    # if changes are rollbacked, only featureDeleted is called

    # ------------------- point edit -------------------
    def pointFeatureAdded(self, fid):
        # added features have temporary negative id
        # debug ( "feature added fid %s" % fid )
        self.setEdited()
        feature = getLayerFeature(self.pointLayer, fid)
        point = self.registerPointFeature(feature)  # returns LrsPoint
        if not point: return  # route id not in selection

        route = self.getRoute(point.routeId)
        errorUpdates = route.calibrateAndGetUpdates(self.extrapolate)
        self.emitUpdateErrors(errorUpdates)

    def pointFeatureDeleted(self, fid):
        # debug ( "feature deleted fid %s" % fid )
        self.setEdited()
        # deleted feature cannot be read anymore from layer
        point = self.points.get(fid)
        if not point: return  # route id not in selection

        route = self.getRoute(point.routeId)
        self.unregisterPointByFid(fid)
        errorUpdates = route.calibrateAndGetUpdates(self.extrapolate)
        self.emitUpdateErrors(errorUpdates)

    def pointGeometryChanged(self, fid, geo):
        # debug ( "geometry changed fid %s" % fid )
        self.setEdited()

        # remove old
        point = self.points.get(fid)
        if not point: return  # route id not in selection

        route = self.getRoute(point.routeId)
        self.unregisterPointByFid(fid)

        # add new
        feature = getLayerFeature(self.pointLayer, fid)
        self.registerPointFeature(feature)

        errorUpdates = route.calibrateAndGetUpdates(self.extrapolate)
        self.emitUpdateErrors(errorUpdates)

    def pointAttributeValueChanged(self, fid, attIdx, value):
        # debug ( "attribute changed fid = %s attIdx = %s value = %s " % (fid, attIdx, value) )
        self.setEdited()

        fields = self.pointLayer.fields()
        routeIdx = fields.indexFromName(self.pointRouteField)
        measureIdx = fields.indexFromName(self.pointMeasureField)
        compositeIdxs = [fields.indexFromName(name) for name in self.compositeRouteFields]
        compositeIdxs.append(fields.indexFromName(self.directionField))
        # debug ( "routeIdx = %s measureIdx = %s" % ( routeIdx, measureIdx) )

        if attIdx == routeIdx or attIdx == measureIdx or attIdx in compositeIdxs:
            point = self.points.get(fid)
            if point:  # was in selection
                route = self.getRoute(point.routeId)
                self.unregisterPointByFid(fid)

                if attIdx == routeIdx:
                    # recalibrate old
                    errorUpdates = route.calibrateAndGetUpdates(self.extrapolate)
                    self.emitUpdateErrors(errorUpdates)

            feature = getLayerFeature(self.pointLayer, fid)
            point = self.registerPointFeature(feature)  # returns LrsPoint
            if point:  # route id in selection
                route = self.getRoute(point.routeId)
                errorUpdates = route.calibrateAndGetUpdates(self.extrapolate)
                self.emitUpdateErrors(errorUpdates)

    # --------------------------- line edit ---------------------------
    def lineFeatureAdded(self, fid):
        # added features have temporary negative id
        # debug ( "feature added fid %s" % fid )
        self.setEdited()
        feature = getLayerFeature(self.lineLayer, fid)
        line = self.registerLineFeature(feature, self.officialArcMeasureScaleResolved)  # returns LrsLine
        if not line: return  # route id not in selection

        route = self.getRoute(line.routeId)
        errorUpdates = route.calibrateAndGetUpdates(self.extrapolate)
        self.emitUpdateErrors(errorUpdates)

    def lineFeatureDeleted(self, fid):
        # debug ( "feature deleted fid %s" % fid )
        # deleted feature cannot be read anymore from layer
        self.setEdited()
        line = self.lines.get(fid)
        if not line: return  # route id not in selection

        route = self.getRoute(line.routeId)
        self.unregisterLineByFid(fid)
        errorUpdates = route.calibrateAndGetUpdates(self.extrapolate)
        self.emitUpdateErrors(errorUpdates)

    def lineGeometryChanged(self, fid, geo):
        # debug ( "geometry changed fid %s" % fid )
        self.setEdited()

        # remove old
        line = self.lines.get(fid)
        if not line: return  # route id not in selection

        route = self.getRoute(line.routeId)
        self.unregisterLineByFid(fid)

        # add new
        feature = getLayerFeature(self.lineLayer, fid)
        self.registerLineFeature(feature, self.officialArcMeasureScaleResolved)

        errorUpdates = route.calibrateAndGetUpdates(self.extrapolate)
        self.emitUpdateErrors(errorUpdates)

    def lineAttributeValueChanged(self, fid, attIdx, value):
        # debug ( "attribute changed fid = %s attIdx = %s value = %s " % (fid, attIdx, value) )
        self.setEdited()

        fields = self.lineLayer.fields()
        routeIdx = fields.indexFromName(self.lineRouteField)
        compositeIdxs = [fields.indexFromName(name) for name in self.compositeRouteFields]
        compositeIdxs.append(fields.indexFromName(self.directionField))
        compositeIdxs.append(fields.indexFromName(self.lineMeasureFromField))
        compositeIdxs.append(fields.indexFromName(self.lineMeasureToField))
        # debug ( "routeIdx = %s" % ( routeIdx, measureIdx) )

        if attIdx == routeIdx or attIdx in compositeIdxs:
            line = self.lines.get(fid)
            if line:  # was in selection
                route = self.getRoute(line.routeId)
                self.unregisterLineByFid(fid)
                errorUpdates = route.calibrateAndGetUpdates(self.extrapolate)
                self.emitUpdateErrors(errorUpdates)

            feature = getLayerFeature(self.lineLayer, fid)

            line = self.registerLineFeature(feature, self.officialArcMeasureScaleResolved)  # returns LrsLine
            if line:  # route id in selection
                route = self.getRoute(line.routeId)
                errorUpdates = route.calibrateAndGetUpdates(self.extrapolate)
                self.emitUpdateErrors(errorUpdates)

    # ------------------- STATS -------------------
    def getStatsHtmlRow(self, name, label):
        # return "%s : %s<br>" % ( label, self.stats[name] )
        value = self.stats[name]
        # lengths are in map units not in measure units
        # if 'length' in name.lower():
        #    value = formatMeasure( value, self.measureUnit )
        return "<tr><td>%s</td> <td>%s</td></tr>" % (label, value)

    def getStatsHtml(self):
        html = '''<html><head>
                    <style type="text/css">
                      table {
                        border: 1px solid gray;
                        border-spacing: 0px;
                      }
                      td {
                        padding: 5px;
                        border: 1px solid gray;
                        font-size: 10pt;
                      }
                      body {
                        font-size: 10pt;
                      }
                    </style>
                  </head><body>'''
        html += '<table border="1">'

        for s in self.statsNames:
            html += self.getStatsHtmlRow(s[0], s[1])

        html += '</table>'
        html += '<p>Lengths in map units.'
        html += '</body></html>'
        return html

    # get statistics
    def getStat(self, name):
        return self.stats[name]
