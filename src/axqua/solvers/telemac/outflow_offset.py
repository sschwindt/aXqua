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
final value 300 s after a step of 1.6 m.

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


def _dbl(value: float) -> str:
    text = f"{float(value):.10G}"
    return text.replace("E", "D") if "E" in text else text + "D0"


def source(offset: float = 0.0) -> str:
    """Fixed-form Fortran of ``USER_SL`` that adds *offset* [m] to every prescribed level."""
    lines = [
        "!     aXqua generated: the outflow level as a calibration parameter",
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


def prepare(cfg) -> Path:
    """Check the case and write ``user_sl.f`` with offset 0 into its user Fortran folder."""
    check(cfg)
    return write(Path(cfg.model_dir) / cfg.user_fortran_dir)


def with_fortran_folder(cfg, text: str) -> str:
    """*text* of a steering file that names the user Fortran folder."""
    from axqua.solvers.telemac.steering import set_keyword

    if any(line.strip().upper().startswith("FORTRAN FILE")
           for line in text.splitlines()):
        return text
    return set_keyword(text, "FORTRAN FILE", f"'{cfg.user_fortran_dir}'", append=True)
