"""What the OpenFOAM backend can do, and how to tell what a case has done with it.

Import-light by contract (see :mod:`axqua.solvers`): nothing here imports the
OpenFOAM extension's own modules, so a capability listing costs one small import.

The support matrix is where this backend differs most from TELEMAC's, and the
distinction between *not applicable* and *not implemented* carries real information:

* ``steady2d`` / ``unsteady2d`` are **not applicable**. ``interFoam`` is a two-phase
  VOF solver; there is no depth-averaged mode to run, and reporting "not implemented"
  would suggest axqua is merely missing a feature that could be added.
* ``morphodynamics`` and the convergence studies are **not implemented**: each is
  genuinely possible for OpenFOAM and simply is not built yet. A user deciding
  which code to set up deserves to see that difference.
* ``calibration`` **is** supported - see
  :mod:`axqua.solvers.openfoam.calibration`. It calibrates the bed roughness and
  the k-epsilon coefficients against measured velocity components.
"""

from __future__ import annotations

import os
from pathlib import Path

from axqua.core.capabilities import Capability, Support
from axqua.core.registry import BackendSpec, CapabilitySpec


def _describe_environment(cfg) -> str:
    """One line naming how this machine reaches OpenFOAM.

    On Windows this is normally ``wsl / <distro> / bashrc``: the Foundation build
    axqua targets has no native Windows port.
    """
    env = cfg.openfoam.environment
    kind = env.kind or ("windows" if os.name == "nt" else "posix")
    script = env.setup_script or cfg.openfoam.bashrc
    parts = [kind]
    if env.distro:
        parts.append(env.distro)
    if script:
        parts.append(Path(str(script)).name)
    return " / ".join(parts)


#: Parameter names HydroBayesCal's OpenFOAM binding can route, lowercased (see
#: ``hydroBayesCal.openfoam.control_openfoam.KEPSILON_COEFFS`` and its ``ks``
#: branch). Defined here rather than beside the calibration code so the capability
#: predicate below stays import-light: ``calibration.py`` pulls in pandas, and
#: ``tests/test_capabilities.py`` asserts that listing capabilities imports nothing
#: heavy.
OPENFOAM_PARAMETERS = frozenset({"ks", "cmu", "c1", "c2", "sigmak", "sigmaeps"})


def _case(cfg) -> Path:
    return Path(cfg.openfoam_case_dir)


def _calibration_root(cfg) -> Path:
    return Path(cfg.calibration_dir) / "openfoam"


def _calibration_configured(cfg) -> bool:
    """An OpenFOAM block plus at least one parameter the binding can actually route.

    Only ``calibration.parameters`` is consulted. The roughness table and the
    target-template spreadsheet are the other two places a parameter can be
    declared, but reading either needs pandas, which this module must not import.
    That is no real loss here: OpenFOAM's ``ks`` is one global value, so a per-zone
    roughness table has nothing to contribute to it.
    """
    return ("openfoam" in cfg.declared_blocks
            and any(str(p.name).strip().lower() in OPENFOAM_PARAMETERS
                    for p in cfg.calibration.parameters))


def _calibration_built(cfg) -> bool:
    """The per-run case template and the emitted HydroBayesCal config both exist."""
    root = _calibration_root(cfg)
    return ((root / "case-template" / "constant" / "polyMesh" / "faces").is_file()
            and (root / "config_OpenFOAM.py").is_file())


def _calibration_run(cfg) -> bool:
    """The initial design has produced model outputs."""
    return (_calibration_root(cfg) / "auto-saved-results-HydroBayesCal"
            / "restart_data" / "initial-model-outputs.json").is_file()


def _mesh_built(cfg) -> bool:
    """The mesh is the case: no ``faces`` file, nothing to run."""
    return (_case(cfg) / "constant" / "polyMesh" / "faces").is_file()


def _has_run(cfg) -> bool:
    """Any written time directory beyond ``0`` - in the case root after a serial run
    or a reconstruct, or inside ``processor0`` while a parallel run is still
    decomposed."""
    for root in (_case(cfg), _case(cfg) / "processor0"):
        if not root.is_dir():
            continue
        for entry in root.iterdir():
            try:
                if float(entry.name) > 0.0:
                    return True
            except ValueError:
                continue
    return False


SPEC = BackendSpec(
    name="openfoam",
    title="OpenFOAM interFoam (two-phase free surface)",
    config_key="openfoam",
    implementation="axqua.solvers.openfoam.backend:BACKEND",
    # Declared by the case when the block names an OpenFOAM installation. Whether
    # *this machine* has it is the marker body's `env` line, not the filename.
    enabled=lambda cfg: "openfoam" in cfg.declared_blocks,
    environment=lambda cfg: _describe_environment(cfg),
    capabilities={
        Capability.STEADY2D: CapabilitySpec(support=Support.NOT_APPLICABLE),
        Capability.UNSTEADY2D: CapabilitySpec(support=Support.NOT_APPLICABLE),
        # The VOF run is 3D and transient by nature; it is reported under
        # free_surface_3d rather than pretending to be a "steady 3D" case.
        Capability.STEADY3D: CapabilitySpec(support=Support.NOT_APPLICABLE),
        Capability.UNSTEADY3D: CapabilitySpec(support=Support.NOT_APPLICABLE),
        Capability.FREE_SURFACE_3D: CapabilitySpec(
            support=Support.SUPPORTED,
            configured=lambda cfg: "openfoam" in cfg.declared_blocks,
            built=_mesh_built,
            run=_has_run,
        ),
        # Possible, not yet built - see the plan's phase list.
        Capability.MORPHODYNAMICS: CapabilitySpec(support=Support.NOT_IMPLEMENTED),
        Capability.GAIN_LOSE: CapabilitySpec(support=Support.NOT_IMPLEMENTED),
        Capability.MESH_CONVERGENCE: CapabilitySpec(support=Support.NOT_IMPLEMENTED),
        Capability.VERTICAL_CONVERGENCE: CapabilitySpec(support=Support.NOT_IMPLEMENTED),
        Capability.CALIBRATION: CapabilitySpec(
            support=Support.SUPPORTED,
            configured=_calibration_configured,
            built=_calibration_built,
            run=_calibration_run,
        ),
    },
)
