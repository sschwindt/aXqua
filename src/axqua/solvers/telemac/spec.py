"""What the TELEMAC backend can do, and how to tell what a case has done with it.

Import-light by contract (see :mod:`axqua.solvers`): standard library, plus
attribute access on a ``Config``. Nothing here imports ``steering``, ``selafin``,
gmsh or TELEMAC's Python, because ``axqua status`` and a future QGIS plugin read
this module to populate a capability table and must not pay for a solver import to do
it.

The ``built``/``run`` predicates are deliberately **path checks on the case's own
declared filenames** rather than attempts to parse anything. A status report has to be
fast, has to work on a half-finished case, and must never be the thing that fails.
"""

from __future__ import annotations

import os
from pathlib import Path

from axqua.core.capabilities import Capability, Support
from axqua.core.registry import BackendSpec, CapabilitySpec

# the three 3D steering files add3d.py writes (axqua.threed)
HYDROSTATIC_CAS = "hotstart3d_hydrostatic.cas"
#: What the hydrostatic run writes. It keeps a name of its own so that it does not
#: overwrite the non-hydrostatic result (``Config.results3d_slf``).
HYDROSTATIC_RESULT = "r3d-hydrostatic.slf"
HYDRODYN_CAS = "hotstart3d_hydrodyn.cas"
UNSTEADY3D_CAS = "unsteady3d.cas"


def _describe_environment(cfg) -> str:
    """One line naming how this machine reaches TELEMAC (posix / windows / wsl).

    Import-light: builds the description from config fields only, never by probing -
    a status listing must not spawn a shell per solver.
    """
    env = cfg.telemac.environment
    kind = env.kind or ("windows" if os.name == "nt" else "posix")
    script = env.setup_script or cfg.telemac.pysource
    parts = [kind]
    if env.distro:
        parts.append(env.distro)
    if script:
        parts.append(Path(str(script)).name)
    return " / ".join(parts)


def _model(cfg, name: str) -> Path:
    return Path(cfg.model_dir) / name


def _exists(cfg, name: str) -> bool:
    return _model(cfg, name).is_file()


def result_files(cfg) -> list[tuple[str, str, Path]]:
    """Every result file a TELEMAC case can have: ``(name, what it is, path)``.

    All of them, whether they exist or not; the caller looks. The names are the ones
    the steering files are written with, so a case that renames a result in its case
    file is followed. The hydrostatic 3D run keeps names of its own.
    """
    steady = Path(str(getattr(cfg, "results_slf", "r2d.slf")))
    rows = [
        (steady.name, "steady simulation, 2D"),
        # the name steering.write_hotstart_cas gives the continued run
        (f"{steady.stem}-hotstart{steady.suffix}", "continued steady simulation, 2D"),
        (getattr(cfg, "results_unsteady_slf", "r2d-unsteady.slf"),
         "unsteady simulation, 2D"),
        (getattr(cfg, "results3d_slf", "r3d.slf"), "3D simulation"),
        (getattr(cfg, "results2d_from_3d_slf", "r3d-2d.slf"),
         "3D simulation, depth-averaged"),
        (HYDROSTATIC_RESULT, "3D simulation, hydrostatic"),
        ("r3d-2d-hydrostatic.slf", "3D simulation, hydrostatic, depth-averaged"),
        (getattr(cfg, "results3d_unsteady_slf", "r3d-unsteady.slf"),
         "unsteady simulation, 3D"),
        (getattr(cfg, "results2d_from_3d_unsteady_slf", "r3d-2d-unsteady.slf"),
         "unsteady simulation, 3D, depth-averaged"),
    ]
    seen: set[str] = set()
    out = []
    for name, label in rows:
        if name and name not in seen:
            seen.add(name)
            out.append((name, label, _model(cfg, name)))
    return out


# --------------------------------------------------------------------------- #
# "configured": what this case asks for, from the config alone
# --------------------------------------------------------------------------- #


def _has_varying_inflow(cfg) -> bool:
    """A *varying* hydrograph is what makes a case unsteady - a constant series is
    just the steady case written as a table, and reading the file to find out would
    break this module's import-light contract, so the presence of the series is taken
    as the declaration."""
    return cfg.boundaries.inflow is not None


def _calibration_configured(cfg) -> bool:
    """Calibration needs parameters to perturb and ground truth to fit against."""
    has_params = bool(cfg.calibration.parameters)
    gt = cfg.ground_truth
    has_truth = bool(gt.measurements or gt.sources or gt.targets)
    return has_params and has_truth


def _three_d_configured(cfg) -> bool:
    """The 3D path is available once the 2D result it hotstarts from exists; there is
    no separate config switch for it (``add3d.py`` is an opt-in script)."""
    return _exists(cfg, cfg.results_slf)


SPEC = BackendSpec(
    name="telemac",
    title="TELEMAC-2D / TELEMAC-3D (+ GAIA)",
    config_key="telemac",
    implementation="axqua.solvers.telemac.backend:BACKEND",
    # declared by the case, not "resolvable on this machine" - the marker
    # FILENAME must mean the same thing wherever the case is checked out
    enabled=lambda cfg: "telemac" in cfg.declared_blocks,
    environment=lambda cfg: _describe_environment(cfg),
    capabilities={
        Capability.STEADY2D: CapabilitySpec(
            support=Support.SUPPORTED,
            configured=lambda cfg: True,           # every TELEMAC case builds one
            built=lambda cfg: _exists(cfg, cfg.cas_file),
            run=lambda cfg: _exists(cfg, cfg.results_slf),
        ),
        Capability.UNSTEADY2D: CapabilitySpec(
            support=Support.SUPPORTED,
            configured=_has_varying_inflow,
            built=lambda cfg: _exists(cfg, cfg.unsteady_cas_file),
            run=lambda cfg: _exists(cfg, cfg.results_unsteady_slf),
        ),
        Capability.STEADY3D: CapabilitySpec(
            support=Support.SUPPORTED,
            configured=_three_d_configured,
            built=lambda cfg: _exists(cfg, HYDRODYN_CAS) or _exists(cfg, HYDROSTATIC_CAS),
            # Either variant is a steady 3D run. Only the non-hydrostatic result was
            # looked for, so the variant the plugin runs never showed as run.
            run=lambda cfg: (_exists(cfg, cfg.results3d_slf)
                             or _exists(cfg, HYDROSTATIC_RESULT)),
        ),
        Capability.UNSTEADY3D: CapabilitySpec(
            support=Support.SUPPORTED,
            configured=lambda cfg: _has_varying_inflow(cfg) and _three_d_configured(cfg),
            built=lambda cfg: _exists(cfg, UNSTEADY3D_CAS),
            run=lambda cfg: _exists(cfg, cfg.results3d_unsteady_slf),
        ),
        # A free-surface VOF model is not what TELEMAC is: it solves the shallow-water
        # equations with the surface as a state variable, not two phases with a
        # resolved interface. "Not implemented" would imply it is merely missing.
        Capability.FREE_SURFACE_3D: CapabilitySpec(support=Support.NOT_APPLICABLE),
        Capability.MORPHODYNAMICS: CapabilitySpec(
            support=Support.SUPPORTED,
            configured=lambda cfg: bool(cfg.morphodynamics.enabled),
            built=lambda cfg: _exists(cfg, cfg.gaia_cas),
            run=lambda cfg: _exists(cfg, cfg.results_slf),
        ),
        Capability.GAIN_LOSE: CapabilitySpec(
            support=Support.SUPPORTED,
            configured=lambda cfg: bool(cfg.gain_lose.enabled),
            built=lambda cfg: (_exists(cfg, cfg.source_regions_file)
                               or _model(cfg, cfg.user_fortran_dir).is_dir()),
            run=lambda cfg: _exists(cfg, cfg.results_slf),
        ),
        Capability.MESH_CONVERGENCE: CapabilitySpec(
            support=Support.SUPPORTED,
            configured=lambda cfg: True,
            built=lambda cfg: (Path(cfg.postprocessing_dir) / "mesh-convergence").is_dir(),
            run=lambda cfg: any((Path(cfg.postprocessing_dir) / "mesh-convergence")
                                .glob("mesh-convergence.*")),
        ),
        Capability.VERTICAL_CONVERGENCE: CapabilitySpec(
            support=Support.SUPPORTED,
            configured=_three_d_configured,
            built=lambda cfg: (Path(cfg.postprocessing_dir)
                               / "vertical-convergence").is_dir(),
            run=lambda cfg: any((Path(cfg.postprocessing_dir) / "vertical-convergence")
                                .glob("vertical-convergence.*")),
        ),
        Capability.CALIBRATION: CapabilitySpec(
            support=Support.SUPPORTED,
            configured=_calibration_configured,
            built=lambda cfg: (Path(cfg.calibration_dir) / cfg.hbc_config).is_file(),
            run=lambda cfg: any(Path(cfg.calibration_dir).glob("**/restart_data")),
        ),
    },
)
