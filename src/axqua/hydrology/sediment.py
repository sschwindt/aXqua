"""Sediment transport in the hydrology mode: which GAIA setup suits the soil and the slope.

The runoff of a catchment carries soil. How much, and how much of it reaches the outlet or a river, depends on what the soil is made of and how steep the terrain is. This module turns a description of the soil into the settings of the block ``morphodynamics`` (GAIA, coupled into the hydrology run) and says why. It is what the *Sediment transport wizard* of the plugin asks, through ``axqua hydrology sediment``.

The soil is described by one of: a USDA texture class (the one of the case, ``hydrology.soil_texture``, or another), *clay*, *sand*, the shares of sand, silt and clay, or a median grain size d50. A texture class stands for the shares at the middle of its field in the USDA texture triangle (:data:`TEXTURE_SHARES`). The rules (:func:`recommend`):

* **Sand** (sand at least 70 %, or a d50 from 0.062 to 2 mm): one non-cohesive class, bed load after van Rijn (1984a, GAIA formula 7) and, for a d50 up to 0.5 mm, suspension with the equilibrium concentration of van Rijn (1984b, formula 3). These are the formulas for the sand sizes of soils: van Rijn derived the bed-load formula for 0.2 to 2 mm and the suspended load for 0.1 to 0.5 mm.
* **Clay** (clay at least 40 %, or a d50 below 0.062 mm): one cohesive class in suspension, eroded after Partheniades (1965) above a critical shear stress and deposited after Krone (1962), with GAIA's default parameters, which a soil needs calibrated.
* **Loams and silts** (everything between): two classes, the sand share non-cohesive with van Rijn's bed load and suspension, the silt and clay share cohesive.
* **Gravel** (d50 above 2 mm): Meyer-Peter and Müller (1948, formula 1) without suspension, because van Rijn's formulas end at 2 mm.

On terrain steeper than 2 % (the median slope of the catchment) the slope effect of Koch and Flokstra (1981) is switched on, which turns bed load downhill; above 20 % the formulas, derived for rivers, are far outside their range and the answer says so. The soil that can erode is one layer of ``layer_thickness`` (0.3 m, the plough layer of a field, by default) above a bed that does not erode.

**A river through the catchment** (an inflow line with a discharge) narrows what TELEMAC v9.1.1 can run, measured on the test catchment of aXqua with the finite-volume kernel of the hydrology mode (2026-11-01) and stated by :func:`river_conflicts`: clear water enters over a bed that the flow erodes at its full capacity, and the finite-volume kernel applies GAIA's erosion and deposition explicitly (``init_trac.f``) with a deposition rate of the settling velocity times the Rouse ratio of near-bed to mean concentration (``prep_advection_gaia.f``, ``suspension_rouse.f``), which grows with the grain size. Suspended sand coarser than 0.15 mm then drives the concentration beyond 1000 g/l within the first steps unless GAIA's ``EQUILIBRIUM INFLOW CONCENTRATION`` brings the river in at equilibrium; together with a cohesive class that option does not help either (0.4 mm sand with clay stops with it), and it puts tens of g/l of the cohesive class into the river. Sand coarser than 0.5 mm stops also with it. Bed load without any suspended class ends in a crash of GAIA (``free()`` in ``gaia_prepare_step``) for every grain size. Without a river every one of these soils runs, so the rules apply only with one; neither the number of bed layers nor the mixture of clay and sand as such is the cause (GAIA's manual allows any combination of classes).

The values of the cohesive soil (critical shear stress 0.5 N/m2, Partheniades constant 0.001 kg/m2/s, GAIA's defaults, and a dry bulk density of 1300 kg/m3, typical of arable topsoil) are starting values and not properties of a particular soil: measured erodibility of soils spans orders of magnitude.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

#: The shares of sand, silt and clay [%] at the middle of each USDA texture class, read off the texture triangle (rounded): what a texture class stands for when nothing more is known.
TEXTURE_SHARES: dict[str, tuple[float, float, float]] = {
    "sand": (92.0, 5.0, 3.0),
    "loamy sand": (82.0, 12.0, 6.0),
    "sandy loam": (65.0, 25.0, 10.0),
    "loam": (40.0, 40.0, 20.0),
    "silt loam": (20.0, 65.0, 15.0),
    "silt": (7.0, 87.0, 6.0),
    "sandy clay loam": (60.0, 13.0, 27.0),
    "clay loam": (33.0, 34.0, 33.0),
    "silty clay loam": (10.0, 56.0, 34.0),
    "sandy clay": (52.0, 6.0, 42.0),
    "silty clay": (7.0, 47.0, 46.0),
    "clay": (20.0, 20.0, 60.0),
}

#: The grain sizes [m] of the classes a recommendation sets up.
SAND_D50 = 0.00025            # a sand soil: fine to medium sand
SAND_SHARE_D50 = 0.00015      # the sand of a loam: mostly fine sand
FINES_SILT = 0.00002          # silt and clay, silt the larger part: coarse flocs
FINES_CLAY = 0.000005         # silt and clay, clay the larger part

#: Where the rules change.
SAND_SHARE = 70.0             # % sand above which a soil is a sand
CLAY_SHARE = 40.0             # % clay above which a soil is a clay
MUD_D50 = 0.000062            # m, the upper limit of silt (Wentworth)
GRAVEL_D50 = 0.002            # m, where sand ends and van Rijn's formulas too
SUSPENSION_D50 = 0.0005       # m, the upper end of van Rijn's suspended load
SLOPE_EFFECT = 0.02           # median slope above which bed load is turned downhill
STEEP = 0.20                  # median slope above which the formulas are far outside

#: Starting values of a cohesive soil.
MUD_EROSION_SHEAR = 0.5       # N/m2, GAIA's default
PARTHENIADES = 0.001          # kg/m2/s, GAIA's default
MUD_CONCENTRATION = 1300.0    # g/l, dry bulk density of arable topsoil
LAYER_THICKNESS = 0.3         # m, the plough layer of a field

#: GAIA's numbers of the formulas.
VAN_RIJN_BEDLOAD = 7
VAN_RIJN_SUSPENSION = 3
MEYER_PETER_MUELLER = 1

#: The suspended sand that ran at a river inflow (:func:`river_conflicts`): with a cohesive class or without GAIA's equilibrium inflow, and with it.
RIVER_SAND_D50 = 0.00015      # m, 0.1 and 0.15 mm ran, 0.25 and 0.4 mm stopped
RIVER_SAND_EQUILIBRIUM_D50 = 0.0005   # m, 0.25 to 0.5 mm ran, 1 mm stopped

SOIL_KINDS = ("texture", "clay", "sand", "shares", "grain")


@dataclass
class Recommendation:
    """What :func:`recommend` answers."""

    model: str                        # sand | clay | mixed | gravel
    title: str
    settings: dict[str, Any]
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    shares: tuple[float, float, float] | None = None
    d50: float | None = None
    slope: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"model": self.model, "title": self.title, "settings": self.settings,
                "reasons": self.reasons, "warnings": self.warnings,
                "shares": (None if self.shares is None else
                           dict(zip(("sand", "silt", "clay"), self.shares))),
                "d50_mm": None if self.d50 is None else self.d50 * 1000.0,
                "slope": self.slope}


def texture_shares(name: str) -> tuple[float, float, float]:
    """The shares of sand, silt and clay of a texture class."""
    key = " ".join(str(name).lower().replace("_", " ").replace("-", " ").split())
    if key not in TEXTURE_SHARES:
        raise ValueError(f"unknown texture class {name!r}; one of "
                         + ", ".join(TEXTURE_SHARES))
    return TEXTURE_SHARES[key]


def catchment_slope(cfg) -> float | None:
    """The median slope [-] of the terrain inside the outline, or None without one. Each ``axqua hydrology sediment`` is a process of its own, so the plugin passes the slope of its first answer (``--slope``) instead of having it read again."""
    import numpy as np

    from axqua.hydrology.terrain import load_grid

    try:
        grid = load_grid(cfg, max_cells=200_000)
    except Exception:  # noqa: BLE001 - no terrain model yet: the slope is not known
        return None
    gy, gx = np.gradient(grid.z, grid.cell)
    slope = np.hypot(gx, gy)[grid.mask]
    slope = slope[np.isfinite(slope)]
    return float(np.median(slope)) if slope.size else None


def recommend(*, soil: str = "texture", texture: str | None = None,
              shares: tuple[float, float, float] | None = None,
              d50: float | None = None, layer_thickness: float = LAYER_THICKNESS,
              slope: float | None = None) -> Recommendation:
    """The GAIA setup for a soil and a slope; see the module docstring for the rules.

    *soil* is one of :data:`SOIL_KINDS`: ``texture`` (with *texture*), ``clay``, ``sand``, ``shares`` (with *shares*, sand/silt/clay in %) or ``grain`` (with *d50* in m). *slope* is the median slope [-] of the catchment, or None.
    """
    if soil not in SOIL_KINDS:
        raise ValueError(f"the soil is one of {', '.join(SOIL_KINDS)}, not {soil!r}")
    if layer_thickness is None or not layer_thickness > 0:
        raise ValueError("the layer of soil that can erode needs a thickness above zero")
    described = soil
    if soil == "texture":
        if not texture:
            raise ValueError("a texture class is needed")
        shares = texture_shares(texture)
        described = f"texture {texture}"
    elif soil == "clay":
        shares, described = TEXTURE_SHARES["clay"], "clay"
    elif soil == "sand":
        shares, described = TEXTURE_SHARES["sand"], "sand"
    elif soil == "shares":
        if shares is None or len(shares) != 3:
            raise ValueError("the shares of sand, silt and clay are needed")
        total = float(sum(shares))
        if not total > 0 or any(v < 0 for v in shares):
            raise ValueError("the shares of sand, silt and clay must be positive")
        shares = tuple(100.0 * float(v) / total for v in shares)
        described = "sand {:.0f} %, silt {:.0f} %, clay {:.0f} %".format(*shares)
    elif soil == "grain":
        if d50 is None or not d50 > 0:
            raise ValueError("a median grain size d50 above zero is needed")
        described = f"d50 {1000.0 * d50:g} mm"

    reasons: list[str] = []
    warnings: list[str] = []
    settings: dict[str, Any] = {
        "morphodynamics.enabled": True,
        "morphodynamics.soil": described,
        "morphodynamics.layer_thickness": float(layer_thickness),
        "morphodynamics.bed_layers": 1,
        "morphodynamics.morphological_factor": 1.0,
        "morphodynamics.prescribed_solid_discharges": None,
        # every key a recommendation controls is named, None where it does not apply, so that saving a sand after a loam removes the cohesive settings of the loam
        "morphodynamics.mud_erosion_shear": None,
        "morphodynamics.partheniades": None,
        "morphodynamics.mud_concentration": None,
    }

    if soil == "grain":
        model = ("gravel" if d50 > GRAVEL_D50 else "clay" if d50 < MUD_D50 else "sand")
        grain = float(d50)
    else:
        sand, silt, clay = shares
        model = ("sand" if sand >= SAND_SHARE else
                 "clay" if clay >= CLAY_SHARE else "mixed")
        grain = SAND_D50
    sand_reason = ("van Rijn (1984a) derived the bed-load formula for grains of 0.2 to "
                   "2 mm and van Rijn (1984b) the suspended load for 0.1 to 0.5 mm, the "
                   "sand of soils")
    mud = {"morphodynamics.mud_erosion_shear": MUD_EROSION_SHEAR,
           "morphodynamics.partheniades": PARTHENIADES,
           "morphodynamics.mud_concentration": MUD_CONCENTRATION}

    if model == "gravel":
        title = "Bed load after Meyer-Peter and Müller"
        settings.update({
            "morphodynamics.sediment_type": "NCO",
            "morphodynamics.sediment_classes": [{"diameter": grain, "density": 2650,
                                                 "type": "NCO", "fraction": 1.0}],
            "morphodynamics.bedload": True,
            "morphodynamics.bedload_formula": MEYER_PETER_MUELLER,
            "morphodynamics.suspended_load": False,
            "morphodynamics.suspension_formula": None,
            "morphodynamics.equilibrium_inflow": False,
        })
        reasons.append(f"A d50 of {1000 * grain:g} mm is gravel: it moves as bed load "
                       "and does not go into suspension, and van Rijn's formulas end at "
                       "2 mm, so Meyer-Peter and Müller (1948) is used.")
    elif model == "sand":
        suspended = grain <= SUSPENSION_D50
        title = ("Bed load and suspension after van Rijn" if suspended
                 else "Bed load after van Rijn")
        settings.update({
            "morphodynamics.sediment_type": "NCO",
            "morphodynamics.sediment_classes": [{"diameter": grain, "density": 2650,
                                                 "type": "NCO", "fraction": 1.0}],
            "morphodynamics.bedload": True,
            "morphodynamics.bedload_formula": VAN_RIJN_BEDLOAD,
            "morphodynamics.suspended_load": suspended,
            "morphodynamics.suspension_formula": VAN_RIJN_SUSPENSION if suspended else None,
            "morphodynamics.equilibrium_inflow": suspended,
        })
        reasons.append(f"The soil is a sand ({described}), one non-cohesive class of "
                       f"{1000 * grain:g} mm; " + sand_reason + ".")
        if grain < 0.0002:
            warnings.append(f"A d50 of {1000 * grain:g} mm is below the 0.2 mm of van "
                            "Rijn's bed-load formula; most of it will go into suspension.")
        if not suspended:
            reasons.append("Grains coarser than 0.5 mm hardly go into suspension, so "
                           "suspension is off.")
    elif model == "clay":
        fine = grain if soil == "grain" else (
            FINES_CLAY if shares[2] >= shares[1] else FINES_SILT)
        title = "Cohesive soil in suspension after Partheniades and Krone"
        settings.update({
            "morphodynamics.sediment_type": "CO",
            "morphodynamics.sediment_classes": [{"diameter": fine, "density": 2650,
                                                 "type": "CO", "fraction": 1.0}],
            "morphodynamics.bedload": False,
            "morphodynamics.suspended_load": True,
            "morphodynamics.suspension_formula": None,
            "morphodynamics.equilibrium_inflow": False,
            **mud,
        })
        reasons.append(f"The soil is cohesive ({described}): clay and silt erode as "
                       "aggregates when the shear stress exceeds a critical value "
                       "(Partheniades 1965), travel in suspension and settle where the "
                       "flow slows (Krone 1962). One cohesive class of "
                       f"{1000 * fine:g} mm, whose settling velocity GAIA computes.")
        warnings.append("The erosion parameters of a cohesive soil (critical shear "
                        f"stress {MUD_EROSION_SHEAR} N/m², Partheniades constant "
                        f"{PARTHENIADES} kg/m²/s) are GAIA's defaults, starting values "
                        "that a soil needs calibrated.")
    else:
        sand, silt, clay = shares
        fine = FINES_CLAY if clay >= silt else FINES_SILT
        title = "Sand after van Rijn with cohesive fines"
        settings.update({
            "morphodynamics.sediment_type": "NCO",
            "morphodynamics.sediment_classes": [
                {"diameter": fine, "density": 2650, "type": "CO",
                 "fraction": round((silt + clay) / 100.0, 4)},
                {"diameter": SAND_SHARE_D50, "density": 2650, "type": "NCO",
                 "fraction": round(sand / 100.0, 4)}],
            "morphodynamics.bedload": True,
            "morphodynamics.bedload_formula": VAN_RIJN_BEDLOAD,
            "morphodynamics.suspended_load": True,
            "morphodynamics.suspension_formula": VAN_RIJN_SUSPENSION,
            # GAIA's equilibrium inflow puts tens of g/l of the cohesive class into a river (measured on the test catchment): the river brings clear water
            "morphodynamics.equilibrium_inflow": False,
            **mud,
        })
        reasons.append(f"The soil is a mixture ({described}): its sand ({sand:.0f} %, "
                       f"{1000 * SAND_SHARE_D50:g} mm) is non-cohesive, its silt and clay "
                       f"({silt + clay:.0f} %, {1000 * fine:g} mm) cohesive. The sand "
                       "moves as bed load and in suspension after van Rijn, the fines in "
                       "suspension after Partheniades and Krone; " + sand_reason + ".")
        warnings.append("The erosion parameters of the cohesive share are GAIA's "
                        "defaults, starting values that a soil needs calibrated.")

    if slope is not None and math.isfinite(slope):
        settings["morphodynamics.slope_effect"] = slope > SLOPE_EFFECT and \
            bool(settings.get("morphodynamics.bedload"))
        if settings["morphodynamics.slope_effect"]:
            reasons.append(f"The median slope of the catchment is {100 * slope:.1f} %, "
                           "above 2 %: the slope effect of Koch and Flokstra (1981) turns "
                           "the bed load downhill.")
        if slope > STEEP:
            warnings.append(f"The median slope of the catchment is {100 * slope:.0f} %. "
                            "The transport formulas were derived for rivers and are far "
                            "outside their range on such slopes; read the result as an "
                            "order of magnitude.")
    else:
        settings["morphodynamics.slope_effect"] = bool(settings.get("morphodynamics.bedload"))
    reasons.append(f"The soil that can erode is {layer_thickness:g} m thick, above a bed "
                   "that does not erode.")
    return Recommendation(model=model, title=title, settings=settings, reasons=reasons,
                          warnings=warnings, shares=shares if soil != "grain" else None,
                          d50=grain if soil == "grain" else None, slope=slope)


def river_conflicts(m) -> list[tuple[str, str]]:
    """What of the sediment setup *m* (a ``Morphodynamics``) TELEMAC v9.1.1 cannot run when a river flows into the model, as ``(message, remedy)``; empty when it can. The measurements behind the rules are in the module docstring."""
    if not getattr(m, "enabled", False):
        return []
    classes = m.classes
    kinds = [m.class_type(c) for c in classes]
    cohesive = "CO" in kinds
    sands = [float(c.get("diameter", 0.0) or 0.0) for c, k in zip(classes, kinds)
             if k == "NCO"] if m.suspended_load else []
    out = []
    if m.bedload and m.suspended_classes == 0:
        out.append((
            "Bed load without any class in suspension stops GAIA at a river inflow: "
            "TELEMAC v9.1.1 crashes in its first step (gaia_prepare_step).",
            "For sand of {1000 * RIVER_SAND_EQUILIBRIUM_D50:g} mm or less, put it into "
            "suspension too (suspended_load, with equilibrium_inflow); for coarser sand "
            "and gravel, leave the river line out of the liquid boundaries or switch "
            "sediment transport off."))
    coarse = [d for d in sands if d > RIVER_SAND_D50]
    if coarse and cohesive:
        out.append((
            f"Suspended sand of {1000 * max(coarse):g} mm together with a cohesive class "
            "stops TELEMAC at a river inflow: the concentration exceeds 1000 g/l within "
            f"the first steps. With a river, the sand of a soil with clay can be "
            f"{1000 * RIVER_SAND_D50:g} mm at most.",
            f"Give the sand a diameter of {1000 * RIVER_SAND_D50:g} mm or less (the "
            "Sediment transport wizard does for a loam), or describe the soil without "
            "the cohesive class, or leave the river line out."))
    elif coarse and not m.equilibrium_inflow:
        out.append((
            f"Suspended sand of {1000 * max(coarse):g} mm stops TELEMAC at a river inflow "
            "when the river brings clear water: the concentration exceeds 1000 g/l "
            "within the first steps.",
            "Switch equilibrium_inflow on (EQUILIBRIUM INFLOW CONCENTRATION), so that the "
            f"river enters at equilibrium, or use sand of {1000 * RIVER_SAND_D50:g} mm "
            "or less."))
    too_coarse = [d for d in coarse if d > RIVER_SAND_EQUILIBRIUM_D50]
    if too_coarse and not cohesive and m.equilibrium_inflow:
        out.append((
            f"Suspended sand of {1000 * max(too_coarse):g} mm stops TELEMAC at a river "
            "inflow also when the river enters at equilibrium.",
            f"Give the sand in suspension a diameter of {1000 * RIVER_SAND_EQUILIBRIUM_D50:g}"
            " mm or less, or leave the river line out."))
    return out


def has_river(cfg) -> bool:
    """Whether water flows into the model through an inflow line of the liquid boundaries."""
    if cfg.boundaries.liquid_boundaries is None:
        return False
    from axqua.core.boundaries import liquid_lines

    try:
        return "inflow" in liquid_lines(cfg)
    except Exception:  # noqa: BLE001 - the case check names an unreadable layer
        return False


def recommend_for_case(cfg, *, slope: float | None = None, **options) -> Recommendation:
    """:func:`recommend` with the slope of the case's terrain (or *slope*, when the caller knows it already), and its texture class where none is given."""
    if options.get("soil", "texture") == "texture" and not options.get("texture"):
        options["texture"] = getattr(cfg.hydrology, "soil_texture", None) or "loam"
    answer = recommend(slope=catchment_slope(cfg) if slope is None else slope, **options)
    if has_river(cfg):
        from axqua.config import Morphodynamics

        proposed = Morphodynamics(**{key.split(".", 1)[1]: value
                                     for key, value in answer.settings.items()
                                     if value is not None and key.startswith("morphodynamics.")})
        for message, remedy in river_conflicts(proposed):
            answer.warnings.append(f"This case has a river inflow. {message} {remedy}")
    return answer
