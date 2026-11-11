"""Before a calibration: which roughness zones it varies, and why some are left out.

The plugin asks ``axqua calibration <case> --json`` (:meth:`RunnerClient.calibration_info`)
before it submits a calibration. When the measurement points leave a zone of the
roughness table without a point, or the water at a zone's points is shallower than its
largest ks, :func:`ask` shows what the calibration will do, with two buttons (the user's
order of 2026-11-11):

* **Calibrate only zones ...**: submit; the zones listed are the ones that will vary,
  the others keep the value of the built model;
* **Abort and check the ground truth in QGIS**: submit nothing, and add the roughness
  zones and the calibration points (labeled with their zone) to the map
  (:func:`show_in_qgis`).

Closing the window submits nothing either.
"""

from __future__ import annotations

from qgis.PyQt.QtWidgets import QMessageBox

from ..compat import enum_value, english_buttons, exec_dialog

SUBMIT, CHECK = "submit", "check"


def message(data: dict) -> str:
    """The text of the question, from the zone plan."""
    zones = {z["zone"]: z for z in data.get("zones") or []}
    lines = ["The calibration points decide which roughness zones can be calibrated."]
    excluded = data.get("excluded_zones") or []
    capped = data.get("capped_zones") or []
    for zone in excluded:
        reason = zones.get(zone, {}).get("reason") or ""
        lines.append(f"• Zone {zone} is left out: {reason}. It keeps the value of "
                     f"the built model.")
    for zone in capped:
        z = zones.get(zone, {})
        lines.append(
            f"• Zone {zone}: the largest ks is lowered from {z.get('ks_max'):g} m to "
            f"{z.get('ks_max_used'):g} m, the median measured water depth of its "
            f"{z.get('points')} points. Shallower than the roughness, the logarithmic "
            f"friction law does not apply.")
    calibrated = data.get("calibrated_zones") or []
    lines.append("")
    lines.append("Calibrate only zones " + ", ".join(str(z) for z in calibrated)
                 + ", or abort and look at the measurement points and the roughness "
                   "zones in QGIS first?")
    return "\n".join(lines)


def ask(parent, data: dict) -> str | None:
    """Show the question; ``SUBMIT``, ``CHECK`` or None (closed)."""
    box = QMessageBox(parent)
    box.setWindowTitle("Calibration: roughness zones")
    box.setIcon(enum_value(QMessageBox, "Icon.Question", "Question"))
    box.setText(message(data))
    calibrated = ", ".join(str(z) for z in data.get("calibrated_zones") or [])
    submit = box.addButton(f"Calibrate only zones {calibrated}",
                           enum_value(QMessageBox, "ButtonRole.AcceptRole", "AcceptRole"))
    check = box.addButton("Abort and check the ground truth in QGIS",
                          enum_value(QMessageBox, "ButtonRole.RejectRole", "RejectRole"))
    box.setDefaultButton(submit)
    english_buttons(box)
    exec_dialog(box)
    if box.clickedButton() is submit:
        return SUBMIT
    if box.clickedButton() is check:
        return CHECK
    return None


def show_in_qgis(data: dict) -> list:
    """Add the roughness zones and the calibration points to the map; returns the layers."""
    from qgis.core import (QgsCategorizedSymbolRenderer, QgsFeature, QgsGeometry,
                           QgsPalLayerSettings, QgsPointXY, QgsProject,
                           QgsRendererCategory, QgsSymbol, QgsVectorLayer,
                           QgsVectorLayerSimpleLabeling)

    layers = []
    project = QgsProject.instance()
    path = data.get("roughness_zones")
    field = data.get("zone_field") or "Zone ID"
    if path:
        zones = QgsVectorLayer(path, "Roughness zones (calibration check)", "ogr")
        if zones.isValid():
            values = sorted({f[field] for f in zones.getFeatures()
                             if field in zones.fields().names()})
            categories = []
            for value in values:
                symbol = QgsSymbol.defaultSymbol(zones.geometryType())
                symbol.setOpacity(0.55)
                left_out = value in (data.get("excluded_zones") or [])
                categories.append(QgsRendererCategory(
                    value, symbol, f"zone {value}" + (" (left out)" if left_out else "")))
            if categories:
                zones.setRenderer(QgsCategorizedSymbolRenderer(field, categories))
            label = QgsPalLayerSettings()
            label.fieldName = f"'zone ' || \"{field}\""
            label.isExpression = True
            zones.setLabeling(QgsVectorLayerSimpleLabeling(label))
            zones.setLabelsEnabled(True)
            project.addMapLayer(zones)
            layers.append(zones)
    points = data.get("points") or []
    if points:
        # fields in the URI: QgsField's type argument is QVariant in QGIS 3 and
        # QMetaType in QGIS 4
        layer = QgsVectorLayer(f"Point?crs=EPSG:{int(data.get('crs_epsg') or 25832)}"
                               "&field=situation:string&field=zone:integer"
                               "&field=depth:double",
                               "Calibration points (calibration check)", "memory")
        provider = layer.dataProvider()
        features = []
        for point in points:
            feature = QgsFeature(layer.fields())
            feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(point["x"], point["y"])))
            feature.setAttributes([point.get("situation"), point.get("zone"),
                                   point.get("depth")])
            features.append(feature)
        provider.addFeatures(features)
        layer.updateExtents()
        label = QgsPalLayerSettings()
        label.fieldName = "zone"
        layer.setLabeling(QgsVectorLayerSimpleLabeling(label))
        layer.setLabelsEnabled(True)
        project.addMapLayer(layer)
        layers.append(layer)
    return layers
