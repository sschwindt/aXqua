"""What a case can calibrate besides the roughness of its zones, for the calibration parameters of the plugin.

The calibration targets are the measured quantities the model is compared with (water depth, velocity); the calibration parameters are what the calibration varies to match them. Three kinds of parameter, declared in three places:

* **the roughness zones**, declared in the roughness table of the case (``geodata.roughness_table``: a range and the column ``calibration``); listed here, changed in the table;
* **the outflow level**, through the energy slope at the outlet (``calibration.outflow_slope``) or an offset of the level (``calibration.outflow_offset``), one of the two (:mod:`axqua.solvers.telemac.outflow_offset`); the answer says which of the two the case can use and why not, and gives the outflow level each would set at the discharge of every calibration situation, so that a range can be chosen by the levels it means;
* **further parameters** out of :data:`axqua.targets.PARAMETER_CATALOG` that fit the case (the keywords of its TELEMAC module, the conductivity of a gain-lose reach, the GAIA coefficients of a case with sediment transport), written as ``calibration.parameters``.

The user's order of 2026-11-20 ("a wizard for defining additional calibration targets like the energy slope calibration at the outlet"): before it, the energy slope could be entered only in the case file. Nothing of the case is written here; the plugin merges the choice into the case file (``case read`` / ``case write``).
"""

from __future__ import annotations

import logging
import math

log = logging.getLogger("axqua")

__all__ = ["SLOPE_FLOOR", "SUGGESTED_OFFSET", "choices", "discharges", "further",
           "outflow", "preview", "zones"]

#: The smallest energy slope the wizard suggests [-]: a level held up from downstream
#: (Inn KB15: 3.8e-5 against a bed slope of 2.0e-3).
SLOPE_FLOOR = 1.0e-5
#: The range of an offset of the outflow level the wizard suggests [m].
SUGGESTED_OFFSET = (-0.5, 1.5)
#: Catalog entries a single number cannot set (two values in the steering file).
TWO_VALUES = frozenset({"NON-DIMENSIONAL DISPERSION COEFFICIENTS"})
#: The catalog module of each calibration solver.
MODULES = {"Telemac2d": "telemac2d", "Telemac3d": "telemac3d"}


def zones(cfg) -> list[dict]:
    """The roughness zones of the roughness table: range, nominal ks and whether they are calibrated."""
    from axqua.core.geodata import read_roughness_zones

    table = cfg.geodata.roughness_table
    if table is None:
        return []
    try:
        rows = read_roughness_zones(table)
    except (OSError, ValueError) as exc:
        log.warning("the roughness table could not be read: %s", exc)
        return []
    out = []
    for zid, zone in sorted(rows.items()):
        ranged = (zone.ks_min is not None and zone.ks_max is not None
                  and zone.ks_max > zone.ks_min)
        out.append({"zone": int(zid), "name": f"zone{zid}", "ks": float(zone.ks),
                    "ks_min": zone.ks_min, "ks_max": zone.ks_max,
                    "calibrated": bool(ranged and zone.calibrate is not False)})
    return out


def discharges(cfg) -> list[dict]:
    """``[{name, discharge}]``: the discharge of every calibration situation, else the case's own."""
    out = []
    for situation in cfg.calibration.situations:
        total = situation.total_flowrate
        if total is not None and total > 0:
            out.append({"name": situation.name, "discharge": float(total)})
    if not out and cfg.boundaries.prescribed_flowrate:
        out.append({"name": "case discharge",
                    "discharge": float(cfg.boundaries.prescribed_flowrate)})
    return out


def _section(cfg):
    """The outflow section of the case, or ``(None, reason)``."""
    from axqua.solvers.telemac import outflow_offset

    try:
        return outflow_offset.section_of(cfg), ""
    except Exception as exc:                       # noqa: BLE001 - a reason for the form
        return None, f"the outflow section could not be read: {exc}"


def outflow(cfg) -> dict:
    """How the case can calibrate its outflow level.

    ``{current: {kind, range}, bed_slope, slope: {available, reason, remedy, suggested},
    offset: {...}}``. The energy slope is offered where the outflow level comes from the
    rating of the outflow section (:func:`axqua.solvers.telemac.outflow_offset.problem`,
    and :func:`~axqua.solvers.telemac.outflow_offset.check_rating` for a rating curve of
    the case's own); its suggested range runs from :data:`SLOPE_FLOOR` up to the bed
    slope of the reach, the slope the rating uses.
    """
    from axqua.core.errors import ConfigError
    from axqua.solvers.telemac import outflow_offset

    try:
        which = outflow_offset.kind(cfg)
    except ConfigError:
        which = None                                # both set: the form shows neither
    given = {"slope": cfg.calibration.outflow_slope, "offset": cfg.calibration.outflow_offset}
    answer: dict = {"current": {"kind": which,
                                "range": list(given[which]) if which else None},
                    "bed_slope": None}
    for option in ("slope", "offset"):
        found = outflow_offset.problem(cfg, option)
        entry = {"available": found is None, "reason": found[0] if found else "",
                 "remedy": found[1] if found else ""}
        answer[option] = entry
    answer["offset"]["suggested"] = list(SUGGESTED_OFFSET)
    upper = 2.0e-3
    if answer["slope"]["available"]:
        section, reason = _section(cfg)
        if section is None:
            answer["slope"].update(available=False, reason=reason,
                                   remedy="Check the outflow line, the terrain model and "
                                          "the roughness zones of the case.")
        else:
            try:
                outflow_offset.check_rating(cfg, section)
            except ConfigError as exc:
                answer["slope"].update(available=False, reason=exc.message,
                                       remedy=exc.remedy or "")
            if section.slope > 0:
                answer["bed_slope"] = float(section.slope)
                upper = float(section.slope)
    answer["slope"]["suggested"] = [SLOPE_FLOOR, max(upper, 10 * SLOPE_FLOOR)]
    return answer


def preview(cfg, which: str, low: float, high: float) -> list[dict]:
    """The outflow level a range of *which* (``"slope"`` or ``"offset"``) sets at the discharge of every calibration situation.

    ``[{name, discharge, level, low, high}]``: ``level`` is the level of the rating at the
    bed slope [m], ``low``/``high`` the level at the two ends of the range. A smaller
    energy slope holds the water higher, so for the slope ``high`` is the level at
    *low*. An offset moves every level by the same height.
    """
    from axqua.core.errors import ConfigError

    if which not in ("slope", "offset"):
        raise ConfigError(f"no preview for {which!r}", subject="calibration",
                          remedy="Ask for the slope or the offset.")
    if not (math.isfinite(low) and math.isfinite(high) and high > low):
        raise ConfigError(f"[{low}, {high}] is not a range", subject=f"calibration.outflow_{which}",
                          remedy="Enter the smaller value first.")
    if which == "slope" and low <= 0:
        raise ConfigError("an energy slope is larger than 0",
                          subject="calibration.outflow_slope",
                          remedy="Enter a range such as 1.0e-5 to 2.0e-3.")
    section, reason = _section(cfg)
    if section is None:
        raise ConfigError(reason, subject=f"calibration.outflow_{which}",
                          remedy="Check the outflow line, the terrain model and the "
                                 "roughness zones of the case.")
    out = []
    for row in discharges(cfg):
        q = row["discharge"]
        level = section.stage(q)
        if which == "slope":
            at_low, at_high = section.stage(q, high), section.stage(q, low)
        else:
            at_low, at_high = level + low, level + high
        out.append({**row, "level": round(float(level), 3), "low": round(float(at_low), 3),
                    "high": round(float(at_high), 3)})
    return out


def further(cfg) -> list[dict]:
    """The catalog parameters that fit the case, and the ones it already calibrates.

    ``[{name, label, module, min, max, tips, selected, range}]``: the keywords of the
    TELEMAC module of the calibration, ``f.HMP_KF`` where the case has a gain-lose reach,
    and the GAIA coefficients that need no class or layer number where it has sediment
    transport. An entry of ``calibration.parameters`` that is not in the catalog is
    listed too (``module: "case"``), so that a form keeps it.
    """
    from axqua.targets import PARAMETER_CATALOG

    module = MODULES.get(cfg.calibration.solver_name, "telemac2d")
    wanted = {module}
    if cfg.gain_lose.active and cfg.gain_lose.implementation == "fortran":
        wanted.add("gainlose")
    if getattr(cfg.morphodynamics, "enabled", False):
        wanted.add("gaia")
    given = {str(p.name).strip(): p for p in cfg.calibration.parameters}
    out, seen = [], set()
    for spec in PARAMETER_CATALOG:
        if spec.module not in wanted or spec.per_class or spec.keyword in TWO_VALUES:
            continue
        name = spec.hbc_name(None)
        mine = given.get(name)
        out.append({"name": name, "label": spec.label, "module": spec.module,
                    "min": spec.vmin, "max": spec.vmax, "tips": spec.tips,
                    "selected": mine is not None,
                    "range": [mine.min, mine.max] if mine is not None else None})
        seen.add(name)
    for name, mine in given.items():
        if name in seen or name.lower().startswith("zone"):
            continue
        out.append({"name": name, "label": name, "module": "case", "min": mine.min,
                    "max": mine.max, "tips": mine.comment or "", "selected": True,
                    "range": [mine.min, mine.max]})
    return out


def choices(cfg, *, slope: tuple[float, float] | None = None,
            offset: tuple[float, float] | None = None) -> dict:
    """Everything the calibration parameters of the plugin show, and the outflow levels a range of the slope or the offset sets."""
    table = cfg.geodata.roughness_table
    answer = {"zones": zones(cfg), "roughness_table": str(table) if table else "",
              "outflow": outflow(cfg), "further": further(cfg),
              "discharges": discharges(cfg)}
    for which, given in (("slope", slope), ("offset", offset)):
        if given is not None:
            answer.setdefault("preview", {})[which] = preview(cfg, which, *given)
    return answer
