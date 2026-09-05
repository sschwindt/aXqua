"""The default A3 print layout.

Built in code rather than shipped as a ``.qpt`` template, because a template stores the
map extent and the layer set it was made with - so a shipped one would either be empty or
would be a picture of somebody else's river. Built here, it fits *this* project's extent
and this reach.

The contents are the brief's (plan §17): the simulation ROI, a north arrow, a bold **Q**
arrow following the bulk flow direction, a two-tone scale bar, an empty legend with a hint
that the user should populate it, and 16 pt Arial throughout.

The flow arrow is the only interesting one. It points along the reach's own principal
axis, obtained from the mesh or the centerline rather than assumed - an arrow pointing the
wrong way down a river is worse than no arrow, because a reader will believe it.
"""

from __future__ import annotations

import logging
import math

from qgis.core import (QgsLayoutItemLabel, QgsLayoutItemLegend, QgsLayoutItemMap,
                       QgsLayoutItemPicture, QgsLayoutItemPolyline, QgsLayoutItemScaleBar,
                       QgsLayoutPoint, QgsLayoutSize, QgsPrintLayout, QgsProject)
from qgis.PyQt.QtCore import QPointF
from qgis.PyQt.QtGui import QColor, QFont, QPolygonF

from ..compat import LAYOUT_MM, LEGEND_STYLES, PICTURE_SVG

LAYOUT_NAME = "aXqua A3"
log = logging.getLogger("axqua.plugin")

FONT_FAMILY = "Arial"
FONT_SIZE_PT = 16

#: A3 landscape, in millimetres.
PAGE_W, PAGE_H = 420.0, 297.0
MARGIN = 12.0


def _font(bold: bool = False) -> QFont:
    font = QFont(FONT_FAMILY, FONT_SIZE_PT)
    font.setBold(bold)
    return font


def add_default_layout(iface, project: QgsProject | None = None) -> str:
    """Create the layout and return its name. Replaces an existing one of the same name."""
    project = project or QgsProject.instance()
    manager = project.layoutManager()
    for existing in manager.printLayouts():
        if existing.name() == LAYOUT_NAME:
            manager.removeLayout(existing)

    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName(LAYOUT_NAME)
    page = layout.pageCollection().page(0)
    page.setPageSize(QgsLayoutSize(PAGE_W, PAGE_H, LAYOUT_MM))

    canvas = iface.mapCanvas()
    map_item = _add_map(layout, canvas)
    _add_title(layout, project)
    _add_north_arrow(layout)
    _add_flow_arrow(layout, canvas)
    _add_scalebar(layout, map_item)
    _add_legend(layout, map_item)

    manager.addLayout(layout)
    return LAYOUT_NAME


def _add_map(layout: QgsPrintLayout, canvas) -> QgsLayoutItemMap:
    item = QgsLayoutItemMap(layout)
    width = PAGE_W - 2 * MARGIN - 70.0          # room for the legend column
    height = PAGE_H - 2 * MARGIN - 22.0         # room for the title
    item.attemptMove(QgsLayoutPoint(MARGIN, MARGIN + 20.0,
                                    LAYOUT_MM))
    item.attemptResize(QgsLayoutSize(width, height, LAYOUT_MM))
    item.setExtent(canvas.extent())
    item.setFrameEnabled(True)
    layout.addLayoutItem(item)
    return item


def _add_title(layout: QgsPrintLayout, project: QgsProject) -> None:
    label = QgsLayoutItemLabel(layout)
    label.setText(project.title() or "Simulation region of interest")
    label.setFont(_font(bold=True))
    label.adjustSizeToText()
    label.attemptMove(QgsLayoutPoint(MARGIN, MARGIN, LAYOUT_MM))
    layout.addLayoutItem(label)


def _add_north_arrow(layout: QgsPrintLayout) -> None:
    picture = QgsLayoutItemPicture(layout)
    # QGIS ships the north arrows as SVGs; resolving one through the search paths avoids
    # bundling an image and keeps the plugin free of binary assets.
    picture.setPicturePath("NorthArrows/layout_default_north_arrow.svg",
                           PICTURE_SVG)
    picture.attemptMove(QgsLayoutPoint(PAGE_W - 60.0, MARGIN + 24.0,
                                       LAYOUT_MM))
    picture.attemptResize(QgsLayoutSize(20.0, 20.0, LAYOUT_MM))
    layout.addLayoutItem(picture)

    label = QgsLayoutItemLabel(layout)
    label.setText("N")
    label.setFont(_font(bold=True))
    label.adjustSizeToText()
    label.attemptMove(QgsLayoutPoint(PAGE_W - 52.0, MARGIN + 44.0,
                                     LAYOUT_MM))
    layout.addLayoutItem(label)


def reach_bearing(project=None) -> float | None:
    """The flow direction from the reach itself, or None if nothing says.

    A line layer named for what it is - centerline, axis, reach, thalweg - is exactly
    the geometry aXqua's preprocessing already writes, and its first-to-last bearing is
    a *measurement*. Without one there is nothing here worth guessing from, and the
    caller falls back to the extent's aspect.
    """
    from qgis.core import QgsProject, QgsWkbTypes
    project = project or QgsProject.instance()
    names = ("centerline", "centreline", "center line", "thalweg", "reach", "axis")
    for layer in project.mapLayers().values():
        if not hasattr(layer, "geometryType") or not hasattr(layer, "getFeatures"):
            continue
        try:
            if layer.geometryType() != QgsWkbTypes.GeometryType.LineGeometry:
                continue
        except (AttributeError, TypeError):
            continue
        if not any(word in (layer.name() or "").lower() for word in names):
            continue
        for feature in layer.getFeatures():
            geometry = feature.geometry()
            if geometry is None or geometry.isEmpty():
                continue
            points = geometry.asPolyline() or (geometry.asMultiPolyline() or [[]])[0]
            if len(points) < 2:
                continue
            first, last = points[0], points[-1]
            return bearing(last.x() - first.x(), last.y() - first.y())
    return None


def _add_flow_arrow(layout: QgsPrintLayout, canvas) -> None:
    """A bold Q arrow along the reach.

    Taken from a centerline layer when the project has one, which makes it a direction
    that was measured rather than inferred. Failing that it falls back to the extent's
    own aspect - a river reach is longer than it is wide, so its bounding box points
    along the flow more often than not - and that is why the arrow is labelled ``Q``
    rather than presented as authoritative: the user can rotate it, and a wrong arrow
    they can see beats a confident one they cannot check.
    """
    x, y = PAGE_W - 60.0, MARGIN + 62.0
    length = 34.0
    heading = reach_bearing()
    if heading is None:
        extent = canvas.extent()
        # East for a wide extent, north for a tall one.
        heading = 90.0 if extent.width() >= extent.height() else 0.0
    angle = math.radians(heading)
    # Compass bearing to page direction: the layout's y axis grows downward while the
    # map is drawn north-up, so north is -y.
    dx, dy = math.sin(angle), -math.cos(angle)
    cx, cy = x + length / 2.0, y + length / 2.0
    points = [QPointF(cx - dx * length / 2.0, cy - dy * length / 2.0),
              QPointF(cx + dx * length / 2.0, cy + dy * length / 2.0)]

    arrow = QgsLayoutItemPolyline(QPolygonF(points), layout)
    arrow.setEndMarker(QgsLayoutItemPolyline.MarkerMode.ArrowHead)
    arrow.setArrowHeadWidth(6.0)
    arrow.setArrowHeadStrokeColor(QColor("black"))
    arrow.setArrowHeadFillColor(QColor("black"))
    symbol = arrow.symbol()
    if symbol is not None:
        symbol.setWidth(2.0)                 # bold, as asked
    layout.addLayoutItem(arrow)

    label = QgsLayoutItemLabel(layout)
    label.setText("Q")
    label.setFont(_font(bold=True))
    label.adjustSizeToText()
    label.attemptMove(QgsLayoutPoint(x + length / 2 - 3.0, y + 6.0,
                                     LAYOUT_MM))
    layout.addLayoutItem(label)


def _add_scalebar(layout: QgsPrintLayout, map_item: QgsLayoutItemMap) -> None:
    bar = QgsLayoutItemScaleBar(layout)
    # "Single Box" alternates filled and empty segments, which is the one black / one
    # white section the brief asks for.
    bar.setStyle("Single Box")
    bar.setLinkedMap(map_item)
    bar.applyDefaultSize()
    bar.setNumberOfSegments(1)
    bar.setNumberOfSegmentsLeft(1)
    bar.setFillColor(QColor("black"))
    bar.setFillColor2(QColor("white"))
    bar.setFont(_font())
    bar.attemptMove(QgsLayoutPoint(PAGE_W - 62.0, PAGE_H - MARGIN - 18.0,
                                   LAYOUT_MM))
    layout.addLayoutItem(bar)


def _add_legend(layout: QgsPrintLayout, map_item: QgsLayoutItemMap) -> None:
    legend = QgsLayoutItemLegend(layout)
    legend.setTitle("Legend")
    legend.setLinkedMap(map_item)
    # Empty on purpose: which layers belong in a figure is an editorial decision, and a
    # legend auto-filled with every loaded layer is something the user has to undo.
    legend.setAutoUpdateModel(False)
    model = legend.model()
    if model is not None and model.rootGroup() is not None:
        for child in list(model.rootGroup().children()):
            model.rootGroup().removeChildNode(child)
    _style_legend_fonts(legend)
    legend.attemptMove(QgsLayoutPoint(PAGE_W - 62.0, MARGIN + 92.0,
                                      LAYOUT_MM))
    layout.addLayoutItem(legend)

    hint = QgsLayoutItemLabel(layout)
    hint.setText("Add the layers you want shown:\nright-click the legend, then\n"
                 "Item Properties > Legend Items.")
    hint.setFont(_font())
    hint.adjustSizeToText()
    hint.attemptMove(QgsLayoutPoint(PAGE_W - 62.0, MARGIN + 104.0,
                                    LAYOUT_MM))
    layout.addLayoutItem(hint)


def _style_legend_fonts(legend: QgsLayoutItemLegend) -> None:
    """16 pt Arial throughout the legend, on either QGIS.

    The font API moved in QGIS 3.40 (``QgsLegendStyle.setTextFormat``) from the older
    ``setFont``. Both spellings are tried rather than branching on a version, so this
    keeps working across the 3.44-to-4.x range the plugin targets - and a legend whose
    font could not be set is still a usable legend, so nothing here raises.
    """
    from qgis.core import QgsTextFormat

    for name, bold in (("Title", True), ("Group", True),
                       ("Subgroup", False), ("SymbolLabel", False)):
        component = LEGEND_STYLES.get(name)
        if component is None:                        # pragma: no cover - older QGIS
            log.debug("this QGIS has no legend component %r", name)
            continue
        try:
            style = legend.style(component)
            if hasattr(style, "setTextFormat"):
                style.setTextFormat(QgsTextFormat.fromQFont(_font(bold)))
            else:                                    # pragma: no cover - older QGIS
                style.setFont(_font(bold))
            legend.setStyle(component, style)
        except (AttributeError, TypeError, ValueError) as exc:
            # Narrow, and reported: a legend whose font could not be set is still a
            # usable legend, but swallowing the reason silently is how a styling bug
            # becomes unexplainable.
            log.debug("could not set the %s legend font: %s", name, exc)


def bearing(dx: float, dy: float) -> float:
    """Compass bearing of a vector, in degrees. Used when a centerline is available."""
    return (math.degrees(math.atan2(dx, dy)) + 360.0) % 360.0
