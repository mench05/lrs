# -*- coding: utf-8 -*-
"""
/***************************************************************************
 LrsDockWidget
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
import os

from qgis.PyQt import uic
from qgis.PyQt.QtCore import QVariant, QCoreApplication
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialogButtonBox, QDockWidget, QGridLayout,
                                 QLabel, QLineEdit, QPushButton, QTableView, QWidget)
from qgis.core import QgsApplication
from qgis.gui import QgsHighlight, QgsMapToolEmitPoint

from ..lrs.error.lrserrorlayermanager import LrsErrorLayerManager
from ..lrs.error.lrserrorlinelayer import LrsErrorLineLayer
from ..lrs.error.lrserrormodel import LrsErrorModel
from ..lrs.error.lrserrorpointlayer import LrsErrorPointLayer
from ..lrs.error.lrserrorvisualizer import LrsErrorVisualizer
from ..lrs.error.lrsqualitylayer import LrsQualityLayer
from ..lrs.error.lrsqualitylayermanager import LrsQualityLayerManager
from ..lrs.lrsevents import LrsEvents
from ..lrs.lrscalib import LrsCalib
from ..lrs.lrslayer import LrsLayer
from ..lrs.lrsoutput import LrsOutput
from ..lrs.lrsmeasures import LrsMeasures
from .lrscombomanager import LrsComboManager
from .lrscombomanagerbase import *
from .lrsfieldcombomanager import LrsFieldComboManager
from .lrslayercombomanager import LrsLayerComboManager
from .lrsselectiondialog import *
from .lrsunitcombomanager import LrsUnitComboManager
from .lrswidgetmanager import *

Ui_LrsDockWidget, _ = uic.loadUiType(
    os.path.join(os.path.dirname(__file__), 'ui_lrsdockwidget.ui')
)

try:
    import psycopg2
    import psycopg2.extensions

    # use unicode!
    psycopg2.extensions.register_type(psycopg2.extensions.UNICODE)
    psycopg2.extensions.register_type(psycopg2.extensions.UNICODEARRAY)
    havePostgis = True
except:
    havePostgis = False


class LrsDockWidget(QDockWidget, Ui_LrsDockWidget):
    def __init__(self, parent, iface):
        # #debug( "LrsDockWidget.__init__")
        self.iface = iface
        self.lrs = None  # Lrs object
        self.lrsLayer = None  # Common input LrsLayer for locate/events/measure
        self.genSelectionDialog = None
        self.locatePoint = None  # QgsPointXY
        self.locateHighlight = None  # QgsHighlight
        self.locatePickMapTool = None
        self.previousMapTool = None
        self.errorPointLayer = None
        self.errorPointLayerManager = None
        self.errorLineLayer = None
        self.errorLineLayerManager = None
        self.qualityLayer = None
        self.qualityLayerManager = None
        self.locateRouteGroups = {}

        self.pluginDir = os.path.dirname(os.path.dirname(__file__))

        super(LrsDockWidget, self).__init__(parent)

        # Set up the user interface from Designer.
        self.setupUi(self)
        self.translateUiToCatalan()

        # keep progress frame height
        self.genProgressFrame.setMinimumHeight(self.genProgressFrame.height())
        self.hideGenProgress()

        self.tabWidget.currentChanged.connect(self.tabChanged)

        # ------------- locate, events, measure have synchronized lrs layer and route field --------
        lrsLayerComboList = [self.locateLrsLayerCombo, self.eventsLrsLayerCombo, self.measureLrsLayerCombo]

        self.lrsLayerCM = LrsLayerComboManager(lrsLayerComboList,
                                               geometryType=QgsWkbTypes.LineGeometry,
                                               geometryHasM=True, settingsName='lrsLayerId')

        self.lrsLayerCM.layerChanged.connect(self.lrsLayerChanged)

        lrsRouteFieldComboList = [self.locateLrsRouteFieldCombo, self.eventsLrsRouteFieldCombo,
                                  self.measureLrsRouteFieldCombo]
        self.lrsRouteFieldCM = LrsFieldComboManager(lrsRouteFieldComboList, self.lrsLayerCM,
                                                    settingsName='lrsRouteField', allowNone=True)

        # using activated() which is called on user interaction to avoid be called 3 times from each combo
        # self.lrsRouteFieldCM.fieldNameChanged.connect(self.lrsRouteFieldNameChanged)
        self.lrsRouteFieldCM.fieldNameActivated.connect(self.lrsRouteFieldNameActivated)

        # ----------------------- locateTab ---------------------------
        self.locateRouteCM = LrsComboManager(self.locateRouteCombo)
        self.addLocateRouteFilter()
        self.addLocatePickButton()
        self.locateHighlightWM = LrsWidgetManager(self.locateHighlightCheckBox, settingsName='locateHighlight',
                                                  defaultValue=True)
        self.locateBufferWM = LrsWidgetManager(self.locateBufferSpin, settingsName='locateBuffer', defaultValue=200.0)

        self.locateRouteCombo.currentIndexChanged.connect(self.locateRouteChanged)
        self.locateRouteFilterLineEdit.textChanged.connect(self.resetLocateRoutes)
        self.locateMeasureSpin.valueChanged.connect(self.resetLocateEvent)
        self.locateBufferSpin.valueChanged.connect(self.locateBufferChanged)
        self.locateCenterButton.clicked.connect(self.locateCenter)
        self.locateHighlightCheckBox.stateChanged.connect(self.locateHighlightChanged)
        self.locateZoomButton.clicked.connect(self.locateZoom)
        self.locatePickButton.clicked.connect(self.activateLocatePickTool)
        self.locateHelpButton.clicked.connect(lambda: self.showHelp('locate'))
        self.resetLocateRoutes()
        self.locateProgressBar.hide()

        # ----------------------- eventsTab ---------------------------
        self.eventsLayerCM = LrsLayerComboManager(self.eventsLayerCombo, settingsName='eventsLayerId')
        self.eventsRouteFieldCM = LrsFieldComboManager(self.eventsRouteFieldCombo, self.eventsLayerCM,
                                                       settingsName='eventsRouteField')
        self.eventsMeasureStartFieldCM = LrsFieldComboManager(self.eventsMeasureStartFieldCombo, self.eventsLayerCM,
                                                              types=QVARIANT_NUMBER_TYPE_LIST,
                                                              settingsName='eventsMeasureStartField')
        self.eventsMeasureEndFieldCM = LrsFieldComboManager(self.eventsMeasureEndFieldCombo, self.eventsLayerCM,
                                                            types=QVARIANT_NUMBER_TYPE_LIST, allowNone=True,
                                                            settingsName='eventsMeasureEndField')
        # Offset values
        self.eventsOffsetStartFieldCM = LrsFieldComboManager(self.eventsOffsetStartFieldCombo, self.eventsLayerCM,
                                                              types=QVARIANT_NUMBER_TYPE_LIST, allowNone=True,
                                                              settingsName='eventsOffsetStartField')
        self.eventsOffsetEndFieldCM = LrsFieldComboManager(self.eventsOffsetEndFieldCombo, self.eventsLayerCM,
                                                            types=QVARIANT_NUMBER_TYPE_LIST, allowNone=True,
                                                            settingsName='eventsOffsetEndField')

        self.eventsFeaturesSelectCM = LrsComboManager(self.eventsFeaturesSelectCombo, options=(
            (ALL_FEATURES, self.tr('Tots els elements')), (SELECTED_FEATURES, self.tr('Elements seleccionats'))),
                                                      defaultValue=ALL_FEATURES, settingsName='eventsFeaturesSelect')
        self.addEventsDefaultDirectionOption()

        self.eventsOutputNameWM = LrsWidgetManager(self.eventsOutputNameLineEdit, settingsName='eventsOutputName',
                                                   defaultValue='Esdeveniments LRS')
        self.eventsErrorFieldWM = LrsWidgetManager(self.eventsErrorFieldLineEdit, settingsName='eventsErrorField',
                                                   defaultValue='lrs_err')
        validator = QRegExpValidator(QRegExp('[A-Za-z_][A-Za-z0-9_]+'), None)
        self.eventsErrorFieldLineEdit.setValidator(validator)

        self.eventsButtonBox.button(QDialogButtonBox.Ok).clicked.connect(self.createEvents)
        self.eventsButtonBox.button(QDialogButtonBox.Reset).clicked.connect(self.resetEventsOptionsAndWrite)
        self.eventsButtonBox.button(QDialogButtonBox.Help).clicked.connect(lambda: self.showHelp('events'))
        self.eventsLayerCombo.currentIndexChanged.connect(self.resetEventsButtons)
        self.eventsRouteFieldCombo.currentIndexChanged.connect(self.resetEventsButtons)
        self.eventsMeasureStartFieldCombo.currentIndexChanged.connect(self.resetEventsButtons)
        self.eventsMeasureEndFieldCombo.currentIndexChanged.connect(self.resetEventsButtons)
        self.eventsDefaultDirectionCombo.currentIndexChanged.connect(self.resetEventsButtons)
        # Offset
        self.eventsOffsetStartFieldCombo.currentIndexChanged.connect(self.resetEventsButtons)
        self.eventsOffsetEndFieldCombo.currentIndexChanged.connect(self.resetEventsButtons)
        self.eventsOutputNameLineEdit.textEdited.connect(self.resetEventsButtons)
        self.resetEventsOptions()
        self.resetEventsButtons()
        self.eventsProgressBar.hide()

        self.eventsLayerCM.reload()

        # ----------------------- measureTab ---------------------------
        self.measureLayerCM = LrsLayerComboManager(self.measureLayerCombo, geometryType=QgsWkbTypes.PointGeometry,
                                                   settingsName='measureLayerId')
        self.measureRouteFieldCM = LrsFieldComboManager(self.measureRouteFieldCombo, self.measureLayerCM,
                                                        allowNone=True, settingsName='measureRouteField')
        self.measureThresholdWM = LrsWidgetManager(self.measureThresholdSpin, settingsName='measureThreshold',
                                                   defaultValue=100.0)
        self.measureOutputNameWM = LrsWidgetManager(self.measureOutputNameLineEdit, settingsName='measureOutputName',
                                                    defaultValue='Mesures LRS')

        self.measureOutputRouteFieldWM = LrsWidgetManager(self.measureOutputRouteFieldLineEdit,
                                                          settingsName='measureOutputRouteField',
                                                          defaultValue='route')
        validator = QRegExpValidator(QRegExp('[A-Za-z_][A-Za-z0-9_]+'), None)
        self.measureOutputRouteFieldLineEdit.setValidator(validator)

        self.measureMeasureFieldWM = LrsWidgetManager(self.measureMeasureFieldLineEdit,
                                                      settingsName='measureMeasureField', defaultValue='measure')
        self.measureMeasureFieldLineEdit.setValidator(validator)

        self.measureButtonBox.button(QDialogButtonBox.Ok).clicked.connect(self.calculateMeasures)
        self.measureButtonBox.button(QDialogButtonBox.Reset).clicked.connect(self.resetMeasureOptionsAndWrite)
        self.measureButtonBox.button(QDialogButtonBox.Help).clicked.connect(lambda: self.showHelp('measures'))
        self.measureLayerCombo.currentIndexChanged.connect(self.resetMeasureButtons)
        self.measureOutputNameLineEdit.textEdited.connect(self.resetMeasureButtons)
        self.measureOutputRouteFieldLineEdit.textEdited.connect(self.resetMeasureButtons)
        self.measureMeasureFieldLineEdit.textEdited.connect(self.resetMeasureButtons)
        self.resetMeasureOptions()
        self.resetMeasureButtons()
        self.measureProgressBar.hide()

        self.measureLayerCM.reload()

        # ------------- genTab -----------------------
        self.genLineLayerCM = LrsLayerComboManager(self.genLineLayerCombo, geometryType=QgsWkbTypes.LineGeometry,
                                                   settingsName='lineLayerId')
        self.genLineRouteFieldCM = LrsFieldComboManager(self.genLineRouteFieldCombo, self.genLineLayerCM,
                                                        settingsName='lineRouteField')
        self.genPointLayerCM = LrsLayerComboManager(self.genPointLayerCombo, geometryType=QgsWkbTypes.PointGeometry,
                                                    settingsName='pointLayerId')
        self.genPointRouteFieldCM = LrsFieldComboManager(self.genPointRouteFieldCombo, self.genPointLayerCM,
                                                         settingsName='pointRouteField')
        self.genPointMeasureFieldCM = LrsFieldComboManager(self.genPointMeasureFieldCombo, self.genPointLayerCM,
                                                           types=QVARIANT_NUMBER_TYPE_LIST,
                                                           settingsName='pointMeasureField')

        self.genMeasureUnitCM = LrsUnitComboManager(self.genMeasureUnitCombo, settingsName='measureUnit',
                                                    defaultValue=LrsUnits.KILOMETER)

        self.genSelectionModeCM = LrsComboManager(self.genSelectionModeCombo, options=(
            ('all', self.tr('Totes les rutes')), ('include', self.tr('Incloure rutes')),
            ('exclude', self.tr('Excluir rutes'))),
                                                  defaultValue='all', settingsName='selectionMode')
        self.genSelectionWM = LrsWidgetManager(self.genSelectionLineEdit, settingsName='selection')

        self.genThresholdWM = LrsWidgetManager(self.genThresholdSpin, settingsName='threshold', defaultValue=100.0)
        self.genSnapWM = LrsWidgetManager(self.genSnapSpin, settingsName='snap', defaultValue=0.0)
        self.genParallelModeCM = LrsComboManager(self.genParallelModeCombo, options=(
            ('error', self.tr('Marcar com a errors')), ('span', self.tr('Unir amb línia recta')),
            ('exclude', self.tr('Excluir'))), defaultValue='error', settingsName='parallelMode')
        self.genExtrapolateWM = LrsWidgetManager(self.genExtrapolateCheckBox, settingsName='extrapolate',
                                                 defaultValue=False)

        self.addGenerateRobustOptions()

        self.genOutputNameWM = LrsWidgetManager(self.genOutputNameLineEdit, settingsName='lrsOutputName',
                                                defaultValue='LRS')

        self.genLineLayerCombo.currentIndexChanged.connect(self.resetGenerateButtons)
        self.genLineLayerCombo.currentIndexChanged.connect(self.updateLabelsUnits)
        self.genLineRouteFieldCombo.currentIndexChanged.connect(self.resetGenerateButtons)
        self.genPointLayerCombo.currentIndexChanged.connect(self.resetGenerateButtons)
        self.genPointRouteFieldCombo.currentIndexChanged.connect(self.resetGenerateButtons)
        self.genPointMeasureFieldCombo.currentIndexChanged.connect(self.resetGenerateButtons)
        self.genCompositeRouteIdCheckBox.stateChanged.connect(self.resetGenerateButtons)

        self.genSelectionModeCombo.currentIndexChanged.connect(self.enableGenerateSelection)
        self.genSelectionButton.clicked.connect(self.openGenerateSelectionDialog)

        self.genButtonBox.button(QDialogButtonBox.Ok).clicked.connect(self.generateLrs)
        self.genButtonBox.button(QDialogButtonBox.Reset).clicked.connect(self.resetGenerateOptionsAndWrite)
        self.genButtonBox.button(QDialogButtonBox.Help).clicked.connect(lambda: self.showHelp('calibration'))

        self.genOutputNameLineEdit.textChanged.connect(self.resetGenButtons)
        self.genCreateOutputButton.setEnabled(False)
        self.genCreateOutputButton.clicked.connect(self.createLrsOutput)

        # load layers after other combos were connected
        self.genLineLayerCM.reload()
        self.genPointLayerCM.reload()

        self.enableGenerateSelection()

        # ------------- errorTab -----------------------
        self.errorVisualizer = LrsErrorVisualizer(self.iface.mapCanvas())
        self.errorModel = None
        self.errorView.horizontalHeader().setStretchLastSection(True)
        self.errorZoomButton.setEnabled(False)
        self.errorZoomButton.setIcon(QgsApplication.getThemeIcon('/mActionZoomIn.svg'))
        self.errorZoomButton.setText('Zoom')
        self.errorZoomButton.clicked.connect(self.errorZoom)
        self.errorFilterLineEdit.textChanged.connect(self.errorFilterChanged)

        self.addErrorLayersButton.clicked.connect(self.addErrorLayers)
        self.addQualityLayerButton.clicked.connect(self.addQualityLayer)
        self.errorButtonBox.button(QDialogButtonBox.Help).clicked.connect(lambda: self.showHelp('errors'))

        # ---------------------------- statistics tab ----------------------------
        # currently not used (did not correspond well to errors)
        # self.tabWidget.removeTab( self.tabWidget.indexOf(self.statsTab) )

        # ------------------ help tab -------------------------
        # Importing QWebEngineView gives (Qt 5.8.0, PyQt5 5.8.2):
        # ImportError: QtWebEngineWidgets must be imported before a QCoreApplication instance is created
        # from PyQt5.QtWebEngineWidgets import QWebEngineView
        # self.helpWebEngineView = QWebEngineView(self.helpTab)

        # QTextBrowser does not render perfectly all html created by Sphinx -> see help/source/conf.py
        # http://doc.qt.io/qt-5/richtext-html-subset.html
        # QTextBrowser would not render external web sites -> open in browser
        self.helpTextBrowser.setOpenExternalLinks(True)
        self.helpTextBrowser.setSource(QUrl(self.getHelpUrl()))
        # self.helpTextBrowser.setSource(QUrl("http://www.mpasolutions.it/"))
        # self.helpTextBrowser.setSource(QUrl("http://www.google.com/"))

        # -----------------------------------------------------
        # after all combos were created and connected
        self.lrsLayerCM.reload()  # -> lrsRouteFieldCM -> ....

        # -----------------------------------------------------
        self.enableTabs()

        QgsProject.instance().layersWillBeRemoved.connect(self.layersWillBeRemoved)

        QgsProject.instance().readProject.connect(self.projectRead)

        QgsProject().instance().crsChanged.connect(self.mapSettingsCrsChanged)

        self.updateLabelsUnits()

        # newProject is currently missing in sip
        # QgsProject.instance().newProject.connect( self.projectNew )

        # read project if plugin was reloaded
        self.projectRead()

    def translateUiToCatalan(self):
        self.tabWidget.setTabText(self.tabWidget.indexOf(self.locateTab), 'Localitzar')
        self.tabWidget.setTabText(self.tabWidget.indexOf(self.eventsTab), 'Esdeveniments')
        self.tabWidget.setTabText(self.tabWidget.indexOf(self.measureTab), 'Mesures')
        self.tabWidget.setTabText(self.tabWidget.indexOf(self.calibTab), 'Calibratge')
        self.tabWidget.setTabText(self.tabWidget.indexOf(self.errorTab), 'Errors')

        self.locateLrsLayerLabel.setText('Capa amb mesures')
        self.locateLrsRouteFieldLabel.setText('Camp de ruta')
        self.locateRouteLabel.setText('Ruta')
        self.locateRangesLabel.setText('Mesures disponibles')
        self.locateMeasureLabel.setText('Mesura')
        self.locateCoordinatesLabel.setText('Coordenades')
        self.locateHighlightLabel.setText('Ressaltar')
        self.locateBufferLabel.setText('Buffer de zoom')
        self.locateHelpButton.setText('Ajuda')
        self.locateCenterButton.setText('Centrar')
        self.locateZoomButton.setText('Zoom')

        self.eventsLrsLayerLabel.setText('Capa amb mesures')
        self.eventsLrsRouteFieldLabel.setText('Camp de ruta')
        self.eventsLayerLabel.setText('Capa d\'esdeveniments')
        self.eventsRouteFieldLabel.setText('Camp de ruta dels esdeveniments')
        self.eventsMeasureStartFieldLabel.setText('Camp de mesura inicial')
        self.eventsMeasureEndFieldLabel.setText('Camp de mesura final')
        self.eventsOffsetStartFieldLabel.setText('Camp de desplaçament inicial')
        self.eventsOffsetEndFieldLabel.setText('Camp de desplaçament final')
        self.eventsFeaturesSelectLabel.setText('Elements')
        self.eventsOutputNameLabel.setText('Nom de la capa de sortida')
        self.eventsErrorFieldLabel.setText('Camp d\'error de sortida')

        self.measureLrsLayerLabel.setText('Capa amb mesures')
        self.measureLrsRouteFieldLabel.setText('Camp de ruta')
        self.measureLayerLabel.setText('Capa')
        self.measureRouteFieldLabel.setText('Camp de ruta (opcional)')
        self.measureThresholdLabel.setText('Distancia max. al punt')
        self.measureOutputNameLabel.setText('Nom de la capa de sortida')
        self.measureOutputRouteFieldLabel.setText('Camp de ruta de sortida')
        self.measureMeasureFieldLabel.setText('Camp de mesura de sortida')

        self.genLineLayerLabel.setText('Capa de línies')
        self.genLineRouteFieldLabel.setText('Camp de ruta de línies')
        self.genPointLayerLabel.setText('Capa de punts')
        self.genPointRouteFieldLabel.setText('Camp de ruta de punts')
        self.genPointMeasureFieldLabel.setText('Camp de mesura')
        self.genMeasureUnitLabel.setText('Unitat de mesura')
        self.genSelectionModeLabel.setText('Selecció')
        self.genSnapLabel.setText('Snap')
        self.genThresholdLabel.setText('Llindar')
        self.genParallelModeLabel.setText('Paral·lels')
        self.genExtrapolateLabel.setText('Extrapolar')
        self.genOutputNameLabel.setText('Nom de la capa de sortida')
        self.genSelectionButton.setText('Seleccionar')
        self.genCreateOutputButton.setText('Crear')

        self.errorFilterLabel.setText('Filtre')
        self.errorTotalLengthLabel.setText('Longitud total de totes les línies')
        self.errorIncludedLengthLabel.setText('Longitud de les línies incloses')
        self.errorSuccessLengthLabel.setText('Longitud del LRS creat correctament')
        self.addErrorLayersButton.setText('Crear capes d\'error')
        self.addQualityLayerButton.setText('Crear capa de qualitat')
        self.addErrorLayersButton.setToolTip('Afegeix capes d\'error a la vista del mapa')
        self.addQualityLayerButton.setToolTip('Afegeix la capa de qualitat a la vista del mapa')

        for buttonBox in (self.eventsButtonBox, self.measureButtonBox, self.genButtonBox):
            buttonBox.button(QDialogButtonBox.Ok).setText('Acceptar')
            buttonBox.button(QDialogButtonBox.Reset).setText('Restablir')
            buttonBox.button(QDialogButtonBox.Help).setText('Ajuda')
        self.errorButtonBox.button(QDialogButtonBox.Help).setText('Ajuda')

    def addLocateRouteFilter(self):
        parent = self.locateRouteCombo.parentWidget()
        layout = parent.layout() if parent else None
        self.locateRouteFilterLabel = QLabel(self.tr('Filtre de ruta'), parent)
        self.locateRouteFilterLineEdit = QLineEdit(parent)
        self.locateRouteFilterLineEdit.setToolTip(self.tr('Filtra les rutes disponibles de la llista de localització.'))
        self.locateRouteFilterLineEdit.setPlaceholderText(self.tr('Escriu CODIVIA, sentit o ramal'))
        if layout and isinstance(layout, QGridLayout):
            layout.addWidget(self.locateRouteFilterLabel, 6, 0)
            layout.addWidget(self.locateRouteFilterLineEdit, 6, 1)
        elif layout:
            panel = QWidget(parent)
            panelLayout = QGridLayout(panel)
            panelLayout.setContentsMargins(0, 0, 0, 0)
            panelLayout.addWidget(self.locateRouteFilterLabel, 0, 0)
            panelLayout.addWidget(self.locateRouteFilterLineEdit, 0, 1)
            layout.addWidget(panel)

    def addLocatePickButton(self):
        parent = self.locateRouteCombo.parentWidget()
        layout = parent.layout() if parent else None
        self.locatePickButton = QPushButton(self.tr('Capturar PK al mapa'), parent)
        self.locatePickButton.setToolTip(
            self.tr('Fes clic al mapa per trobar la ruta LRS i la mesura més properes.'))
        if layout and isinstance(layout, QGridLayout):
            layout.addWidget(self.locatePickButton, 6, 2)
        elif layout:
            layout.addWidget(self.locatePickButton)

    def addGenerateRobustOptions(self):
        parent = self.genExtrapolateCheckBox.parentWidget()
        layout = parent.layout() if parent else None

        self.genCompositeRouteIdLabel = QLabel(self.tr('Camps del ROUTE_ID compost'), parent)
        self.genCompositeRouteIdLineEdit = QLineEdit(parent)
        self.genCompositeRouteIdLineEdit.setToolTip(self.tr('Camps separats per comes per formar la ruta lògica.'))
        self.genCompositeRouteIdLineEdit.setText('CODIVIA,DIRECCIO')

        self.genCompositeRouteIdCheckBox = QCheckBox(parent)
        self.genOfficialMeasuresCheckBox = QCheckBox(parent)
        self.genStrictDirectionCheckBox = QCheckBox(parent)
        self.genSharedGeometryCheckBox = QCheckBox(parent)
        self.genTolerantModeCheckBox = QCheckBox(parent)
        self.genRamalHandlingCheckBox = QCheckBox(parent)
        self.genRoundaboutHandlingCheckBox = QCheckBox(parent)
        self.genDiagnosticsCheckBox = QCheckBox(parent)

        robustWidgets = (
            (self.tr('Usar ROUTE_ID compost'), self.genCompositeRouteIdCheckBox),
            (self.tr('Usar mesures oficials dels arcs'), self.genOfficialMeasuresCheckBox),
            (self.tr('Separar per DIRECCIO'), self.genStrictDirectionCheckBox),
            (self.tr('Permetre geometria compartida per sentit'), self.genSharedGeometryCheckBox),
            (self.tr('Continuar amb advertències'), self.genTolerantModeCheckBox),
            (self.tr('Tractar els ramals com a independents'), self.genRamalHandlingCheckBox),
            (self.tr('Tractar els anells tancats com a independents'), self.genRoundaboutHandlingCheckBox),
            (self.tr('Generar diagnòstics'), self.genDiagnosticsCheckBox),
        )

        if layout and isinstance(layout, QGridLayout):
            row = 18
            layout.addWidget(self.genCompositeRouteIdLabel, row, 0)
            layout.addWidget(self.genCompositeRouteIdLineEdit, row, 1)
            row += 1
            for labelText, widget in robustWidgets:
                label = QLabel(labelText, parent)
                layout.addWidget(label, row, 0)
                layout.addWidget(widget, row, 1)
                row += 1
        elif layout:
            robustPanel = QWidget(parent)
            robustLayout = QGridLayout(robustPanel)
            robustLayout.setContentsMargins(0, 0, 0, 0)
            robustLayout.addWidget(self.genCompositeRouteIdLabel, 0, 0)
            robustLayout.addWidget(self.genCompositeRouteIdLineEdit, 0, 1)
            row = 1
            for labelText, widget in robustWidgets:
                label = QLabel(labelText, robustPanel)
                robustLayout.addWidget(label, row, 0)
                robustLayout.addWidget(widget, row, 1)
                row += 1
            layout.addWidget(robustPanel)

        self.genCompositeRouteIdWM = LrsWidgetManager(self.genCompositeRouteIdCheckBox,
                                                      settingsName='useCompositeRouteId', defaultValue=True)
        self.genCompositeRouteFieldsWM = LrsWidgetManager(self.genCompositeRouteIdLineEdit,
                                                          settingsName='compositeRouteFields',
                                                          defaultValue='CODIVIA,DIRECCIO')
        self.genOfficialMeasuresWM = LrsWidgetManager(self.genOfficialMeasuresCheckBox,
                                                      settingsName='useOfficialArcMeasures', defaultValue=True)
        self.genStrictDirectionWM = LrsWidgetManager(self.genStrictDirectionCheckBox,
                                                     settingsName='strictDirection', defaultValue=True)
        self.genSharedGeometryWM = LrsWidgetManager(self.genSharedGeometryCheckBox,
                                                    settingsName='allowSharedGeometryDirections', defaultValue=True)
        self.genTolerantModeWM = LrsWidgetManager(self.genTolerantModeCheckBox,
                                                  settingsName='tolerantMode', defaultValue=True)
        self.genRamalHandlingWM = LrsWidgetManager(self.genRamalHandlingCheckBox,
                                                   settingsName='specialRamalHandling', defaultValue=True)
        self.genRoundaboutHandlingWM = LrsWidgetManager(self.genRoundaboutHandlingCheckBox,
                                                        settingsName='specialRoundaboutHandling', defaultValue=True)
        self.genDiagnosticsWM = LrsWidgetManager(self.genDiagnosticsCheckBox,
                                                 settingsName='generateDiagnostics', defaultValue=True)

    def lrsLayerChanged(self, layer):
        # debug("lrsLayerChanged layer: %s" % (layer.name() if layer else None))
        self.lrsLayer = None
        if layer is not None:
            self.lrsLayer = LrsLayer(layer)
        self.lrsRouteFieldCM.reset()
        self.resetLocateRoutes()
        self.updateMeasureUnits()
        # don't write here, the layer is changing also when loading plugin ->
        # written when route is selected
        # self.lrsLayerCM.writeToProject()

    def lrsRouteFieldNameActivated(self, fieldName):
        # debug("lrsRouteFieldNameActivated fieldName = " + fieldName)
        self.loadLrsLayer()
        self.lrsLayerCM.writeToProject()
        self.lrsRouteFieldCM.writeToProject()

    def loadLrsLayer(self):
        fieldName = self.lrsRouteFieldCM.value()
        if self.lrsLayer:
            self.lrsLayer.setRouteFieldName(fieldName)
            if fieldName:
                self.showLrsLayerProgressBar()
                self.lrsLayer.load(self.loadLrsLayerProgress)
                self.hideLrsLayerProgressBar()
            else:
                self.lrsLayer.reset()
        self.resetLocateRoutes()

    def errorFilterChanged(self, text):
        if not self.sortErrorModel: return
        self.sortErrorModel.setFilterWildcard(text)

    def projectRead(self):
        # debug("projectRead")
        if not QgsProject:
            return

        project = QgsProject.instance()
        if not project:
            return

        self.lrsLayerCM.readFromProject()
        self.lrsRouteFieldCM.readFromProject()
        self.loadLrsLayer()  # load if layer + route field were selected
        self.readGenerateOptions()
        self.readLocateOptions()
        self.readEventsOptions()
        self.readMeasureOptions()

        # --------------------- set error layers if stored in project -------------------
        errorLineLayerId = project.readEntry(PROJECT_PLUGIN_NAME, "errorLineLayerId")[0]
        # debug("projectRead errorLineLayerId = %s" % errorLineLayerId)
        self.errorLineLayer = project.mapLayer(errorLineLayerId)
        # debug("projectRead errorLineLayer = %s" % self.errorLineLayer)
        # layers must be tested 'is not None' (because layers have __len__(), some strange len)
        if self.errorLineLayer is not None:
            self.errorLineLayerManager = LrsErrorLayerManager(self.errorLineLayer)

        errorPointLayerId = project.readEntry(PROJECT_PLUGIN_NAME, "errorPointLayerId")[0]
        self.errorPointLayer = project.mapLayer(errorPointLayerId)
        if self.errorPointLayer is not None:
            self.errorPointLayerManager = LrsErrorLayerManager(self.errorPointLayer)

        qualityLayerId = project.readEntry(PROJECT_PLUGIN_NAME, "qualityLayerId")[0]
        self.qualityLayer = project.mapLayer(qualityLayerId)
        if self.qualityLayer is not None:
            self.qualityLayerManager = LrsQualityLayerManager(self.qualityLayer)

        self.resetGenerateButtons()

        # #debug
        # if self.genLineLayerCM.getLayer():
        #    self.generateLrs() # only when reloading!

    def projectNew(self):
        self.deleteLrs()
        self.resetGenerateOptions()
        self.resetEventsOptions()
        self.resetMeasureOptions()
        self.enableTabs()

    def deleteLrs(self):
        if self.lrs is not None:
            self.lrs.disconnect()
            del self.lrs
        self.lrs = None

    def close(self):
        # #debug( "LrsDockWidget.close")
        self.deleteLrs()
        QgsProject.instance().layersWillBeRemoved.disconnect(self.layersWillBeRemoved)
        QgsProject.instance().readProject.disconnect(self.projectRead)
        QgsProject().instance().crsChanged.disconnect(self.mapSettingsCrsChanged)

        # Must delete combo managers to disconnect!
        del self.genLineLayerCM
        del self.genLineRouteFieldCM
        del self.genPointLayerCM
        del self.genPointRouteFieldCM
        del self.genPointMeasureFieldCM
        del self.genCompositeRouteIdWM
        del self.genCompositeRouteFieldsWM
        del self.genOfficialMeasuresWM
        del self.genStrictDirectionWM
        del self.genSharedGeometryWM
        del self.genTolerantModeWM
        del self.genRamalHandlingWM
        del self.genRoundaboutHandlingWM
        del self.genDiagnosticsWM
        del self.errorVisualizer

        del self.eventsLayerCM
        del self.eventsRouteFieldCM
        del self.eventsMeasureStartFieldCM
        del self.eventsMeasureEndFieldCM
        del self.eventsDefaultDirectionCM
        # Offset
        del self.eventsOffsetStartFieldCM
        del self.eventsOffsetEndFieldCM
        del self.eventsFeaturesSelectCM

        self.restorePreviousMapTool()
        self.clearLocateHighlight()

        super(LrsDockWidget, self).close()

    def layersWillBeRemoved(self, layerIdList):
        # debug("layersWillBeRemoved layerIdList = %s" % layerIdList)
        project = QgsProject.instance()
        # layers must be tested 'is not None' (because layers have __len__(), some strange len)
        for id in layerIdList:
            if self.errorPointLayer is not None and self.errorPointLayer.id() == id:
                # debug("layersWillBeRemoved errorPointLayer.id = %s -> unset" % self.errorPointLayer.id())
                self.errorPointLayerManager = None
                self.errorPointLayer = None
                project.removeEntry(PROJECT_PLUGIN_NAME, "errorPointLayerId")
            if self.errorLineLayer is not None and self.errorLineLayer.id() == id:
                self.errorLineLayerManager = None
                self.errorLineLayer = None
                project.removeEntry(PROJECT_PLUGIN_NAME, "errorLineLayerId")
            if self.qualityLayer is not None and self.qualityLayer.id() == id:
                self.qualityLayerManager = None
                self.qualityLayer = None
                project.removeEntry(PROJECT_PLUGIN_NAME, "qualityLayerId")

    def enableTabs(self):
        enable = bool(self.lrs)
        # self.errorTab.setEnabled(enable)
        # self.locateTab.setEnabled(enable)
        # self.eventsTab.setEnabled(enable)
        # self.measureTab.setEnabled(enable)
        # self.statsTab.setEnabled(enable)

    def tabChanged(self, index):
        # #debug("tabChanged index = %s" % index )
        pass

    def mapSettingsCrsChanged(self):
        # #debug("mapSettingsCrsChanged")
        self.updateLabelsUnits()

    @staticmethod
    def getUnitsLabel(crs):
        if crs:
            return " (%s)" % QgsUnitTypes.encodeUnit(crs.mapUnits())
        else:
            return ""

    def getThresholdLabel(self, crs):
        #label = "Max point distance"
        label = QCoreApplication.translate("LrsDockWidget", "Max point distance")
        if crs is not None:
            label += self.getUnitsLabel(crs)
        return label

    def getHelpUrl(self):
        #return "file:///" + self.pluginDir + "/help/index.html"
        helpFile = u'file:///{}/help/{}'.format(
            self.pluginDir,
            QCoreApplication.translate("LrsDockWidget", "index.html"))
        # debug('help %s' % helpFile)
        return helpFile

    def showHelp(self, anchor=None):
        # debug("showHelp anchor = %s" % anchor)
        url = self.getHelpUrl()
        if anchor:
            url += "#" + anchor
            # debug("showHelp url = %s" % url)
            self.helpTextBrowser.setSource(QUrl(url))
        # QDesktopServices.openUrl(QUrl(url)) # open in default browser
        self.tabWidget.setCurrentIndex(self.tabWidget.indexOf(self.helpTab))

    def lrsEdited(self):
        self.resetStats()

    # ------------------- GENERATE (CALIBRATE) -------------------

    def resetGenerateButtons(self):
        enabled = self.genLineLayerCombo.currentIndex() != -1 and self.genLineRouteFieldCombo.currentIndex() != -1 and self.genPointLayerCombo.currentIndex() != -1 and self.genPointRouteFieldCombo.currentIndex() != -1 and self.genPointMeasureFieldCombo.currentIndex() != -1

        self.genButtonBox.button(QDialogButtonBox.Ok).setEnabled(enabled)

    def resetGenerateOptions(self):
        self.genLineLayerCM.reset()
        self.genLineRouteFieldCM.reset()
        self.genPointLayerCM.reset()
        self.genPointRouteFieldCM.reset()
        self.genPointMeasureFieldCM.reset()
        self.genMeasureUnitCM.reset()
        self.genSelectionModeCM.reset()
        self.genSelectionWM.reset()
        self.genThresholdWM.reset()
        self.genSnapWM.reset()
        self.genParallelModeCM.reset()
        self.genExtrapolateWM.reset()
        self.genCompositeRouteIdWM.reset()
        self.genCompositeRouteFieldsWM.reset()
        self.genOfficialMeasuresWM.reset()
        self.genStrictDirectionWM.reset()
        self.genSharedGeometryWM.reset()
        self.genTolerantModeWM.reset()
        self.genRamalHandlingWM.reset()
        self.genRoundaboutHandlingWM.reset()
        self.genDiagnosticsWM.reset()
        self.genOutputNameWM.reset()

        self.resetGenerateButtons()

    def resetGenerateOptionsAndWrite(self):
        self.resetGenerateOptions()
        self.writeGenerateOptions()

    def enableGenerateSelection(self):
        enable = self.genSelectionModeCM.value() != 'all'
        self.genSelectionLineEdit.setEnabled(enable)
        self.genSelectionButton.setEnabled(enable)

    # save settings in project
    def writeGenerateOptions(self):
        self.genLineLayerCM.writeToProject()
        self.genLineRouteFieldCM.writeToProject()
        self.genPointLayerCM.writeToProject()
        self.genPointRouteFieldCM.writeToProject()
        self.genPointMeasureFieldCM.writeToProject()
        self.genMeasureUnitCM.writeToProject()
        self.genSelectionModeCM.writeToProject()
        self.genSelectionWM.writeToProject()
        self.genThresholdWM.writeToProject()
        self.genSnapWM.writeToProject()
        self.genParallelModeCM.writeToProject()
        self.genExtrapolateWM.writeToProject()
        self.genCompositeRouteIdWM.writeToProject()
        self.genCompositeRouteFieldsWM.writeToProject()
        self.genOfficialMeasuresWM.writeToProject()
        self.genStrictDirectionWM.writeToProject()
        self.genSharedGeometryWM.writeToProject()
        self.genTolerantModeWM.writeToProject()
        self.genRamalHandlingWM.writeToProject()
        self.genRoundaboutHandlingWM.writeToProject()
        self.genDiagnosticsWM.writeToProject()
        self.genOutputNameWM.writeToProject()

    def readGenerateOptions(self):
        self.genLineLayerCM.readFromProject()
        self.genLineRouteFieldCM.readFromProject()
        self.genPointLayerCM.readFromProject()
        self.genPointRouteFieldCM.readFromProject()
        self.genPointMeasureFieldCM.readFromProject()
        self.genMeasureUnitCM.readFromProject()
        self.genSelectionModeCM.readFromProject()
        self.genSelectionWM.readFromProject()
        self.genThresholdWM.readFromProject()
        self.genSnapWM.readFromProject()
        self.genParallelModeCM.readFromProject()
        self.genExtrapolateWM.readFromProject()
        self.genCompositeRouteIdWM.readFromProject()
        self.genCompositeRouteFieldsWM.readFromProject()
        self.genOfficialMeasuresWM.readFromProject()
        self.genStrictDirectionWM.readFromProject()
        self.genSharedGeometryWM.readFromProject()
        self.genTolerantModeWM.readFromProject()
        self.genRamalHandlingWM.readFromProject()
        self.genRoundaboutHandlingWM.readFromProject()
        self.genDiagnosticsWM.readFromProject()
        self.genOutputNameWM.readFromProject()

    def getGenerateSelection(self):
        return map(str.strip, self.genSelectionLineEdit.text().split(','))

    def openGenerateSelectionDialog(self):
        if not self.genSelectionDialog:
            self.genSelectionDialog = LrsSelectionDialog()
            self.genSelectionDialog.accepted.connect(self.generateSelectionDialogAccepted)

        layer = self.genLineLayerCM.getLayer()
        fieldName = self.genLineRouteFieldCM.getFieldName()
        select = self.getGenerateSelection()
        self.genSelectionDialog.load(layer, fieldName, select)

        self.genSelectionDialog.show()

    def generateSelectionDialogAccepted(self):
        selection = self.genSelectionDialog.selected()
        selection = ",".join(map(str, selection))
        self.genSelectionLineEdit.setText(selection)

    def getGenerateCrs(self):
        crs = None
        # debug ( "genLineLayerCM = %s" % self.genLineLayerCM )
        lineLayer = self.genLineLayerCM.getLayer()
        if lineLayer:
            # debug('lineLayer.crs().authid() = %s' % lineLayer.crs().authid())
            crs = lineLayer.crs()

        if isProjectCrsEnabled():
            # #debug ('enabled mapCanvas crs = %s' % self.iface.mapCanvas().mapSettings().destinationCrs().authid() )
            crs = getProjectCrs()
        return crs

    # set threshold units according to current crs
    def updateLabelsUnits(self):
        crs = self.getGenerateCrs()
        label = self.getThresholdLabel(crs)
        self.genThresholdLabel.setText(label)
        #label = "Max lines snap" + self.getUnitsLabel(crs)
        label = QCoreApplication.translate("LrsDockWidget", "Max lines snap") + self.getUnitsLabel(crs)
        self.genSnapLabel.setText(label)

    def generateLrs(self):
        # debug ( 'generateLrs')
        self.deleteLrs()

        self.errorVisualizer.clearHighlight()

        self.writeGenerateOptions()

        crs = self.getGenerateCrs()

        selection = self.getGenerateSelection()
        snap = self.genSnapSpin.value()
        threshold = self.genThresholdSpin.value()
        parallelMode = self.genParallelModeCM.value()
        extrapolate = self.genExtrapolateCheckBox.isChecked()
        compositeRouteFields = self.genCompositeRouteIdLineEdit.text()

        # self.mapUnitsPerMeasureUnit = self.genMapUnitsPerMeasureUnitSpin.value()
        measureUnit = self.genMeasureUnitCM.unit()

        self.lrs = LrsCalib(self.genLineLayerCM.getLayer(), self.genLineRouteFieldCM.getFieldName(),
                            self.genPointLayerCM.getLayer(), self.genPointRouteFieldCM.getFieldName(),
                            self.genPointMeasureFieldCM.getFieldName(), selectionMode=self.genSelectionModeCM.value(),
                            selection=selection, crs=crs, snap=snap, threshold=threshold, parallelMode=parallelMode,
                            extrapolate=extrapolate, measureUnit=measureUnit,
                            useCompositeRouteId=self.genCompositeRouteIdCheckBox.isChecked(),
                            compositeRouteFields=compositeRouteFields,
                            useOfficialArcMeasures=self.genOfficialMeasuresCheckBox.isChecked(),
                            strictDirection=self.genStrictDirectionCheckBox.isChecked(),
                            allowSharedGeometryDirections=self.genSharedGeometryCheckBox.isChecked(),
                            tolerantMode=self.genTolerantModeCheckBox.isChecked(),
                            specialRamalHandling=self.genRamalHandlingCheckBox.isChecked(),
                            specialRoundaboutHandling=self.genRoundaboutHandlingCheckBox.isChecked(),
                            generateDiagnostics=self.genDiagnosticsCheckBox.isChecked())

        self.genProgressLabel.setText("Registrant elements")
        self.lrs.progressChanged.connect(self.showGenProgress)
        self.lrs.calibrate()

        self.hideGenProgress()
        self.resetStats()

        # ------------------- errors -------------------
        self.errorZoomButton.setEnabled(False)
        self.errorModel = LrsErrorModel()
        self.errorModel.addErrors(self.lrs.getErrors())

        self.sortErrorModel = QSortFilterProxyModel()
        self.sortErrorModel.setFilterKeyColumn(-1)  # all columns
        self.sortErrorModel.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.sortErrorModel.setDynamicSortFilter(True)
        self.sortErrorModel.setSourceModel(self.errorModel)

        self.errorView.setModel(self.sortErrorModel)
        self.sortErrorModel.sort(0)
        self.errorView.resizeColumnsToContents()
        self.errorView.setSelectionBehavior(QAbstractItemView.SelectRows)
        # Attention, if selectionMode is QTableView.SingleSelection, selection is not
        # cleared if deleted row was selected (at least one row is always selected)
        self.errorView.setSelectionMode(QTableView.SingleSelection)
        self.errorView.selectionModel().selectionChanged.connect(self.errorSelectionChanged)

        self.lrs.updateErrors.connect(self.updateErrors)

        self.resetErrorLayers()
        self.resetQualityLayer()

        # layers must be tested 'is not None' (because layers have __len__(), some strange len)
        if self.errorPointLayer is not None or self.errorLineLayer is not None or self.qualityLayer is not None:
            self.iface.mapCanvas().refresh()

        self.lrs.edited.connect(self.lrsEdited)

        self.resetGenButtons()
        self.resetLocateRoutes()
        self.resetEventsButtons()
        self.resetMeasureButtons()
        self.updateMeasureUnits()
        self.enableTabs()

    def isCalibrated(self):
        return self.lrs is not None and self.lrs.isCalibrated()

    def resetGenButtons(self):
        self.genCreateOutputButton.setEnabled(
            self.isCalibrated() and len(self.genOutputNameLineEdit.text().strip()) > 0)

    def createLrsOutput(self):
        output = LrsOutput(self.iface, self.lrs, self.showGenProgress)
        output.output(self.genOutputNameLineEdit.text().strip())
        self.hideGenProgress()

    def showGenProgress(self, label, percent):
        self.genProgressFrame.show()
        self.genProgressLabel.setText(label)
        self.genProgressBar.setValue(int(percent))

    def hideGenProgress(self):
        self.genProgressFrame.hide()

    # ------------------------------- ERRORS -------------------------------
    def updateErrors(self, errorUpdates):
        # #debug ( "updateErrors" )
        # because SingleSelection does not allow to deselect row, we have to clear selection manually
        index = self.getSelectedErrorIndex()
        if index:
            rows = self.errorModel.rowsToBeRemoved(errorUpdates)
            selected = index.row()
            if selected in rows:
                self.errorView.selectionModel().clear()
        self.errorModel.updateErrors(errorUpdates)
        self.errorSelectionChanged()
        self.updateErrorLayers(errorUpdates)
        self.updateQualityLayer(errorUpdates)

    # def errorSelectionChanged(self, selected, deselected ):
    def errorSelectionChanged(self):
        error = self.getSelectedError()
        self.errorVisualizer.highlight(error, self.lrs.crs)
        self.errorZoomButton.setEnabled(error is not None)

    def getSelectedErrorIndex(self):
        sm = self.errorView.selectionModel()
        if not sm.hasSelection():
            return None
        index = sm.selection().indexes()[0]
        index = self.sortErrorModel.mapToSource(index)
        return index

    def getSelectedError(self):
        index = self.getSelectedErrorIndex()
        if not index:
            return None
        return self.errorModel.getError(index)

    def errorZoom(self):
        error = self.getSelectedError()
        if not error:
            return
        self.errorVisualizer.zoom(error, self.lrs.crs)

        # add new error layers to map

    def addErrorLayers(self):
        project = QgsProject.instance()

        if self.errorLineLayer is None:
            self.errorLineLayer = LrsErrorLineLayer(self.lrs.crs)
            self.errorLineLayerManager = LrsErrorLayerManager(self.errorLineLayer)
            self.errorLineLayer.renderer().symbol().setColor(QColor(Qt.red))
            self.resetErrorLineLayer()
            QgsProject.instance().addMapLayers([self.errorLineLayer, ])
            project.writeEntry(PROJECT_PLUGIN_NAME, "errorLineLayerId", self.errorLineLayer.id())

        if self.errorPointLayer is None:
            self.errorPointLayer = LrsErrorPointLayer(self.lrs.crs)
            self.errorPointLayerManager = LrsErrorLayerManager(self.errorPointLayer)
            self.errorPointLayer.renderer().symbol().setColor(QColor(Qt.red))
            self.resetErrorPointLayer()
            QgsProject.instance().addMapLayers([self.errorPointLayer, ])
            project.writeEntry(PROJECT_PLUGIN_NAME, "errorPointLayerId", self.errorPointLayer.id())

    # reset error layers content (features)
    def resetErrorLayers(self):
        # #debug ( "resetErrorLayers" )
        self.resetErrorPointLayer()
        self.resetErrorLineLayer()

    def updateErrorLayers(self, errorUpdates):
        if self.errorPointLayerManager:
            self.errorPointLayerManager.updateErrors(errorUpdates)
        if self.errorLineLayerManager:
            self.errorLineLayerManager.updateErrors(errorUpdates)

    def updateQualityLayer(self, errorUpdates):
        if self.qualityLayerManager:
            self.qualityLayerManager.update(errorUpdates)

    def resetErrorPointLayer(self):
        # debug("resetErrorPointLayer %s" % self.errorPointLayer)
        if self.errorPointLayerManager is None:
            return
        self.errorPointLayerManager.clear()
        errors = self.lrs.getErrors()
        self.errorPointLayerManager.addErrors(errors, self.lrs.crs)

    def resetErrorLineLayer(self):
        if self.errorLineLayerManager is None:
            return
        self.errorLineLayerManager.clear()
        errors = self.lrs.getErrors()
        self.errorLineLayerManager.addErrors(errors, self.lrs.crs)

    def addQualityLayer(self):
        if not self.qualityLayer:
            self.qualityLayer = LrsQualityLayer(self.lrs.crs)
            self.qualityLayerManager = LrsQualityLayerManager(self.qualityLayer)

            self.resetQualityLayer()
            QgsProject.instance().addMapLayers([self.qualityLayer, ])
            project = QgsProject.instance()
            project.writeEntry(PROJECT_PLUGIN_NAME, "qualityLayerId", self.qualityLayer.id())

    def resetQualityLayer(self):
        # #debug ( "resetQualityLayer %s" % self.qualityLayer )
        if not self.qualityLayerManager: return
        self.qualityLayerManager.clear()
        features = self.lrs.getQualityFeatures()
        self.qualityLayerManager.addFeatures(features, self.lrs.crs)

    # ------------------------ common layer for LOCATE, EVENTS, MEASURES -------------
    def showLrsLayerProgressBar(self):
        self.locateProgressBar.show()
        self.eventsProgressBar.show()
        self.measureProgressBar.show()

    def hideLrsLayerProgressBar(self):
        self.locateProgressBar.hide()
        self.eventsProgressBar.hide()
        self.measureProgressBar.hide()

    def loadLrsLayerProgress(self, percent):
        self.locateProgressBar.setValue(int(percent))
        self.eventsProgressBar.setValue(int(percent))
        self.measureProgressBar.setValue(int(percent))

    # ------------------------------------ LOCATE ------------------------------------
    def resetLocateOptions(self):
        self.locateHighlightWM.reset()
        self.locateBufferWM.reset()

    def readLocateOptions(self):
        self.locateHighlightWM.readFromProject()
        self.locateBufferWM.readFromProject()

    def resetLocateRoutes(self):
        # debug("resetLocateRoutes lrsLayer: %s" % (self.lrsLayer if self.lrsLayer else None))
        currentRoute = self.locateRouteCM.value() if hasattr(self, 'locateRouteCM') else None
        routeFilter = ''
        if hasattr(self, 'locateRouteFilterLineEdit'):
            routeFilter = self.locateRouteFilterLineEdit.text().strip().lower()
        options = [(None, '')]
        self.locateRouteGroups = {}
        if self.lrsLayer:
            groups = {}
            for routeId in self.lrsLayer.getRouteIds():
                label = self.locateRouteDisplayLabel(routeId)
                groups.setdefault(label, []).append(routeId)

            for label in sorted(groups.keys()):
                if routeFilter and routeFilter not in label.lower():
                    continue
                value = 'group:%s' % label
                self.locateRouteGroups[value] = sorted(groups[label], key=lambda routeId: "%s" % routeId)
                options.append((value, label))
        # debug("resetLocateRoutes options: %s" % options)
        self.locateRouteCM.setOptions(options)
        if currentRoute is not None:
            idx = self.locateRouteCombo.findData(currentRoute, Qt.UserRole)
            if idx >= 0:
                self.locateRouteCombo.setCurrentIndex(idx)

    def locateRouteDisplayLabel(self, routeId):
        text = "%s" % routeId
        parts = [p for p in text.split('_') if p]
        direction = None
        directionIndex = None
        for i in range(len(parts) - 1, -1, -1):
            part = parts[i]
            if part.lower() in ('creixent', 'decreixent'):
                direction = part
                directionIndex = i
                break

        roadParts = parts[:directionIndex] if directionIndex is not None else parts
        road = "/".join(roadParts) if roadParts else text
        # Preserve the official logical road/subroad code. C-17 and
        # C-17LD/510-CA are different routes and must not be merged in Locate.
        if direction:
            return "%s %s" % (road, direction)
        return road

    def locateSelectedRouteIds(self):
        routeValue = self.locateRouteCM.value()
        if routeValue is None:
            return []
        return self.locateRouteGroups.get(routeValue, [routeValue])

    def mergeMeasureRanges(self, ranges):
        if not ranges:
            return []
        ranges = [[min(r[0], r[1]), max(r[0], r[1])] for r in ranges]
        ranges.sort()
        merged = [ranges[0]]
        for start, end in ranges[1:]:
            last = merged[-1]
            if start <= last[1]:
                last[1] = max(last[1], end)
            else:
                merged.append([start, end])
        return merged

    def locateRouteChanged(self):
        # #debug ('locateRouteChanged')
        rangesText = ''
        routeIds = self.locateSelectedRouteIds()
        if self.lrsLayer and routeIds:
            ranges = []
            for routeId in routeIds:
                ranges.extend(self.lrsLayer.getRouteMeasureRanges(routeId))
            rangeLabels = []
            for r in self.mergeMeasureRanges(ranges):
                rangeLabels.append("%s-%s" % (
                    formatMeasure(r[0], self.lrsLayer.measureUnit), formatMeasure(r[1], self.lrsLayer.measureUnit)))
            rangesText = ", ".join(rangeLabels)
        # #debug ('ranges: %s' % rangesText )
        self.locateRanges.setText(rangesText)

        self.resetLocateEvent()

    def locateBufferChanged(self):
        self.locateBufferWM.writeToProject()

    def resetLocateEvent(self):
        self.clearLocateHighlight()
        routeIds = self.locateSelectedRouteIds()
        measure = self.locateMeasureSpin.value()
        coordinates = ''
        point = None
        if routeIds:
            error = None
            for routeId in routeIds:
                point, error = self.lrsLayer.eventPointXY(routeId, measure)
                if point:
                    break

            if point:
                mapSettings = self.iface.mapCanvas().mapSettings()
                if isProjectCrsEnabled() and getProjectCrs() != self.lrsLayer.crs:
                    transform = QgsCoordinateTransform(self.lrsLayer.crs, mapSettings.destinationCrs(),
                                                       QgsProject.instance())
                    point = transform.transform(point)
                coordinates = "%.3f,%.3f" % (point.x(), point.y())
            else:
                coordinates = error

        self.locatePoint = point  # QgsPointXY
        self.highlightLocatePoint()

        self.locateCoordinates.setText(coordinates)

        self.locateCenterButton.setEnabled(bool(point))
        self.locateZoomButton.setEnabled(bool(point))

    def activateLocatePickTool(self):
        if not self.lrsLayer:
            self.locateCoordinates.setText(self.tr('La capa LRS no està disponible'))
            return
        canvas = self.iface.mapCanvas()
        self.previousMapTool = canvas.mapTool()
        if self.locatePickMapTool is None:
            self.locatePickMapTool = QgsMapToolEmitPoint(canvas)
            self.locatePickMapTool.canvasClicked.connect(self.locateMapClicked)
        canvas.setMapTool(self.locatePickMapTool)
        self.locateCoordinates.setText(self.tr('Fes clic al mapa'))

    def locateMapClicked(self, point, button):
        if not self.lrsLayer:
            return

        mapSettings = self.iface.mapCanvas().mapSettings()
        lrsPoint = point
        if isProjectCrsEnabled() and mapSettings.destinationCrs() != self.lrsLayer.crs:
            transform = QgsCoordinateTransform(mapSettings.destinationCrs(), self.lrsLayer.crs,
                                               QgsProject.instance())
            lrsPoint = transform.transform(point)

        # Use a small click radius. If a route is selected in Locate, constrain
        # the inverse lookup to that logical road/sense to avoid wrong PKs at
        # junctions or parallel carriageways.
        threshold = max(self.iface.mapCanvas().mapUnitsPerPixel() * 12, 10.0)
        threshold = min(threshold, 50.0)
        selectedRouteIds = self.locateSelectedRouteIds()
        routeIds = selectedRouteIds if selectedRouteIds else None
        routeId, measure, distance = self.lrsLayer.pointMeasureForRoutes(lrsPoint, threshold, routeIds)

        if routeId is None or measure is None:
            self.locateCoordinates.setText(self.tr('No hi ha cap ruta LRS dins la tolerància del clic'))
            self.restorePreviousMapTool()
            return

        self.selectLocateRouteId(routeId)
        self.locateMeasureSpin.setValue(float(measure))
        self.resetLocateEvent()
        self.locateCoordinates.setText('%s | %s | d=%.2f' % (
            self.locateRouteDisplayLabel(routeId),
            formatMeasure(measure, self.lrsLayer.measureUnit),
            distance or 0.0))
        self.restorePreviousMapTool()

    def restorePreviousMapTool(self):
        if self.previousMapTool:
            self.iface.mapCanvas().setMapTool(self.previousMapTool)
            self.previousMapTool = None

    def selectLocateRouteId(self, routeId):
        targetValue = None
        for value, routeIds in self.locateRouteGroups.items():
            if routeId in routeIds:
                targetValue = value
                break
        if targetValue is None:
            targetValue = routeId

        idx = self.locateRouteCombo.findData(targetValue, Qt.UserRole)
        if idx < 0 and self.locateRouteFilterLineEdit.text():
            self.locateRouteFilterLineEdit.clear()
            idx = self.locateRouteCombo.findData(targetValue, Qt.UserRole)
        if idx >= 0:
            self.locateRouteCombo.setCurrentIndex(idx)

    def locateHighlightChanged(self):
        # #debug ('locateHighlightChanged')
        self.clearLocateHighlight()
        self.locateHighlightWM.writeToProject()
        self.highlightLocatePoint()

    def highlightLocatePoint(self):
        # #debug ('highlightLocatePoint')
        self.clearLocateHighlight()
        if not self.locatePoint: return
        if not self.locateHighlightCheckBox.isChecked(): return

        mapCanvas = self.iface.mapCanvas()
        mapSettings = mapCanvas.mapSettings()
        # QgsHighlight does reprojection from layer CRS
        crs = getProjectCrs() if isProjectCrsEnabled() else self.lrsLayer.crs
        layer = QgsVectorLayer('Point?crs=' + crsString(crs), 'LRS locate highlight', 'memory')
        #self.locateHighlight = QgsHighlight(mapCanvas, QgsGeometry.fromPoint(self.locatePoint), layer)
        # QgsGeometry(QgsPoint) takes ownership!
        self.locateHighlight = QgsHighlight(mapCanvas, QgsGeometry(QgsPoint(self.locatePoint)), layer)
        # highlight point size is hardcoded in QgsHighlight
        self.locateHighlight.setWidth(2)
        self.locateHighlight.setColor(Qt.yellow)
        self.locateHighlight.show()

    def clearLocateHighlight(self):
        # #debug ('clearLocateHighlight')
        if self.locateHighlight:
            del self.locateHighlight
            self.locateHighlight = None

    def locateCenter(self):
        if not self.locatePoint: return
        mapCanvas = self.iface.mapCanvas()
        extent = mapCanvas.extent()
        extent.scale(1.0, self.locatePoint.x(), self.locatePoint.y())

        self.iface.mapCanvas().setExtent(extent)
        self.iface.mapCanvas().refresh()

    def locateZoom(self):
        if not self.locatePoint: return
        p = self.locatePoint
        b = self.locateBufferSpin.value()
        extent = QgsRectangle(p.x() - b, p.y() - b, p.x() + b, p.y() + b)

        self.iface.mapCanvas().setExtent(extent)
        self.iface.mapCanvas().refresh()

    # ---------------------------------- EVENTS ----------------------------------

    def addEventsDefaultDirectionOption(self):
        parent = self.eventsRouteFieldCombo.parentWidget()
        layout = parent.layout() if parent else None
        self.eventsDefaultDirectionLabel = QLabel(self.tr('Sentit per defecte'), parent)
        self.eventsDefaultDirectionCombo = QComboBox(parent)
        self.eventsDefaultDirectionCombo.setToolTip(
            self.tr('Sentit utilitzat quan la taula d\'esdeveniments només té el codi de carretera.'))
        self.eventsDefaultDirectionCM = LrsComboManager(self.eventsDefaultDirectionCombo, options=(
            (None, self.tr('Cap')),
            ('Creixent', self.tr('Creixent')),
            ('Decreixent', self.tr('Decreixent'))),
            defaultValue=None, settingsName='eventsDefaultDirection')

        if layout and isinstance(layout, QGridLayout):
            layout.addWidget(self.eventsDefaultDirectionLabel, 2, 0)
            layout.addWidget(self.eventsDefaultDirectionCombo, 2, 1)

    def resetEventsOptions(self):
        self.eventsLayerCM.reset()
        self.eventsRouteFieldCM.reset()
        self.eventsMeasureStartFieldCM.reset()
        self.eventsMeasureEndFieldCM.reset()
        self.eventsDefaultDirectionCM.reset()
        # Offset
        self.eventsOffsetStartFieldCM.reset()
        self.eventsOffsetEndFieldCM.reset()
        self.eventsFeaturesSelectCM.reset()
        self.eventsOutputNameWM.reset()
        self.eventsErrorFieldWM.reset()

        self.resetEventsButtons()

    def resetEventsOptionsAndWrite(self):
        self.resetEventsOptions()
        self.writeEventsOptions()

    def resetEventsButtons(self):
        enabled = bool(
            self.lrsLayer) and self.eventsLayerCombo.currentIndex() != -1 and self.eventsRouteFieldCombo.currentIndex() != -1 and self.eventsMeasureStartFieldCombo.currentIndex() != -1 and bool(
            self.eventsOutputNameLineEdit.text())

        self.eventsButtonBox.button(QDialogButtonBox.Ok).setEnabled(enabled)

    # save settings in project
    def writeEventsOptions(self):
        self.eventsLayerCM.writeToProject()
        self.eventsRouteFieldCM.writeToProject()
        self.eventsMeasureStartFieldCM.writeToProject()
        self.eventsMeasureEndFieldCM.writeToProject()
        self.eventsDefaultDirectionCM.writeToProject()
        # Offset
        self.eventsOffsetStartFieldCM.writeToProject()
        self.eventsOffsetEndFieldCM.writeToProject()
        self.eventsFeaturesSelectCM.writeToProject()
        self.eventsOutputNameWM.writeToProject()
        self.eventsErrorFieldWM.writeToProject()

    def readEventsOptions(self):
        self.eventsLayerCM.readFromProject()
        self.eventsRouteFieldCM.readFromProject()
        self.eventsMeasureStartFieldCM.readFromProject()
        self.eventsMeasureEndFieldCM.readFromProject()
        self.eventsDefaultDirectionCM.readFromProject()
        # Offset
        self.eventsOffsetStartFieldCM.readFromProject()
        self.eventsOffsetEndFieldCM.readFromProject()
        self.eventsFeaturesSelectCM.readFromProject()
        self.eventsOutputNameWM.readFromProject()
        self.eventsErrorFieldWM.readFromProject()

    def createEvents(self):
        self.writeEventsOptions()
        self.eventsProgressBar.show()

        layer = self.eventsLayerCM.getLayer()
        routeFieldName = self.eventsRouteFieldCM.getFieldName()
        startFieldName = self.eventsMeasureStartFieldCM.getFieldName()
        endFieldName = self.eventsMeasureEndFieldCM.getFieldName()
        defaultDirection = self.eventsDefaultDirectionCM.value()
        # Offset
        startOffsetFieldName = self.eventsOffsetStartFieldCM.getFieldName()
        endOffsetFieldName = self.eventsOffsetEndFieldCM.getFieldName()
        featuresSelect = self.eventsFeaturesSelectCM.value()
        outputName = self.eventsOutputNameLineEdit.text()
        if not outputName: outputName = self.eventsOutputNameWM.defaultValue()
        errorFieldName = self.eventsErrorFieldLineEdit.text()

        events = LrsEvents(self.lrsLayer, self.eventsProgressBar)
        #events.create(layer, featuresSelect, routeFieldName, startFieldName, endFieldName, errorFieldName, outputName)
        events.create(layer, featuresSelect, routeFieldName, startFieldName, endFieldName, errorFieldName, outputName,
                      startOffsetFieldName, endOffsetFieldName, defaultDirection)

    # ------------------- MEASURE -------------------

    def resetMeasureOptions(self):
        # #debug('resetMeasureOptions')
        self.measureLayerCM.reset()
        self.measureRouteFieldCM.reset()
        self.measureThresholdWM.reset()
        self.measureOutputNameWM.reset()
        self.measureOutputRouteFieldWM.reset()
        self.measureMeasureFieldWM.reset()

        self.resetMeasureButtons()

    def resetMeasureOptionsAndWrite(self):
        self.resetMeasureOptions()
        self.writeMeasureOptions()

    def resetMeasureButtons(self):
        # #debug('resetMeasureButtons')
        enabled = bool(self.lrsLayer) and self.measureLayerCombo.currentIndex() != -1 and bool(
            self.measureOutputNameLineEdit.text()) and bool(self.measureOutputRouteFieldLineEdit.text()) and bool(
            self.measureMeasureFieldLineEdit.text())

        self.measureButtonBox.button(QDialogButtonBox.Ok).setEnabled(enabled)

    # save settings in project
    def writeMeasureOptions(self):
        self.measureLayerCM.writeToProject()
        self.measureRouteFieldCM.writeToProject()
        self.measureThresholdWM.writeToProject()
        self.measureOutputNameWM.writeToProject()
        self.measureOutputRouteFieldWM.writeToProject()
        self.measureMeasureFieldWM.writeToProject()

    def readMeasureOptions(self):
        self.measureLayerCM.readFromProject()
        self.measureRouteFieldCM.readFromProject()
        self.measureThresholdWM.readFromProject()
        self.measureOutputNameWM.readFromProject()
        self.measureOutputRouteFieldWM.readFromProject()
        self.measureMeasureFieldWM.readFromProject()

    # set threshold units according to current crs
    def updateMeasureUnits(self):
        crs = self.lrsLayer.crs if self.lrsLayer else None
        label = self.getThresholdLabel(crs)
        # debug('updateMeasureUnits label = %s' % label)
        self.measureThresholdLabel.setText(label)

    def calculateMeasures(self):
        # #debug('calculateMeasures')
        self.writeMeasureOptions()

        self.measureProgressBar.show()

        layer = self.measureLayerCM.getLayer()
        routeFieldName = self.measureRouteFieldCM.getFieldName()
        threshold = self.measureThresholdSpin.value()
        outputName = self.measureOutputNameLineEdit.text()
        if not outputName: outputName = self.measureOutputNameWM.defaultValue()
        outputRouteFieldName = self.measureOutputRouteFieldLineEdit.text()
        measureFieldName = self.measureMeasureFieldLineEdit.text()

        measures = LrsMeasures(self.iface, self.lrsLayer, self.measureProgressBar)
        measures.calculate(layer, routeFieldName, outputRouteFieldName, measureFieldName, threshold, outputName)

    # ------------------- STATS -------------------

    def resetStats(self):
        # debug ( 'setStats' )
        # html = ''
        if self.lrs:
            #     if self.lrs.getEdited():
            #         html = 'Statistics are not available if an input layer has been edited after calibration. Run calibration again to get fresh statistics.'
            #     else:
            #         html = self.lrs.getStatsHtml()
            # self.statsTextEdit.setHtml(html)

            self.errorTotalLength.setText("%.3f" % self.lrs.getStat('length'))
            self.errorIncludedLength.setText("%.3f" % self.lrs.getStat('lengthIncluded'))
            self.errorSuccessLength.setText("%.3f" % self.lrs.getStat('lengthOk'))

    # -------------------- widget ----------------------
    def saveWidgetGeometry(self):
        # debug("LrsDockWidget.saveWidgetGeometry")
        settings = QgsSettings()
        settings.setValue("/Windows/lrs/geometry", self.saveGeometry())

    def restoreWidgetGeometry(self):
        # debug("LrsDockWidget.restoreWidgetGeometry")
        settings = QgsSettings()
        self.restoreGeometry(settings.value("/Windows/lrs/geometry", QByteArray()))
