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
from PyQt5.QtCore import QVariant
from qgis.core import QgsFields

from ..utils import makeField


class LrsErrorFields(QgsFields):
    def __init__(self):
        super(LrsErrorFields, self).__init__()

        fields = [
            makeField('error', QVariant.String),  # error type, avoid 'type' which could be keyword
            makeField('severity', QVariant.String),
            makeField('element', QVariant.String),
            makeField('route', QVariant.String),
            makeField('measure', QVariant.String),
            makeField('codivia', QVariant.String),
            makeField('direccio', QVariant.String),
            makeField('idlrs', QVariant.String),
            makeField('idpk', QVariant.String),
            makeField('message', QVariant.String),
        ]

        for field in fields:
            self.append(field)


LRS_ERROR_FIELDS = LrsErrorFields()
