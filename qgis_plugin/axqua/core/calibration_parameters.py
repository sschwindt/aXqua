"""The calibration parameters window as data: how its choices go into the case file.

Pure Python, no Qt: the window (:mod:`..gui.calibration_parameters_wizard`) shows the answer of ``axqua calibration <case> --parameters`` and calls :func:`merged` with what the user chose; the plugin then writes the result through ``axqua case write``.

Three kinds of parameter, written to three places:

* the roughness zones are in the roughness table of the case, which the window does not write;
* the outflow level is ``calibration.outflow_slope`` or ``calibration.outflow_offset`` (``[min, max]``), never both;
* further parameters are entries ``{name, min, max}`` of ``calibration.parameters``; an entry for a roughness zone there (``zone<N>``, the way before the roughness table) is kept as it was.
"""

from __future__ import annotations

import math

#: The ways to calibrate the outflow level, as the window offers them.
OUTFLOW_KINDS = ("none", "slope", "offset")
#: The setting of each way.
OUTFLOW_KEYS = {"slope": "outflow_slope", "offset": "outflow_offset"}


def parse_range(low_text: str, high_text: str, *, positive: bool = False) -> tuple[float, float]:
    """Two typed numbers as ``(min, max)``; :class:`ValueError` with a sentence otherwise. A decimal comma is read as a point."""
    try:
        low, high = (float(str(text).strip().replace(",", ".")) for text in (low_text, high_text))
    except ValueError:
        raise ValueError("Enter two numbers, for example 1.0e-5 and 2.0e-3.") from None
    if not (math.isfinite(low) and math.isfinite(high)):
        raise ValueError("Enter two finite numbers.")
    if not high > low:
        raise ValueError("The largest value must be larger than the smallest.")
    if positive and not low > 0:
        raise ValueError("An energy slope is larger than 0.")
    return low, high


def merged(data: dict, outflow: str, outflow_range: tuple[float, float] | None,
           further: list[dict]) -> dict:
    """The case file *data* with the outflow level and the further parameters as chosen.

    *outflow* is one of :data:`OUTFLOW_KINDS`; *further* the chosen entries ``{name, min,
    max}`` (a ``comment`` is kept). Every other entry of the block ``calibration`` stays
    as it was, and so do the entries of ``calibration.parameters`` for roughness zones.
    """
    if outflow not in OUTFLOW_KINDS:
        raise ValueError(f"unknown way to calibrate the outflow level: {outflow!r}")
    data = dict(data or {})
    block = dict(data.get("calibration") or {})
    for key in OUTFLOW_KEYS.values():
        block.pop(key, None)
    if outflow != "none":
        if outflow_range is None:
            raise ValueError("The outflow level needs a range.")
        block[OUTFLOW_KEYS[outflow]] = [float(outflow_range[0]), float(outflow_range[1])]
    kept = [dict(entry) for entry in block.get("parameters") or []
            if str(entry.get("name", "")).strip().lower().startswith("zone")]
    earlier = {str(entry.get("name", "")).strip(): entry
               for entry in block.get("parameters") or []}
    chosen = []
    for entry in further:
        name = str(entry["name"]).strip()
        row = {"name": name, "min": float(entry["min"]), "max": float(entry["max"])}
        comment = entry.get("comment") or (earlier.get(name) or {}).get("comment")
        if comment:
            row["comment"] = comment
        chosen.append(row)
    if kept or chosen:
        block["parameters"] = kept + chosen
    else:
        block.pop("parameters", None)
    data["calibration"] = block
    return data


def summary(outflow: str, outflow_range: tuple[float, float] | None,
            further: list[dict]) -> list[str]:
    """What Save writes, one line per setting."""
    lines = []
    if outflow == "none":
        lines.append("Outflow level: not calibrated (calibration.outflow_slope and "
                     "calibration.outflow_offset removed)")
    else:
        what = "energy slope at the outlet" if outflow == "slope" else "offset of the outflow level"
        unit = "" if outflow == "slope" else " m"
        low, high = outflow_range or (float("nan"), float("nan"))
        lines.append(f"Outflow level: {what} from {low:g} to {high:g}{unit} "
                     f"(calibration.{OUTFLOW_KEYS[outflow]})")
    if further:
        lines += [f"{entry['name']}: {float(entry['min']):g} to {float(entry['max']):g} "
                  "(calibration.parameters)" for entry in further]
    else:
        lines.append("No further parameters (calibration.parameters keeps only roughness "
                     "zones, if it has any)")
    return lines


def zone_text(zone: dict) -> str:
    """One roughness zone of the table, as the window lists it."""
    if zone.get("calibrated"):
        return (f"calibrated between {100 * float(zone['ks_min']):g} and "
                f"{100 * float(zone['ks_max']):g} cm")
    return f"fixed at {100 * float(zone['ks']):g} cm"
