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

Not available yet: TELEMAC-3D, a level from TELEMAC's own Z(Q) curve
(``normal_depth``) and outflow lines without a level (``free``, ``overflow``).
"""

from __future__ import annotations

import re
from pathlib import Path

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


def parameter(cfg):
    """The calibration parameter of ``calibration.outflow_offset``, or None without one.

    A range that is not two increasing numbers raises :class:`ConfigError`.
    """
    from axqua.config import CalibrationParameter
    from axqua.core.errors import ConfigError

    given = cfg.calibration.outflow_offset
    if given is None:
        return None
    try:
        low, high = (float(v) for v in given)
    except (TypeError, ValueError):
        raise ConfigError(f"calibration.outflow_offset is {given!r}, not a range "
                          "[min, max] in meters", subject="calibration.outflow_offset",
                          remedy="Write it as [min, max], for example [0.0, 2.0].") from None
    if not high > low:
        raise ConfigError(f"calibration.outflow_offset {given!r}: max must be larger "
                          "than min", subject="calibration.outflow_offset",
                          remedy="Write it as [min, max], for example [0.0, 2.0].")
    return CalibrationParameter(
        name=NAME, min=low, max=high,
        comment="offset of the outflow level [m], added to the level of the rating")


def calibrated(names) -> bool:
    """Whether the parameter names (strings) include the outflow offset."""
    return any(str(name).strip() == NAME for name in names)


def check(cfg) -> None:
    """Refuse a calibration of the offset that the case cannot apply."""
    from axqua.core.boundaries import outlet_level
    from axqua.core.errors import ConfigError

    if cfg.calibration.solver_name != "Telemac2d":
        raise ConfigError("the outflow level can only be calibrated with TELEMAC-2D "
                          "(not yet available for TELEMAC-3D)",
                          subject="calibration.outflow_offset",
                          remedy="Remove calibration.outflow_offset.")
    kind = outlet_level(cfg.outflow_condition)
    if kind not in LEVEL_KINDS:
        raise ConfigError(
            f"the outflow condition {cfg.outflow_condition!r} prescribes no level that "
            "an offset could change", subject="calibration.outflow_offset",
            remedy="Use outflow_condition: stage_discharge or elevation, or remove "
                   "calibration.outflow_offset.")
    if cfg.gain_lose.active and cfg.gain_lose.implementation == "fortran":
        raise ConfigError(
            "the outflow level and a gain-lose reach both need the Fortran file "
            "HydroBayesCal rewrites; calibrating both is not yet available",
            subject="calibration.outflow_offset",
            remedy="Remove calibration.outflow_offset or the gain-lose reach.")


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
    """Check the case and write ``user_sl.f`` with offset 0 into ``user_fortran`` of *model_dir* (the case's model folder by default)."""
    check(cfg)
    check_folder(cfg, model_dir)
    return write(Path(model_dir or cfg.model_dir) / FOLDER)


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
