"""The layers of ``survey-plan.gpkg`` on the map: the access points the user sets, and the plan.

The layer ``access_points`` belongs to the user. :func:`show_access` adds it to the project, limits its field ``role`` to the roles the library knows (a value map, so nobody has to spell one), and switches it to editing with the *Add point* tool, so that the next click on the map sets a point. :func:`commit_access` saves the edits before the library reads the file.

The other layers are the plan, which the library replaces as a whole. :func:`remove_plan` takes them off the map first (a layer that is open keeps the file busy on Windows), and :func:`show_plan` adds those that hold something, in a group of their own and with a style that says what the paper map says: the walk paths carry an arrow in their direction, the DGPS surveyor walks a dashed line, a vertical is a circle, a triangle or a square for one, two or three depths.
"""

from __future__ import annotations

from pathlib import Path

from ..core import survey

WHO_COLORS = ("#c8501e", "#5b3fc4", "#0a6b2d", "#a3176b", "#7a5200")
DGPS_COLOR = "#33414e"
INK = "#1f1f1e"
DANGER = "#c62828"
PURPLE = "#2e014b"
WATER_COLORS = {"slow, shallow": "#f6d9a3", "slow, deep": "#d99a2b", "fast, shallow": "#b4d3ee", "fast, deep": "#3f7fbf"}
DEPTH_MARKERS = {1: ("circle", "one depth"), 2: ("triangle", "two depths"), 3: ("square", "three depths")}
EVENT_MARKERS = {"break": ("diamond", "#6b6a66"), "pressure adjustment": ("star", DANGER), "battery change": ("cross_fill", "#5b3fc4")}


def _same_file(source: str, gpkg: Path) -> bool:
    try:
        return Path(source.split("|")[0]).resolve() == Path(gpkg).resolve()
    except OSError:
        return False


def _layer_name(source: str) -> str:
    for part in source.split("|")[1:]:
        if part.startswith("layername="):
            return part.split("=", 1)[1]
    return ""


def open_layers(gpkg: str | Path) -> dict[str, list]:
    """The layers of the project that read *gpkg*, by the name of the layer in the file."""
    from qgis.core import QgsProject, QgsVectorLayer

    found: dict[str, list] = {}
    for layer in QgsProject.instance().mapLayers().values():
        if isinstance(layer, QgsVectorLayer) and _same_file(layer.source(), Path(gpkg)):
            found.setdefault(_layer_name(layer.source()), []).append(layer)
    return found


def _group():
    from qgis.core import QgsProject

    root = QgsProject.instance().layerTreeRoot()
    return root.findGroup(survey.GROUP_NAME) or root.insertGroup(0, survey.GROUP_NAME)


def _label(layer, expression: str, *, size: float = 8.0) -> None:
    from qgis.core import QgsPalLayerSettings, QgsTextBufferSettings, QgsTextFormat, QgsVectorLayerSimpleLabeling

    settings = QgsPalLayerSettings()
    settings.fieldName = expression
    settings.isExpression = True
    text = QgsTextFormat()
    text.setSize(size)
    buffer = QgsTextBufferSettings()
    buffer.setEnabled(True)
    buffer.setSize(0.8)
    text.setBuffer(buffer)
    settings.setFormat(text)
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)


def _marker(shape: str, outline: str, *, fill: str = "#ffffff", size: float = 3.2, width: float = 0.6):
    from qgis.core import QgsMarkerSymbol

    return QgsMarkerSymbol.createSimple({"name": shape, "color": fill, "outline_color": outline, "outline_width": f"{width:g}", "size": f"{size:g}"})


def _arrow_line(color: str, *, width: float = 0.5, dashed: bool = False):
    """A line with an arrow head at the middle of every feature, pointing in its direction."""
    from qgis.core import QgsLineSymbol, QgsMarkerLineSymbolLayer, QgsMarkerSymbol

    symbol = QgsLineSymbol.createSimple({"color": color, "width": f"{width:g}", "line_style": "dash" if dashed else "solid"})
    heads = QgsMarkerLineSymbolLayer.create({"placement": "centralpoint", "rotate": "1"})
    heads.setSubSymbol(QgsMarkerSymbol.createSimple({"name": "filled_arrowhead", "color": color, "outline_style": "no", "size": "2.6"}))
    symbol.appendSymbolLayer(heads)
    return symbol


def _fill(color: str, *, opacity: float = 0.5, outline: str | None = None):
    from qgis.core import QgsFillSymbol

    properties = {"color": color, "outline_style": "no"}
    if outline:
        properties = {"color": color, "outline_color": outline, "outline_width": "0.3"}
    symbol = QgsFillSymbol.createSimple(properties)
    symbol.setOpacity(opacity)
    return symbol


def _values(layer, *names) -> list[tuple]:
    have = layer.fields().names()
    if any(name not in have for name in names):
        return []
    return sorted({tuple(feature[name] for name in names) for feature in layer.getFeatures()}, key=lambda row: tuple(str(v) for v in row))


def _categorized(layer, expression: str, categories: list) -> None:
    from qgis.core import QgsCategorizedSymbolRenderer, QgsRendererCategory

    if categories:
        layer.setRenderer(QgsCategorizedSymbolRenderer(expression, [QgsRendererCategory(value, symbol, text) for value, symbol, text in categories]))


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def style(layer, name: str) -> None:
    """Give the plan layer *name* its style."""
    from qgis.core import QgsSingleSymbolRenderer

    if name == "flowtracker_points":
        several = len({_int(s) for (s, _n) in _values(layer, "surveyor", "n_depths")}) > 1
        categories = []
        for surveyor, depths in _values(layer, "surveyor", "n_depths"):
            shape, words = DEPTH_MARKERS.get(_int(depths, 3), DEPTH_MARKERS[3])
            color = WHO_COLORS[(_int(surveyor, 1) - 1) % len(WHO_COLORS)]
            categories.append((f"{_int(surveyor, 1)}-{_int(depths, 3)}", _marker(shape, color), f"surveyor {_int(surveyor, 1)}, {words}" if several else f"vertical at {words}"))
        _categorized(layer, "to_string(\"surveyor\") || '-' || to_string(\"n_depths\")", categories)
        _label(layer, "\"label\"")
    elif name == "walk_paths":
        categories = []
        for who, number in _values(layer, "who", "number"):
            who_text, n = str(who), _int(number, 1)
            if who_text.lower() == "dgps":
                symbol, text = _arrow_line(DGPS_COLOR, width=0.4, dashed=True), f"DGPS surveyor {n}"
            elif who_text == "temperature":
                symbol, text = _arrow_line(WHO_COLORS[4]), "temperature survey"
            else:
                symbol, text = _arrow_line(WHO_COLORS[(n - 1) % 4]), f"surveyor {n}"
            categories.append((f"{who_text}-{n}", symbol, f"walk of {text}" if who_text != "temperature" else f"walk of the {text}"))
        _categorized(layer, "\"who\" || '-' || to_string(\"number\")", categories)
    elif name == "survey_events":
        categories = []
        for (kind,) in _values(layer, "kind"):
            shape, color = EVENT_MARKERS.get(str(kind), ("diamond", "#6b6a66"))
            categories.append((kind, _marker(shape, INK, fill=color, size=3.6, width=0.3), str(kind)))
        _categorized(layer, "kind", categories)
        _label(layer, "\"kind\" || ' ' || \"start_time\"", size=7.0)
    elif name == "drone_positions":
        layer.setRenderer(QgsSingleSymbolRenderer(_marker("cross2", "#5b3fc4", size=3.4, width=0.7)))
        _label(layer, "to_string(\"station\")")
    elif name == "drone_footprints":
        layer.setRenderer(QgsSingleSymbolRenderer(_fill("#5b3fc4", opacity=0.25, outline="#5b3fc4")))
    elif name == "drone_route":
        layer.setRenderer(QgsSingleSymbolRenderer(_arrow_line("#5b3fc4", width=0.4, dashed=True)))
    elif name == "kayak_tracks":
        categories = [(_int(kayaker, 1), _arrow_line(WHO_COLORS[(_int(kayaker, 1) - 1) % len(WHO_COLORS)], width=0.7), f"drift of kayaker {_int(kayaker, 1)}") for (kayaker,) in _values(layer, "kayaker")]
        _categorized(layer, "kayaker", categories)
    elif name == "kayak_avoid":
        layer.setRenderer(QgsSingleSymbolRenderer(_fill(DANGER, opacity=0.55, outline=DANGER)))
    elif name == "temperature_points":
        categories = [(water, _marker("circle", INK, fill=WATER_COLORS.get(str(water), "#ffffff"), size=3.0, width=0.4), str(water)) for (water,) in _values(layer, "water")]
        _categorized(layer, "water", categories)
        _label(layer, "\"label\"", size=7.0)
    elif name == "temperature_patches":
        categories = [(water, _fill(WATER_COLORS.get(str(water), "#cccccc"), opacity=0.6), f"{water} water") for (water,) in _values(layer, "water")]
        _categorized(layer, "water", categories)
    layer.triggerRepaint()


# -- the access points --------------------------------------------------------------
def show_access(gpkg: str | Path, roles: dict | None = None, *, iface=None, edit: bool = True):
    """Add the layer of the access points to the map (once) and switch it to editing. Returns the layer, or None when the file has none."""
    from qgis.core import QgsEditorWidgetSetup, QgsProject, QgsSingleSymbolRenderer, QgsVectorLayer

    existing = open_layers(gpkg).get(survey.ACCESS_LAYER) or []
    layer = existing[0] if existing else QgsVectorLayer(survey.layer_uri(gpkg, survey.ACCESS_LAYER), survey.ACCESS_NAME, "ogr")
    if not layer.isValid():
        return None
    if not existing:
        index = layer.fields().indexOf("role")
        if index >= 0:
            words = list(roles or survey.ROLES)
            layer.setEditorWidgetSetup(index, QgsEditorWidgetSetup("ValueMap", {"map": [{word: word} for word in words]}))
            layer.setDefaultValueDefinition(index, _default("'access'"))
        layer.setRenderer(QgsSingleSymbolRenderer(_marker("star", "#ffffff", fill=PURPLE, size=6.5, width=0.4)))
        _label(layer, "coalesce(\"role\", 'access') || coalesce(' (' || nullif(\"name\", '') || ')', '')", size=9.0)
        QgsProject.instance().addMapLayer(layer, False)
        _group().insertLayer(0, layer)
    if edit:
        if not layer.isEditable():
            layer.startEditing()
        if iface is not None:
            try:
                iface.setActiveLayer(layer)
                iface.actionAddFeature().trigger()
            except (AttributeError, RuntimeError):      # a QGIS without that tool, or without a window
                pass
    return layer


def _default(expression: str):
    from qgis.core import QgsDefaultValue

    return QgsDefaultValue(expression)


def commit_access(gpkg: str | Path) -> str:
    """Save the edits of the access layer. Returns what went wrong, or an empty text."""
    for layer in open_layers(gpkg).get(survey.ACCESS_LAYER) or []:
        if layer.isEditable() and layer.isModified():
            if not layer.commitChanges():
                return "; ".join(layer.commitErrors()) or "the edits of the layer of the access points could not be saved"
        elif layer.isEditable():
            layer.rollBack()
    return ""


# -- the plan -----------------------------------------------------------------------
def remove_plan(gpkg: str | Path) -> int:
    """Take the plan layers of *gpkg* off the map; the access layer stays. Returns how many were removed."""
    from qgis.core import QgsProject

    plan = {name for name, _kind, _title in survey.PLAN_LAYERS}
    ids = [layer.id() for name, layers in open_layers(gpkg).items() if name in plan for layer in layers]
    if ids:
        QgsProject.instance().removeMapLayers(ids)
    return len(ids)


def show_plan(answer: dict) -> list:
    """Add the layers of a plan that hold something, styled; returns them in drawing order."""
    from qgis.core import QgsProject, QgsVectorLayer

    gpkg = answer.get("gpkg")
    if not gpkg or not Path(gpkg).is_file():
        return []
    remove_plan(gpkg)
    group = _group()
    added = []
    for name, title in survey.layers_to_load(answer):
        layer = QgsVectorLayer(survey.layer_uri(gpkg, name), title, "ogr")
        if not layer.isValid():
            continue
        style(layer, name)
        QgsProject.instance().addMapLayer(layer, False)
        group.insertLayer(0, layer)             # the last one added lies on top
        added.append(layer)
    # the access points stay on top of the plan
    for layer in open_layers(gpkg).get(survey.ACCESS_LAYER) or []:
        node = QgsProject.instance().layerTreeRoot().findLayer(layer.id())
        if node is not None and node.parent() is group:
            clone = node.clone()
            group.insertChildNode(0, clone)
            group.removeChildNode(node)
    return added


def zoom_to(iface, layers: list) -> None:
    """Show all of *layers* on the map."""
    from qgis.core import QgsCoordinateTransform, QgsCsException, QgsProject, QgsRectangle

    if iface is None or not layers:
        return
    canvas = iface.mapCanvas()
    extent = None
    project = QgsProject.instance()
    for layer in layers:
        box = layer.extent()
        if box.isEmpty() and box.width() == 0 and box.height() == 0 and not layer.featureCount():
            continue
        transform = QgsCoordinateTransform(layer.crs(), canvas.mapSettings().destinationCrs(), project)
        try:
            box = transform.transformBoundingBox(box)
        except QgsCsException:                          # an extent that cannot be brought into the system of the map
            box = None
        if box is None:
            pass
        elif extent is None:
            extent = QgsRectangle(box)
        else:
            extent.combineExtentWith(box)
    if extent is not None and not extent.isNull() and extent.isFinite():
        extent.scale(1.1)
        canvas.setExtent(extent)
        canvas.refresh()
