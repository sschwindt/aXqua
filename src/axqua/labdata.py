"""Physical-flume measurements as cross-section reference data.

A laboratory campaign on a scale model does not produce what
:mod:`axqua.ground_truth` expects. There are no coordinates and no point cloud: there
are a handful of **cross-sections**, each with a mean water depth and a maximum
velocity, at a few steady discharges. Comparing those against a model means comparing
*aggregates along a section* with aggregates along the same section - not a modelled
value at a point against a measured value at a point, which is what the calibration CSV
would otherwise imply.

This module reads such a campaign into a tidy per-section table. The model side of the
comparison is :func:`axqua.solvers.telemac.sections.section_statistics`, which reduces a
result file over the same section lines, and :func:`compare` puts the two side by side.

Scale
-----
A flume is usually a scale model, and Froude similarity scales the two quantities
differently: at a length scale ``L`` (prototype:model), lengths go as ``L`` and
velocities as ``sqrt(L)``. Which of the two conventions a published table is already in
is a property of that table, not something to infer, so both scales are explicit
arguments here and neither is guessed. :func:`read_depths` and :func:`read_velocities`
report the convention they were given, and :func:`compare` records it in its output, so
a comparison can never quietly be between a prototype model and a model-scale
measurement.
"""

from __future__ import annotations

import logging
import math
import re
from pathlib import Path

log = logging.getLogger("axqua")

#: discharge labels used in the flume campaign, and what they mean in the prototype
DISCHARGE_LABELS = {"Q30": 0.060, "MQ": 0.135, "HQ100": 1.000, "HQ": 1.000}

#: The same discharge is spelled differently on different sheets of one campaign - the
#: published depth summary says ``HQ100`` where the velocity summary says ``HQ``. Left
#: alone, the two tables simply fail to join and half the reference data disappears
#: into unmatched rows, which is the quiet kind of wrong.
LABEL_ALIASES = {"HQ": "HQ100", "Q100": "HQ100", "HQ_100": "HQ100"}


def canonical_label(label: str) -> str:
    """One spelling per discharge, so sheets of the same campaign join."""
    label = str(label).strip()
    return LABEL_ALIASES.get(label, label)

#: "Q30 - 60 L/s" -> ("Q30", 0.060)
_BLOCK = re.compile(r"^\s*(?P<label>[A-Za-z0-9]+)\s*-\s*(?P<value>[\d.,]+)\s*"
                    r"(?P<unit>L/s|l/s|m3/s|m³/s)\s*$")
#: "XS1 - US2" -> ("XS1", "US2")
_SECTION = re.compile(r"^\s*(?P<xs>XS\s*\d+)\s*(?:-\s*(?P<us>\S+))?\s*$", re.I)


def froude_velocity_scale(length_scale: float) -> float:
    """Velocity scale under Froude similarity for a given length scale."""
    return math.sqrt(length_scale)


def read_depths(path: Path, *, sheet=0, length_scale: float = 1.0) -> list[dict]:
    """Read the flume water-depth summary into one row per discharge and section.

    The sheet is a stack of blocks, one per discharge: a title row naming the
    discharge (``Q30 - 60 L/s``) whose remaining cells are the section labels
    (``XS1 - US2``), then a ``MEAN`` row and a standard-deviation row, both in
    millimetres. Depths are returned in **metres**, multiplied by *length_scale*.

    Returns a list of dicts with ``discharge_label``, ``discharge``, ``section``,
    ``station``, ``h_mean``, ``h_std``.
    """
    import pandas as pd

    frame = pd.read_excel(path, sheet_name=sheet, header=None)
    rows: list[dict] = []
    for index in range(frame.shape[0]):
        block = _BLOCK.match(str(frame.iat[index, 0]))
        if not block:
            continue
        label = canonical_label(block.group("label"))
        discharge = _to_cumecs(block.group("value"), block.group("unit"))
        sections = _section_labels(frame.iloc[index])
        mean = _labelled_row(frame, index, "mean")
        std = _labelled_row(frame, index, "std")
        if mean is None:
            log.warning("labdata: %s has no MEAN row, skipped", label)
            continue
        for column, (section, station) in sections.items():
            value = mean.iat[column]
            if not _is_number(value):
                continue
            rows.append({
                "discharge_label": label,
                "discharge": discharge,
                "section": section,
                "station": station,
                # millimetres in the sheet, metres in the model
                "h_mean": float(value) / 1000.0 * length_scale,
                "h_std": (float(std.iat[column]) / 1000.0 * length_scale
                          if std is not None and _is_number(std.iat[column]) else None),
            })
    if not rows:
        raise ValueError(f"{Path(path).name}: found no '<label> - <Q> L/s' blocks; is "
                         f"this the water-depth summary sheet?")
    log.info("labdata: %d depth values over %d discharges from %s (length scale %g)",
             len(rows), len({r["discharge_label"] for r in rows}), Path(path).name,
             length_scale)
    return rows


def read_velocities(path: Path, *, sheet=0, velocity_scale: float = 1.0) -> list[dict]:
    """Read the flume velocity summary into one row per discharge and section.

    The sheet is one table with a ``Discharge`` column filled only on the first row of
    each block, a section column, and the time-averaged maxima with their standard
    deviations. Velocities are returned in **m/s**, multiplied by *velocity_scale*.

    Returns dicts with ``discharge_label``, ``discharge``, ``section``, ``u_max``,
    ``u_std``, ``u3d_max``, ``u3d_std`` and ``u_section_mean``. A section where the ADV
    could not measure - too shallow, in this campaign - yields ``None`` rather than a
    row that looks measured.
    """
    import pandas as pd

    frame = pd.read_excel(path, sheet_name=sheet, header=None)
    header = _velocity_header(frame)
    columns = _velocity_columns(frame.iloc[header])
    rows: list[dict] = []
    label = None
    for index in range(header + 1, frame.shape[0]):
        cell = frame.iat[index, 0]
        if isinstance(cell, str) and cell.strip() in DISCHARGE_LABELS:
            label = canonical_label(cell.strip())
        if label is None:
            continue
        section_cell = frame.iat[index, columns["section"]]
        section = _SECTION.match(str(section_cell))
        if not section:
            continue
        row = {
            "discharge_label": label,
            "discharge": DISCHARGE_LABELS[label],
            "section": section.group("xs").replace(" ", "").upper(),
        }
        for key, column in columns.items():
            if key == "section":
                continue
            value = frame.iat[index, column]
            row[key] = float(value) * velocity_scale if _is_number(value) else None
        if row.get("u_max") is None and row.get("u3d_max") is None:
            log.info("labdata: %s %s has no ADV velocity (too shallow to measure)",
                     label, row["section"])
        rows.append(row)
    if not rows:
        raise ValueError(f"{Path(path).name}: no velocity rows found under the header")
    log.info("labdata: %d velocity rows over %d discharges from %s (velocity scale %g)",
             len(rows), len({r["discharge_label"] for r in rows}), Path(path).name,
             velocity_scale)
    return rows


def reference_table(depths: list[dict], velocities: list[dict] | None = None):
    """Merge the depth and velocity readings into one row per discharge and section."""
    import pandas as pd

    table = pd.DataFrame(depths)
    if velocities:
        keep = ["discharge_label", "section", "u_max", "u_std", "u3d_max",
                "u_section_mean"]
        vel = pd.DataFrame(velocities)
        vel = vel[[c for c in keep if c in vel.columns]]
        table = table.merge(vel, on=["discharge_label", "section"], how="outer")
    return table.sort_values(["discharge", "section"]).reset_index(drop=True)


def compare(reference, modelled, *, length_scale: float = 1.0,
            velocity_scale: float = 1.0):
    """Put measured section aggregates next to modelled ones.

    *modelled* is the output of
    :func:`~axqua.solvers.telemac.sections.section_statistics` for the same sections,
    carrying a ``name`` that matches the reference ``section``. The scales are recorded
    in the result so a table can never be read without knowing which convention the
    measurements were in.
    """
    import pandas as pd

    model = pd.DataFrame(modelled).rename(columns={"name": "section"})
    merged = reference.merge(model, on="section", how="left", suffixes=("", "_model"))
    if "mean_depth" in merged:
        merged["h_error"] = merged["mean_depth"] - merged["h_mean"]
        merged["h_relative"] = merged["h_error"] / merged["h_mean"].replace(0.0, float("nan"))
    if "max_velocity" in merged and "u_max" in merged:
        merged["u_error"] = merged["max_velocity"] - merged["u_max"]
    merged.attrs["length_scale"] = length_scale
    merged.attrs["velocity_scale"] = velocity_scale
    return merged


def write_comparison(table, path: Path) -> Path:
    """Write a comparison table, with the scale convention in the header comment."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as handle:
        handle.write(f"# length scale {table.attrs.get('length_scale', 1.0)}, "
                     f"velocity scale {table.attrs.get('velocity_scale', 1.0)} "
                     f"(measurements -> model units)\n")
        table.to_csv(handle, index=False)
    return path


# --------------------------------------------------------------------------- #
# sheet plumbing
# --------------------------------------------------------------------------- #
def _to_cumecs(value: str, unit: str) -> float:
    number = float(str(value).replace(",", "."))
    return number / 1000.0 if unit.lower().startswith("l") else number


def _is_number(value) -> bool:
    try:
        return not math.isnan(float(value))
    except (TypeError, ValueError):
        return False


def _section_labels(row) -> dict[int, tuple[str, str | None]]:
    """Column -> (section, station) from a block title row."""
    out = {}
    for column in range(1, len(row)):
        match = _SECTION.match(str(row.iat[column]))
        if match:
            out[column] = (match.group("xs").replace(" ", "").upper(),
                           match.group("us"))
    return out


def _labelled_row(frame, start: int, keyword: str):
    """The row within a block whose first cell mentions *keyword*."""
    for index in range(start + 1, min(start + 5, frame.shape[0])):
        cell = str(frame.iat[index, 0]).lower()
        if keyword in cell:
            return frame.iloc[index]
        if _BLOCK.match(str(frame.iat[index, 0])):
            break
    return None


def _velocity_header(frame) -> int:
    for index in range(frame.shape[0]):
        if str(frame.iat[index, 0]).strip().lower() == "discharge":
            return index
    raise ValueError("no 'Discharge' header row in the velocity sheet")


def _velocity_columns(header) -> dict[str, int]:
    """Map the sheet's column titles onto the names this module uses.

    Case is **load-bearing** here and the matching is case-sensitive because of it: the
    published sheet carries both ``u time avg. max``, the streamwise *component*, and
    ``U time avg. max``, the resultant horizontal speed, and they differ only by that
    capital. Matching case-insensitively silently compares a model's speed against a
    measured component - a difference of a fifth on the sections where the flow turns.
    """
    wanted = {
        "section": ("XS",),
        # the resultant horizontal speed, which is what a modelled |U| compares with
        "u_max": ("U time avg. max",),
        "u_std": ("U std",),
        "u3d_max": ("U3d time-avg. max", "U3d time avg. max"),
        "u3d_std": ("U3d std",),
        "u_section_mean": ("u mean",),
        # the components, kept for reference
        "u_component_max": ("u time avg. max",),
        "v_component_max": ("v time avg. max",),
        "w_component_max": ("w time avg. max",),
    }
    titles = {index: str(header.iat[index]).strip() for index in range(len(header))}
    out: dict[str, int] = {}
    for key, options in wanted.items():
        for index, title in titles.items():
            if title in options:
                out[key] = index
                break
    if "section" not in out:
        raise ValueError("the velocity sheet has no 'XS' column to key the sections on")
    if "u_max" not in out:
        raise ValueError(
            "the velocity sheet has no resultant 'U time avg. max' column; only the "
            "components are present, and comparing one of those against a modelled "
            "speed would be wrong")
    return out
