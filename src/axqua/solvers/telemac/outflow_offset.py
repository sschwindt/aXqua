"""The outflow level as a calibration parameter (``calibration.outflow_offset``).

The outflow level of a river case comes from the rating of the outflow section
(``outflow_condition: stage_discharge``) or is given (``elevation``). Where the water
level of the reach is held up by a control the terrain model does not resolve, or by
one downstream of the model, that level is too low, and no roughness within its range
can raise the water level at the measurement points (Inn KB15, September 2025: the
model water surface in the main channel 0.45 to 0.65 m below the measured one, with
the bed within 3 cm). ``calibration.outflow_offset`` gives a range ``[min, max]`` in
meters, and the calibration varies the level by that offset, added to the level every
situation gets from the rating at its own discharge. One offset is shared by all
situations, as one roughness is. In the trial (2026-11-16) a level of 375.80 m, 1.31 m
above the rating, brought the main channel to within 1 cm of the measured level; the
water level over the patch upstream did not change.

The offset is raised linearly from 0 over the first ``RAMP`` seconds of a run. A run
starts from the steady result at the level of the rating, and a step of a meter at the
outlet sends a surge up the reach: in the trial the water level rose 1.3 m above its
final value 300 s after a step of 1.6 m. A run that applies the offset therefore lasts
the ramp longer (:func:`axqua.bayescal.calibration_hotstart_duration`), and never less
than ``MIN_DURATION``, the ramp and as long again for the reach to settle
(:func:`with_duration`): a run that ended during the ramp would tie each sampled offset
to the water levels of a smaller one.

HydroBayesCal rewrites ``<model_dir>/user_fortran/user_sl.f`` whatever the case names
(``control_telemac.py``), so the case's user Fortran folder has to be ``user_fortran``
and every steering file of a calibration run has to name it (:func:`check`,
:func:`with_fortran_folder`). The built steering file of the case is never changed:
a calibration that would run it gets a copy (:func:`axqua.bayescal.ready_outflow_offset`).

How it reaches TELEMAC: TELEMAC-2D calls ``USER_SL`` with every prescribed level it
reads from ``PRESCRIBED ELEVATIONS`` (``sl.f``), only for boundaries that prescribe a
level. The generated ``user_sl.f`` adds the variable ``OUTFLOW_OFFSET`` to it. Its value
is assigned on a line of its own, which HydroBayesCal rewrites for the parameter
``f.OUTFLOW_OFFSET`` before every run (the way the gain-lose ``f.HMP_KF`` is
calibrated). The validation copies the folder and writes the calibrated offset into the
copy (:func:`axqua.validation.build`).

**The energy slope of the outflow section** (``calibration.outflow_slope``) is the
other way to calibrate the outflow level, for a case whose level comes from the rating
of its outflow section (``rating_method: section``). That rating is the normal depth of
the section: Keulegan's law with the ks of the roughness zones and the bed slope of the
reach. A level held up from downstream is a backwater, in which the energy slope at the
outlet is far smaller than the bed slope. ``calibration.outflow_slope`` gives a range
``[min, max]`` of that energy slope, and the generated ``user_sl.f`` carries the section
and solves the rating itself, for the discharge of the run (the sum of the ``PRESCRIBED
FLOWRATES``) at the sampled slope: the level rises by the difference between the normal
stage at that slope and at the slope of the reach, over the same ramp as the offset.
One slope is calibrated for all situations, so the calibrated relation is a whole
stage-discharge curve, and a validation at another discharge gets the level of that
curve. HydroBayesCal samples ``log10`` of the slope (parameter ``f.OUTFLOW_LOG_SLOPE``),
because the slope spans orders of magnitude. On Inn KB15 the level of the trial, 1.31 m
above the rating at 54.3 m3/s, needs a ks of 10.7 m in the outflow section (Manning n
0.21, no river is that rough) or an energy slope of 3.9e-5 instead of the bed slope of
2.0e-3 (2026-11-17).

Not available yet: TELEMAC-3D, a level from TELEMAC's own Z(Q) curve
(``normal_depth``) and outflow lines without a level (``free``, ``overflow``).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

#: Fortran variable that holds the offset, and HydroBayesCal's name of the parameter.
VARIABLE = "OUTFLOW_OFFSET"
NAME = f"f.{VARIABLE}"
#: The generated routine, in the case's user Fortran folder.
FILE = "user_sl.f"
#: Outflow conditions whose level TELEMAC reads from PRESCRIBED ELEVATIONS.
LEVEL_KINDS = ("fixed", "rating")
#: Seconds over which the offset rises from 0 to its value.
RAMP = 600.0
#: The shortest run that applies the offset [s]: the ramp, and as long again for the reach to settle at the new level.
MIN_DURATION = 2.0 * RAMP
#: The folder HydroBayesCal rewrites the routine in, in the model folder.
FOLDER = "user_fortran"
#: The first line of the routine aXqua writes, by which it knows its own file.
MARK = "!     aXqua generated: the outflow level as a calibration parameter"
#: Fortran variable of ``log10`` of the energy slope of the outflow section, and its parameter.
SLOPE_VARIABLE = "OUTFLOW_LOG_SLOPE"
SLOPE_NAME = f"f.{SLOPE_VARIABLE}"
#: The parameters and Fortran variables of the outflow level.
NAMES = (NAME, SLOPE_NAME)
VARIABLES = (VARIABLE, SLOPE_VARIABLE)
#: Section values per DATA statement and per line of the routine (fixed form: 72 columns, few continuation lines).
_PER_DATA = 45
_PER_LINE = 3


def _dbl(value: float) -> str:
    text = f"{float(value):.10G}"
    return text.replace("E", "D") if "E" in text else text + "D0"


def source(offset: float = 0.0) -> str:
    """Fixed-form Fortran of ``USER_SL`` that adds *offset* [m] to every prescribed level."""
    lines = [
        MARK,
        "!     Written by aXqua (axqua.solvers.telemac.outflow_offset) for",
        "!     TELEMAC-2D v9. SL (sl.f) calls USER_SL with the level of",
        "!     PRESCRIBED ELEVATIONS for every liquid boundary that prescribes",
        "!     a level; inflows that prescribe a discharge never reach it.",
        "!     OUTFLOW_OFFSET is added to that level, raised linearly from 0",
        "!     over the first OUTFLOW_RAMP seconds, so that the run does not",
        "!     start with a step at the outlet. HydroBayesCal rewrites the line",
        "!     that assigns OUTFLOW_OFFSET for the parameter f.OUTFLOW_OFFSET.",
        "!",
        "      SUBROUTINE USER_SL",
        "     &(I , N, SL)",
        "!",
        "      USE BIEF",
        "      USE DECLARATIONS_SPECIAL",
        "      USE DECLARATIONS_TELEMAC",
        "      USE DECLARATIONS_TELEMAC2D",
        "      USE INTERFACE_TELEMAC2D, EX_USER_SL => USER_SL, EX_SL => SL",
        "!",
        "      IMPLICIT NONE",
        "!",
        "      INTEGER, INTENT(IN) :: I,N",
        "      DOUBLE PRECISION, INTENT(INOUT) :: SL",
        f"      DOUBLE PRECISION {VARIABLE},OUTFLOW_RAMP",
        "!",
        f"!     calibratable: HydroBayesCal rewrites this line for {NAME}",
        f"      {VARIABLE} = {_dbl(offset)}",
        f"      OUTFLOW_RAMP = {_dbl(RAMP)}",
        f"      SL = SL + {VARIABLE}*MIN(1.D0,MAX(0.D0,AT/OUTFLOW_RAMP))",
        "!",
        "      RETURN",
        "      END",
    ]
    return "\n".join(lines) + "\n"


def write(folder: Path, offset: float = 0.0) -> Path:
    """Write ``user_sl.f`` with *offset* into *folder* (created if needed)."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / FILE
    path.write_text(source(offset), encoding="utf-8")
    return path


@dataclass(frozen=True)
class Section:
    """The outflow cross-section of the section rating (:func:`axqua.rating.outflow_section`): stations [m], bed [m] and Nikuradse ks [m], sorted by station, and the bed slope of the reach [-] the rating uses."""

    station: np.ndarray
    bed: np.ndarray
    ks: np.ndarray
    slope: float

    def stage(self, discharge: float, slope: float | None = None) -> float:
        """The normal stage [m] of the section for *discharge* at the energy *slope* (the reach slope by default), as the rating computes it."""
        from axqua.rating import stage_for_discharge

        return stage_for_discharge(discharge, station=self.station, bed=self.bed,
                                   ks=self.ks, slope=self.slope if slope is None else slope)

    def slope_for(self, discharge: float, level: float) -> float:
        """The energy slope at which the normal stage for *discharge* is *level* [m] (bisection in log10 of the slope)."""
        low, high = -12.0, math.log10(0.5)
        for _ in range(100):
            mid = 0.5 * (low + high)
            if self.stage(discharge, 10.0 ** mid) > level:
                low = mid
            else:
                high = mid
        return 10.0 ** (0.5 * (low + high))


def section_of(cfg) -> Section:
    """The outflow section of the case, as its section rating samples it."""
    from axqua.rating import outflow_section

    station, bed, ks, slope = outflow_section(cfg)
    return Section(station=np.asarray(station, float), bed=np.asarray(bed, float),
                   ks=np.asarray(ks, float), slope=float(slope))


def _data(name: str, values) -> list[str]:
    """``DATA`` statements of the array *name*, :data:`_PER_DATA` values each, in 72 columns."""
    values = [_dbl(v) for v in values]
    lines = []
    for first in range(0, len(values), _PER_DATA):
        chunk = values[first:first + _PER_DATA]
        lines.append(f"      DATA {name}({first + 1}:{first + len(chunk)}) /")
        rows = [chunk[i:i + _PER_LINE] for i in range(0, len(chunk), _PER_LINE)]
        for j, row in enumerate(rows):
            end = " /" if j == len(rows) - 1 else ","
            lines.append("     &  " + ", ".join(row) + end)
    return lines


def slope_source(section: Section, log_slope: float | None = None) -> str:
    """Fixed-form Fortran of ``USER_SL`` that raises every prescribed level by the rise of the normal stage of *section* when its energy slope falls from the reach slope to ``10**OUTFLOW_LOG_SLOPE``.

    The routine integrates the section exactly as :func:`axqua.rating.stage_for_discharge` does (wetted area, perimeter along the bed, mean ks of the wet stations, Keulegan, 80 bisection steps), for the discharge of the run, the sum of the positive ``PRESCRIBED FLOWRATES``. *log_slope* defaults to ``log10`` of the reach slope, at which the level stays that of the rating.
    """
    n = len(section.bed)
    if log_slope is None:
        log_slope = math.log10(section.slope)
    ds = np.gradient(section.station)
    seg = np.hypot(np.diff(section.station), np.diff(section.bed))
    lines = [
        MARK,
        "!     Written by aXqua (axqua.solvers.telemac.outflow_offset) for",
        "!     TELEMAC-2D v9. SL (sl.f) calls USER_SL with the level of",
        "!     PRESCRIBED ELEVATIONS for every liquid boundary that prescribes",
        "!     a level; inflows that prescribe a discharge never reach it.",
        "!     The routine carries the outflow section of the rating (bed AXQ_ZB,",
        "!     widths AXQ_DS, bed segments AXQ_SG, Nikuradse AXQ_KS) and solves",
        "!     its normal stage for the discharge of the run, the sum of the",
        "!     positive PRESCRIBED FLOWRATES (Keulegan, as axqua.rating does).",
        "!     The level rises by the stage at the energy slope",
        f"!     10**{SLOPE_VARIABLE} minus the stage at the reach slope",
        "!     AXQ_S0, raised linearly from 0 over the first OUTFLOW_RAMP",
        "!     seconds. HydroBayesCal rewrites the line that assigns the",
        f"!     log10 of the slope for the parameter {SLOPE_NAME}.",
        "!",
        "      SUBROUTINE USER_SL",
        "     &(I , N, SL)",
        "!",
        "      USE BIEF",
        "      USE DECLARATIONS_SPECIAL",
        "      USE DECLARATIONS_TELEMAC",
        "      USE DECLARATIONS_TELEMAC2D",
        "      USE INTERFACE_TELEMAC2D, EX_USER_SL => USER_SL, EX_SL => SL",
        "!",
        "      IMPLICIT NONE",
        "!",
        "      INTEGER, INTENT(IN) :: I,N",
        "      DOUBLE PRECISION, INTENT(INOUT) :: SL",
        "!",
        f"      INTEGER, PARAMETER :: AXQ_NS = {n}",
        f"      DOUBLE PRECISION, PARAMETER :: AXQ_S0 = {_dbl(section.slope)}",
        "      DOUBLE PRECISION, SAVE :: AXQ_ZB(AXQ_NS), AXQ_DS(AXQ_NS)",
        "      DOUBLE PRECISION, SAVE :: AXQ_KS(AXQ_NS), AXQ_SG(AXQ_NS-1)",
        "      DOUBLE PRECISION, SAVE :: AXQ_QDONE = -1.D0",
        "      DOUBLE PRECISION, SAVE :: AXQ_SDONE = -1.D0",
        "      DOUBLE PRECISION, SAVE :: AXQ_SHIFT = 0.D0",
        f"      DOUBLE PRECISION {SLOPE_VARIABLE},OUTFLOW_RAMP",
        "      DOUBLE PRECISION AXQ_Q,AXQ_S",
        "      INTEGER AXQ_K",
        "!",
        *_data("AXQ_ZB", section.bed),
        *_data("AXQ_DS", ds),
        *_data("AXQ_KS", section.ks),
        *_data("AXQ_SG", seg),
        "!",
        f"!     calibratable: HydroBayesCal rewrites this line for {SLOPE_NAME}",
        f"      {SLOPE_VARIABLE} = {_dbl(log_slope)}",
        f"      OUTFLOW_RAMP = {_dbl(RAMP)}",
        f"      AXQ_S = 10.D0**{SLOPE_VARIABLE}",
        "      AXQ_Q = 0.D0",
        "      DO AXQ_K = 1, NDEBIT",
        "        AXQ_Q = AXQ_Q + MAX(DEBIT(AXQ_K), 0.D0)",
        "      ENDDO",
        "      IF(AXQ_Q.NE.AXQ_QDONE.OR.AXQ_S.NE.AXQ_SDONE) THEN",
        "        AXQ_SHIFT = AXQ_STAGE(AXQ_Q, AXQ_S) - AXQ_STAGE(AXQ_Q, AXQ_S0)",
        "        AXQ_QDONE = AXQ_Q",
        "        AXQ_SDONE = AXQ_S",
        "      ENDIF",
        "      SL = SL + AXQ_SHIFT*MIN(1.D0,MAX(0.D0,AT/OUTFLOW_RAMP))",
        "!",
        "      RETURN",
        "!",
        "      CONTAINS",
        "!",
        "!     normal stage of the section for the discharge AXQ_QQ at the",
        "!     energy slope AXQ_SS: bisection between the thalweg and 10 m",
        "!     above the highest bed point",
        "      DOUBLE PRECISION FUNCTION AXQ_STAGE(AXQ_QQ, AXQ_SS)",
        "      DOUBLE PRECISION, INTENT(IN) :: AXQ_QQ, AXQ_SS",
        "      DOUBLE PRECISION AXQ_LO, AXQ_HI, AXQ_MID",
        "      INTEGER AXQ_IT",
        "      AXQ_LO = MINVAL(AXQ_ZB)",
        "      AXQ_HI = MAXVAL(AXQ_ZB) + 10.D0",
        "      DO AXQ_IT = 1, 80",
        "        AXQ_MID = 0.5D0*(AXQ_LO + AXQ_HI)",
        "        IF(AXQ_CONVEY(AXQ_MID, AXQ_SS).LT.AXQ_QQ) THEN",
        "          AXQ_LO = AXQ_MID",
        "        ELSE",
        "          AXQ_HI = AXQ_MID",
        "        ENDIF",
        "      ENDDO",
        "      AXQ_STAGE = 0.5D0*(AXQ_LO + AXQ_HI)",
        "      END FUNCTION AXQ_STAGE",
        "!",
        "!     discharge of the section at the level AXQ_WL: Keulegan,",
        "!     C = 18 LOG10(12 R / ks), Q = C A SQRT(R S)",
        "      DOUBLE PRECISION FUNCTION AXQ_CONVEY(AXQ_WL, AXQ_SS)",
        "      DOUBLE PRECISION, INTENT(IN) :: AXQ_WL, AXQ_SS",
        "      DOUBLE PRECISION AXQ_H(AXQ_NS), AXQ_A, AXQ_P, AXQ_R, AXQ_KM",
        "      DOUBLE PRECISION AXQ_C",
        "      INTEGER AXQ_J, AXQ_NW",
        "      AXQ_CONVEY = 0.D0",
        "      AXQ_A = 0.D0",
        "      AXQ_P = 0.D0",
        "      AXQ_KM = 0.D0",
        "      AXQ_NW = 0",
        "      DO AXQ_J = 1, AXQ_NS",
        "        AXQ_H(AXQ_J) = MAX(AXQ_WL - AXQ_ZB(AXQ_J), 0.D0)",
        "        AXQ_A = AXQ_A + AXQ_H(AXQ_J)*AXQ_DS(AXQ_J)",
        "        IF(AXQ_H(AXQ_J).GT.0.D0) THEN",
        "          AXQ_KM = AXQ_KM + AXQ_KS(AXQ_J)",
        "          AXQ_NW = AXQ_NW + 1",
        "        ENDIF",
        "      ENDDO",
        "      IF(AXQ_NW.EQ.0) RETURN",
        "      DO AXQ_J = 1, AXQ_NS - 1",
        "        IF(AXQ_H(AXQ_J).GT.0.D0.OR.AXQ_H(AXQ_J+1).GT.0.D0) THEN",
        "          AXQ_P = AXQ_P + AXQ_SG(AXQ_J)",
        "        ENDIF",
        "      ENDDO",
        "      IF(AXQ_A.LE.0.D0.OR.AXQ_P.LE.0.D0) RETURN",
        "      AXQ_R = AXQ_A/AXQ_P",
        "      AXQ_KM = MAX(AXQ_KM/AXQ_NW, 1.D-4)",
        "      AXQ_C = 18.D0*LOG10(MAX(12.D0*AXQ_R/AXQ_KM, 1.5D0))",
        "      AXQ_CONVEY = AXQ_C*AXQ_A*SQRT(AXQ_R*AXQ_SS)",
        "      END FUNCTION AXQ_CONVEY",
        "!",
        "      END",
    ]
    return "\n".join(lines) + "\n"


def write_for(cfg, folder: Path, which: str | None = None) -> Path:
    """Write the routine of the outflow level the case calibrates into *folder*, at its neutral value: offset 0, or the reach slope. *which* (``"offset"``/``"slope"``) overrides the case's own choice (the validation takes it from the calibrated values)."""
    which = which or kind(cfg) or "offset"
    if which == "offset":
        return write(folder)
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / FILE
    path.write_text(slope_source(section_of(cfg)), encoding="utf-8")
    return path


def _range(cfg, key: str, positive: bool = False) -> tuple[float, float] | None:
    from axqua.core.errors import ConfigError

    given = getattr(cfg.calibration, key, None)
    if given is None:
        return None
    example = "[1.0e-5, 2.0e-3]" if positive else "[0.0, 2.0]"
    unit = "" if positive else " in meters"
    try:
        low, high = (float(v) for v in given)
    except (TypeError, ValueError):
        raise ConfigError(f"calibration.{key} is {given!r}, not a range [min, max]{unit}",
                          subject=f"calibration.{key}",
                          remedy=f"Write it as [min, max], for example {example}.") from None
    if not high > low:
        raise ConfigError(f"calibration.{key} {given!r}: max must be larger than min",
                          subject=f"calibration.{key}",
                          remedy=f"Write it as [min, max], for example {example}.")
    if positive and not low > 0:
        raise ConfigError(f"calibration.{key} {given!r}: an energy slope is larger than 0",
                          subject=f"calibration.{key}",
                          remedy=f"Write it as [min, max], for example {example}.")
    return low, high


def kind(cfg) -> str | None:
    """``"offset"`` or ``"slope"``: how the case calibrates its outflow level, or None.

    Both at once are refused with :class:`ConfigError`: they are two ways to move the same level.
    """
    from axqua.core.errors import ConfigError

    offset = getattr(cfg.calibration, "outflow_offset", None) is not None
    slope = getattr(cfg.calibration, "outflow_slope", None) is not None
    if offset and slope:
        raise ConfigError("calibration.outflow_offset and calibration.outflow_slope both "
                          "move the outflow level; a calibration varies one of them",
                          subject="calibration.outflow_slope",
                          remedy="Remove one of the two.")
    return "offset" if offset else "slope" if slope else None


def configured(cfg) -> bool:
    """Whether the case calibrates its outflow level (an offset or the energy slope)."""
    return kind(cfg) is not None


def parameter(cfg):
    """The calibration parameter of the outflow level, or None without one.

    ``calibration.outflow_offset`` gives ``f.OUTFLOW_OFFSET`` over its range in meters, ``calibration.outflow_slope`` gives ``f.OUTFLOW_LOG_SLOPE`` over ``log10`` of its range. A range that is not two increasing numbers (positive for the slope) raises :class:`ConfigError`.
    """
    from axqua.config import CalibrationParameter

    which = kind(cfg)
    if which == "offset":
        low, high = _range(cfg, "outflow_offset")
        return CalibrationParameter(
            name=NAME, min=low, max=high,
            comment="offset of the outflow level [m], added to the level of the rating")
    if which == "slope":
        low, high = _range(cfg, "outflow_slope", positive=True)
        return CalibrationParameter(
            name=SLOPE_NAME, min=math.log10(low), max=math.log10(high),
            comment="log10 of the energy slope of the outflow section [-]")
    return None


def calibrated(names) -> bool:
    """Whether the parameter names (strings) include the outflow level (offset or slope)."""
    return any(str(name).strip() in NAMES for name in names)


def check(cfg) -> None:
    """Refuse a calibration of the outflow level that the case cannot apply."""
    from axqua.core.boundaries import outlet_level
    from axqua.core.errors import ConfigError

    which = kind(cfg) or "offset"
    key = f"calibration.outflow_{which}"
    if cfg.calibration.solver_name != "Telemac2d":
        raise ConfigError("the outflow level can only be calibrated with TELEMAC-2D "
                          "(not yet available for TELEMAC-3D)",
                          subject=key, remedy=f"Remove {key}.")
    level = outlet_level(cfg.outflow_condition)
    if level not in LEVEL_KINDS:
        raise ConfigError(
            f"the outflow condition {cfg.outflow_condition!r} prescribes no level that "
            f"the {which} could change", subject=key,
            remedy=f"Use outflow_condition: stage_discharge or elevation, or remove {key}.")
    method = getattr(cfg.boundaries, "rating_method", "trapezoid")
    if which == "slope" and (level != "rating" or method != "section"):
        source = ("a fixed level (elevation)" if level == "fixed"
                  else f"a rating by the method {method!r}")
        raise ConfigError(
            "the energy slope at the outlet is calibrated in the rating of the outflow "
            f"section, but the case takes its outflow level from {source}", subject=key,
            remedy="Use outflow_condition: stage_discharge with rating_method: section, "
                   "or calibrate an offset (calibration.outflow_offset) instead.")
    if cfg.gain_lose.active and cfg.gain_lose.implementation == "fortran":
        raise ConfigError(
            "the outflow level and a gain-lose reach both need the Fortran file "
            "HydroBayesCal rewrites; calibrating both is not yet available",
            subject=key, remedy=f"Remove {key} or the gain-lose reach.")


def folder_name(value) -> str:
    """A folder as a steering file or the case names it, without quotes, ``./`` and a trailing slash."""
    text = str(value).strip().strip("'\"").strip().replace("\\", "/")
    return "/".join(part for part in text.split("/") if part not in ("", "."))


def own_routines(folder: Path) -> list[str]:
    """The files in *folder* that define ``USER_SL`` and are not the routine aXqua writes."""
    found = []
    for path in sorted(Path(folder).glob("*.[fF]*")) if Path(folder).is_dir() else []:
        text = path.read_text(encoding="utf-8", errors="replace")
        if path.name == FILE and text.startswith(MARK):
            continue
        if re.search(r"^\s+SUBROUTINE\s+USER_SL\b", text, flags=re.M | re.I):
            found.append(path.name)
    return found


def check_folder(cfg, model_dir=None) -> None:
    """Refuse a case whose user Fortran folder HydroBayesCal does not rewrite, or which holds a ``USER_SL`` of its own."""
    from axqua.core.errors import ConfigError

    if folder_name(cfg.user_fortran_dir) != FOLDER:
        raise ConfigError(
            f"the user Fortran folder of the case is {cfg.user_fortran_dir!r}, but "
            f"HydroBayesCal rewrites the routine of the outflow level in {FOLDER}/{FILE}",
            subject="user_fortran_dir",
            remedy=f"Name the folder {FOLDER} (user_fortran_dir), or remove "
                   "calibration.outflow_offset.")
    folder = Path(model_dir or cfg.model_dir) / FOLDER
    mine = own_routines(folder)
    if mine:
        raise ConfigError(
            f"{folder} holds a routine USER_SL of the case ({', '.join(mine)}), which "
            "the calibration of the outflow level would replace",
            subject="calibration.outflow_offset",
            remedy="Remove calibration.outflow_offset, or move the routine out of the "
                   "folder.")


def prepare(cfg, model_dir=None) -> Path:
    """Check the case and write ``user_sl.f`` at its neutral value (offset 0, or the reach slope) into ``user_fortran`` of *model_dir* (the case's model folder by default)."""
    check(cfg)
    check_folder(cfg, model_dir)
    return write_for(cfg, Path(model_dir or cfg.model_dir) / FOLDER)


def with_fortran_folder(cfg, text: str) -> str:
    """*text* of a steering file that names ``user_fortran``; a steering file that names another folder or file is refused, because TELEMAC would not compile the routine HydroBayesCal rewrites."""
    from axqua.core.errors import ConfigError
    from axqua.solvers.telemac.steering import keyword_spans, set_keyword

    for name, _first, _stop, value in keyword_spans(text.splitlines()):
        if name != "FORTRAN FILE":
            continue
        if folder_name(value) == FOLDER:
            return text
        raise ConfigError(
            f"the steering file names {value.strip()} in FORTRAN FILE, but the routine "
            f"of the outflow level is compiled only from the folder {FOLDER}",
            subject="FORTRAN FILE",
            remedy=f"Put the user routines of the case into the folder {FOLDER} and "
                   "name it in FORTRAN FILE, or remove calibration.outflow_offset.")
    return set_keyword(text, "FORTRAN FILE", f"'{FOLDER}'", append=True)


def with_duration(text: str, minimum: float = MIN_DURATION) -> tuple[str, float | None]:
    """*text* of a steering file whose ``DURATION`` is at least *minimum* [s], and the shorter duration it had (None when it was long enough or names none)."""
    from axqua.solvers.telemac.steering import keyword_spans, set_keyword

    for name, _first, _stop, value in keyword_spans(text.splitlines()):
        if name != "DURATION":
            continue
        try:
            current = float(value.strip().rstrip(";").replace("D", "E").replace("d", "e"))
        except ValueError:
            return text, None
        if current >= minimum:
            return text, None
        return set_keyword(text, "DURATION", f"{minimum:g}"), current
    return text, None
