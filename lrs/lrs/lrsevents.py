# -*- coding: utf-8 -*-
"""
/***************************************************************************
 LrsDockWidget
                                 A QGIS plugin
 Linear reference system builder and editor
                             -------------------
        begin                : 2017-5-20
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
from html import escape

from .utils import *


# Generates events
class LrsEvents(QObject):
    def __init__(self, lrs, progressBar=None):
        # debug( "LrsEvents.__init__")
        # self.iface = iface
        self.lrs = lrs  # Lrs object
        self.progressBar = progressBar
        self._routeIds = None

    @staticmethod
    def is_null(value):
        return value is None or (isinstance(value, QVariant) and value.isNull())

    def create(self, layer, featuresSelect, routeFieldName, startFieldName, endFieldName, errorFieldName, outputName, startOffsetFieldName=None, endOffsetFieldName=None, defaultDirection=None):
        # create new layer
        geometryType = "MultiLineString" if endFieldName else "Point"
        uri = geometryType
        uri += "?crs=%s" % crsString(self.lrs.crs)
        provider = QgsProviderRegistry.instance().createProvider('memory', uri)
        # Because memory provider (QGIS 2.4) fails to parse PostGIS type names (like int8, float, float8 ...)
        # and negative length and precision we overwrite type names according to types and reset length and precision
        fieldsList = layer.fields().toList()
        fixFields(fieldsList)
        provider.addAttributes(fieldsList)
        if errorFieldName:
            provider.addAttributes([makeField(errorFieldName, QVariant.String), ])
        uri = provider.dataSourceUri()
        # debug('uri: %s' % uri)

        outputLayer = QgsVectorLayer(uri, outputName, 'memory')
        if not outputLayer.isValid():
            QMessageBox.information(self, 'Information', 'Cannot create memory layer with uri %s' % uri)

        checkFields(layer, outputLayer)

        # Not sure why attributes were set again here, the attributes are already in uri
        # outputLayer.startEditing()  # to add fields
        # for field in layer.fields():
        #    if not outputLayer.addAttribute(field):
        #        QMessageBox.information(self, 'Information', 'Cannot add attribute %s' % field.name())

        # if errorFieldName:
        #    outputLayer.addAttribute(QgsField(errorFieldName, QVariant.String, "string"))

        # outputLayer.commitChanges()

        # Real-world official networks often have a small offset between the
        # first/last calibrated measure and the operational PK used by events.
        # Use a practical tolerance rather than only decimal-noise tolerance.
        eventTolerance = self.lrs.defaultMeasureTolerance(150.0)

        outputFeatures = []
        batchSize = 500
        fields = outputLayer.fields()
        #debug("create featuresSelect = %s" % featuresSelect)
        if featuresSelect == SELECTED_FEATURES:
            featuresIterator = layer.getSelectedFeatures()
            total = layer.selectedFeatureCount()
        else:
            featuresIterator = layer.getFeatures()
            total = layer.featureCount()
        if total <= 0:
            total = 1
        count = 0
        self._routeIds = self.lrs.getRouteIds() if hasattr(self.lrs, 'getRouteIds') else []
        for feature in featuresIterator:
            # debug("create feature.id = %s" % feature.id())
            routeId = feature[routeFieldName]
            routeIds = self.resolveRouteIds(routeId, defaultDirection)
            start = feature[startFieldName]
            end = feature[endFieldName] if endFieldName else None
            # Some special (HTML?) characters like "<" were breaking output in console -> escape()
            # debug ( "event routeId = %s start = %s end = %s" % ( escape(routeId), start, end ) )
            # Offset
            startOffset = feature[startOffsetFieldName] if startOffsetFieldName else 0.0
            endOffset = feature[endOffsetFieldName] if endOffsetFieldName else 0.0

            outputFeature = QgsFeature(fields)  # fields must exist during feature life!
            for field in layer.fields():
                if outputFeature.fields().indexFromName(field.name()) >= 0:
                    outputFeature[field.name()] = feature[field.name()]

            geo = None
            error = None
            if endFieldName:
                if self.is_null(start) or self.is_null(end):
                    error = 'measure is null'
                else:
                    for candidateRouteId in routeIds:
                        line, error = self.lrs.eventMultiPolyLine(candidateRouteId, start, end, eventTolerance,
                                                                  startOffset, endOffset)
                        if line:
                            geo = QgsGeometry.fromMultiPolylineXY(line)
                            break
            else:
                if self.is_null(start):
                    error = 'measure is null'
                else:
                    for candidateRouteId in routeIds:
                        point, error = self.lrs.eventPointXY(candidateRouteId, start, eventTolerance, startOffset)
                        if point:
                            geo = QgsGeometry(QgsPoint(point))
                            break

            if geo:
                outputFeature.setGeometry(geo)

            if errorFieldName and error:
                outputFeature[errorFieldName] = error

            outputFeatures.append(outputFeature)
            if len(outputFeatures) >= batchSize:
                outputLayer.dataProvider().addFeatures(outputFeatures)
                outputFeatures = []
                QgsApplication.processEvents()

            count += 1
            percent = 100 * count / total
            if self.progressBar:
                self.progressBar.setValue(int(percent))
            if count % 100 == 0:
                QgsApplication.processEvents()

        if outputFeatures:
            outputLayer.dataProvider().addFeatures(outputFeatures)

        QgsProject.instance().addMapLayers([outputLayer, ])

        if self.progressBar:
            self.progressBar.hide()

    def resolveRouteIds(self, routeId, defaultDirection=None):
        if self.lrs.getRouteIfExists(routeId):
            return [routeId]
        if routeId is None:
            return [routeId]

        routeText = str(routeId).strip()
        if not routeText:
            return [routeId]

        normalizedText = normalizeRouteId(routeText)
        routeIds = self._routeIds
        if routeIds is None and hasattr(self.lrs, 'getRouteIds'):
            routeIds = self.lrs.getRouteIds()

        matches = []
        if routeIds:
            base = normalizeRouteId(cleanRouteIdPart(routeText) or routeText)
            for candidate in routeIds:
                normalized = normalizeRouteId(candidate)
                if normalized == normalizedText or normalized == base:
                    matches.append(candidate)
                    continue

                parts = [part for part in normalized.split('_') if part]
                if parts and parts[-1] in ('creixent', 'decreixent'):
                    road = '_'.join(parts[:-1])
                else:
                    road = normalized
                if road == base:
                    matches.append(candidate)

        if defaultDirection and routeIds:
            preferred = []
            direction = normalizeRouteId(defaultDirection)
            for candidate in matches:
                normalized = normalizeRouteId(candidate)
                if normalized.endswith('_%s' % direction) or ('_%s_' % direction) in normalized:
                    preferred.append(candidate)
            if preferred:
                matches = preferred + [candidate for candidate in matches if candidate not in preferred]

        if matches:
            deduped = []
            for candidate in matches:
                if candidate not in deduped:
                    deduped.append(candidate)
            return deduped

        return [routeId]
