# -*- coding: utf-8 -*-
"""
/***************************************************************************
 LrsPlugin
                                 A QGIS plugin
 Linear reference system builder and editor
                              -------------------
        begin                : 2017-5-29
        copyright            : (C) 2017 by Radim Blažek
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

from .error.lrserror import *


# Base class for Lrs and LrsLayer
class LrsBase(QObject):
    def __init__(self, **kwargs):
        super(LrsBase, self).__init__()
        self.lineLayer = None
        self.crs = None
        # dictionary of LrsRoute, key is normalized route id
        self.routes = {}  # LrsRoutBase subclasses
        # self.mapUnitsPerMeasureUnit = kwargs.get('mapUnitsPerMeasureUnit',1000.0)
        self.measureUnit = kwargs.get('measureUnit', LrsUnits.UNKNOWN)
        # LrsLayer has LrsUnits.UNKNOWN, there is no such info available
        #if self.measureUnit == LrsUnits.UNKNOWN:
        #    raise Exception("measureUnit not set")

        self.partSpatialIndex = None
        self.partSpatialIndexRoutePart = None

    def defaultMeasureTolerance(self, meters=150.0):
        if self.measureUnit == LrsUnits.METER:
            return meters
        if self.measureUnit == LrsUnits.KILOMETER:
            return meters / 1000.0
        if self.measureUnit == LrsUnits.FEET:
            return meters * 3.2808399
        if self.measureUnit == LrsUnits.MILE:
            return meters / 1609.344
        # Conservative fallback for cached/imported LRS where the unit is not explicit.
        return meters / 1000.0

    def snapMeasureToRoute(self, routeId, measure, tolerance=None):
        if measure is None:
            return None
        route = self.getRouteIfExists(routeId)
        if not route:
            return measure
        tolerance = self.defaultMeasureTolerance(120.0) if tolerance is None else tolerance
        nearest = None
        nearestDelta = sys.float_info.max
        for part in route.parts:
            for record in getattr(part, 'records', []):
                for endpoint in (record.milestoneFrom, record.milestoneTo):
                    if endpoint is None:
                        continue
                    delta = abs(endpoint - measure)
                    if delta <= tolerance and delta < nearestDelta:
                        nearest = endpoint
                        nearestDelta = delta
        return nearest if nearest is not None else measure

    def reset(self):
        self.routes = {}
        self.partSpatialIndex = None
        self.partSpatialIndexRoutePart = None

    # get route by id if exists otherwise returns None
    # routeId does not have to be normalized
    def getRouteIfExists(self, routeId):
        normalId = normalizeRouteId(routeId)
        if not normalId in self.routes:
            return None
        return self.routes[normalId]

    # get list of available measures ( (from, to),.. )
    def getRouteMeasureRanges(self, routeId):
        routeId = normalizeRouteId(routeId)
        if routeId not in self.routes:
            return []
        return self.routes[routeId].getMeasureRanges()

    # tolerance - maximum accepted measure from start to nearest existing lrs if exact start measure was not found
    # returns ( QgsPointXY, error )
    def eventPointXY(self, routeId, start, tolerance=0, startOffset=0.0):
        error = self.eventValuesError(routeId, start)
        if error: return None, error

        route = self.getRoute(routeId)
        geo, error = route.eventPointXY(start, tolerance, startOffset)
        return geo, error

    # tolerance - minimum missing gap which will be reported as error
    # returns ( QgsMultiPolyline, error )
    def eventMultiPolyLine(self, routeId, start, end, tolerance=0, oStart=0.0, oEnd=0.0):
        #debug("eventMultiPolyLine start = %s end = %s" % (start, end))
        error = self.eventValuesError(routeId, start, end, True)
        if error:
            return None, error

        route = self.getRoute(routeId)
        geo, error = route.eventMultiPolyLine(start, end, tolerance, oStart, oEnd)
        return geo, error

    # ------------------- EVENTS -------------------

    def eventValuesError(self, routeId, start, end=None, linear=False):
        error = None
        missing = []
        if routeId is None:
            missing.append('route')
        if start is None:
            missing.append('start measure')
        if linear and end is None:
            missing.append('end measure')

        if missing:
            error = 'missing %s value' % ' and '.join(missing)

        route = self.getRouteIfExists(routeId)
        #debug("eventValuesError start = %s end = %s" % (start, end))
        if not route:
            error = error + ', ' if error else ''
            error += 'route not available'

        return error

    # ------------------- MEASURE -------------------

    def deletePartSpatialIndex(self):
        if self.partSpatialIndex:
            del self.partSpatialIndex
        self.partSpatialIndex = None
        self.partSpatialIndexRoutePart = None

    def createPartSpatialIndex(self):
        self.deletePartSpatialIndex()
        self.partSpatialIndex = QgsSpatialIndex()
        self.partSpatialIndexRoutePart = {}
        fid = 1
        count = 0
        for route in self.routes.values():
            for i in range(len(route.parts)):
                feature = QgsFeature(fid)
                geo = QgsGeometry.fromPolylineXY(route.parts[i].polyline)
                feature.setGeometry(geo)
                self.partSpatialIndex.addFeature(feature)
                self.partSpatialIndexRoutePart[fid] = [route.routeId, i]
                fid += 1
                count += 1
                if count % 500 == 0:
                    QgsApplication.processEvents()

    # returns nearest routeId, partIdx within threshold
    def nearestRoutePart(self, point, threshold):
        result = self.nearestRoutePartMeasure(point, threshold)
        if result:
            return result[0], result[1]
        return None, None

    def nearestRoutePartMeasure(self, point, threshold, routeIds=None):
        if not self.partSpatialIndex:
            self.createPartSpatialIndex()
        rect = QgsRectangle(point.x() - threshold, point.y() - threshold, point.x() + threshold, point.y() + threshold)
        ids = self.partSpatialIndex.intersects(rect)
        routeFilter = set(normalizeRouteId(routeId) for routeId in routeIds) if routeIds else None
        candidates = []
        for id in ids:
            routeId, partIdx = self.partSpatialIndexRoutePart[id]
            if routeFilter and normalizeRouteId(routeId) not in routeFilter:
                continue
            route = self.getRoute(routeId)
            part = route.parts[partIdx]
            geo = QgsGeometry.fromPolylineXY(part.polyline)
            (sqDist, nearestPnt, afterVertex, leftOf) = geo.closestSegmentWithContext(point, 0)
            dist = math.sqrt(sqDist)
            if dist <= threshold:
                candidates.append((dist, routeId, partIdx, part))

        candidates.sort(key=lambda candidate: candidate[0])
        for dist, routeId, partIdx, part in candidates:
            measure = part.pointMeasure(point)
            if measure is not None:
                return routeId, partIdx, measure, dist

        return None

    # return routeId, measure
    # Note: it may happen that nearest point (projected) has no record on part,
    # in that case is returned None even if another record may be in threshold,
    # this is currently feature
    # TODO: search for nearest available referenced segments (records) instead
    # of part polylines?
    def pointMeasure(self, point, threshold):
        result = self.nearestRoutePartMeasure(point, threshold)
        if result:
            return result[0], self.snapMeasureToRoute(result[0], result[2])

        return None, None

    def pointMeasureForRoutes(self, point, threshold, routeIds=None):
        result = self.nearestRoutePartMeasure(point, threshold, routeIds)
        if result:
            return result[0], self.snapMeasureToRoute(result[0], result[2]), result[3]
        return None, None, None
