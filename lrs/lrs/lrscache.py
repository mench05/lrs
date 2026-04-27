# -*- coding: utf-8 -*-
"""
Cached calibrated LRS loaded from QGIS project custom properties.
"""

from .error.lrserror import *
from .lrsbase import LrsBase
from .lrslayerpart import LrsLayerPart
from .lrslayerroute import LrsLayerRoute


class LrsCache(LrsBase):
    def __init__(self, cacheData):
        super(LrsCache, self).__init__(measureUnit=cacheData.get('measureUnit', LrsUnits.UNKNOWN))
        self.cacheData = cacheData
        self.errors = []
        self.stats = cacheData.get('stats', {})
        self.crs = QgsCoordinateReferenceSystem(cacheData.get('crs', ''))
        routeField = cacheData.get('routeField', {})
        self.routeField = makeField(routeField.get('name', 'route'),
                                    routeField.get('type', QVariant.String),
                                    routeField.get('length', 0),
                                    routeField.get('precision', 0))
        self.load()

    def load(self):
        self.reset()
        for partData in self.cacheData.get('parts', []):
            routeId = partData.get('routeId')
            coords = partData.get('coords') or []
            if len(coords) < 2:
                continue

            line = QgsLineString()
            for coord in coords:
                if len(coord) < 3:
                    continue
                point = QgsPoint(float(coord[0]), float(coord[1]))
                point.addMValue(float(coord[2]))
                line.addVertex(point)

            if line.numPoints() < 2:
                continue

            route = self.getRoute(routeId)
            route.addPart(LrsLayerPart(QgsGeometry(line)))

        for route in self.routes.values():
            route.checkPartOverlaps()

    def disconnect(self):
        pass

    def isCalibrated(self):
        return len(self.routes) > 0

    def getRoute(self, routeId):
        normalId = normalizeRouteId(routeId)
        if normalId not in self.routes:
            self.routes[normalId] = LrsLayerRoute(routeId, parallelMode='error')
        return self.routes[normalId]

    def getRouteIds(self):
        ids = []
        for route in self.routes.values():
            if route.routeId is not None:
                ids.append(route.routeId)
        ids.sort()
        return ids

    def getErrors(self):
        return self.errors

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
        return []

    def getEdited(self):
        return False

    def getStat(self, name):
        return self.stats.get(name, 0)

    def getStatsHtml(self):
        html = '<html><body><table border="1">'
        for name, value in sorted(self.stats.items()):
            html += '<tr><td>%s</td><td>%s</td></tr>' % (name, value)
        html += '</table><p>Loaded from project cache.</p></body></html>'
        return html
