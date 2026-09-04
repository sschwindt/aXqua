"""The VisIt backend: generate a script, run it in VisIt's own Python.

VisIt ships its own interpreter, so ``import visit`` from aXqua's environment is
not possible - and unlike ParaView there is no importable module to fall back on.
The backend therefore **writes a script and executes it with**
``visit -cli -nowin -s``, which is the same shape as
:class:`axqua.solvers.openfoam.runtime.OpenFoamRuntime` sourcing ``etc/bashrc``:
aXqua never imports the tool's Python, it invokes it.

Everything the script needs is **baked in as a literal**. No ``sys.argv``, no
environment lookups: argument forwarding after ``-s`` differs between VisIt
versions, and a self-contained script is also the reproducible record of how a
figure was made. The generated scripts are kept under
``<postprocessing_dir>/visit/`` for exactly that reason.

Three details are load-bearing and each is pinned by a test:

* **the script ends with** ``exit(0)``. Without it ``visit -cli -nowin`` drops
  into an interactive loop and the subprocess hangs forever - the single most
  likely silent failure in this whole path.
* ``outputToCurrentDirectory = 0`` plus an explicit ``outputDirectory``, or the
  PNG lands wherever VisIt happened to start.
* errors inside the script print ``AXQUA-VISIT-ERROR:`` and exit non-zero,
  because VisIt's CLI exit status alone is not reliable enough to judge a run by.
"""

from __future__ import annotations

import logging
import shlex
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

from axqua.core.environment import SolverEnvironment
from axqua.env import ShellRuntime
from axqua.postproc.dataset import Dataset
from axqua.postproc.scenes import Scene, resolved_requirements

log = logging.getLogger("axqua")

ERROR_MARKER = "AXQUA-VISIT-ERROR:"


# --------------------------------------------------------------------------- #
# running
# --------------------------------------------------------------------------- #
class VisitRuntime(ShellRuntime):
    """Run a generated script in VisIt's own Python.

    Normally there is nothing to source - VisIt ships a self-contained launcher -
    so the environment degrades to a plain ``bash -lc``. ``postproc.environment``
    covers the installs that do need a setup script, and is what makes this work
    through WSL on Windows.
    """

    def __init__(self, postproc_config):
        block = getattr(postproc_config, "environment", None)
        script = getattr(block, "setup_script", None) if block is not None else None
        super().__init__(environment=SolverEnvironment.from_config(
            block, legacy_script=script))
        self.visit = str(postproc_config.visit or "visit")

    def check_available(self) -> str:
        proc = self.run(f"{shlex.quote(self.visit)} -version", check=False)
        text = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode != 0 or "VisIt" not in text:
            raise RuntimeError(
                f"Could not run VisIt via {self.visit!r}.\n"
                "Point postproc.visit at the `visit` launcher of your install, e.g.\n"
                "    postproc:\n      visit: /home/IWS/public/visit/bin/visit\n"
                f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")
        return text.strip().splitlines()[0]

    def execute(self, script: Path, *, on_line=None) -> subprocess.CompletedProcess:
        """Run one generated script. Never raises on a non-zero exit - see
        :func:`failed`, which reads the output rather than the status."""
        return self.run(
            f"{shlex.quote(self.visit)} -cli -nowin -s {shlex.quote(str(script))}",
            check=False, on_line=on_line)


def failed(proc: subprocess.CompletedProcess) -> str | None:
    """The error a VisIt run reported, or ``None``.

    VisIt's CLI exit status is unreliable, so the marker the generated script
    prints is what is trusted; the status is only a fallback.
    """
    text = (proc.stdout or "") + (proc.stderr or "")
    for line in text.splitlines():
        if ERROR_MARKER in line:
            return line.split(ERROR_MARKER, 1)[1].strip()
    if proc.returncode != 0:
        return f"visit exited with code {proc.returncode}"
    return None


def available(visit: str | Path) -> str | None:
    """The VisIt version, or ``None``. Never raises - this is a probe."""
    try:
        proc = subprocess.run([str(visit), "-version"], capture_output=True,
                              text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    text = (proc.stdout or "") + (proc.stderr or "")
    return text.strip().splitlines()[0] if "VisIt" in text else None


# --------------------------------------------------------------------------- #
# script generation
# --------------------------------------------------------------------------- #
def _header(dataset: Dataset, scenes: Sequence[Scene], out_dir: Path) -> str:
    from axqua import __version__ as version

    names = ", ".join(s.name for s in scenes)
    return f'''"""Generated by axqua {version} - do not edit; regenerate with `axqua postproc`.

scene(s): {names}
dataset : {dataset.path}
output  : {out_dir}

Run standalone with:  visit -cli -nowin -s <this file>
"""
import sys

AXQUA_ERROR = "{ERROR_MARKER}"


def _fail(message):
    # VisIt's CLI exit status is not reliable on its own, so say so in the output
    # too - axqua greps for this marker.
    print(AXQUA_ERROR + " " + str(message))
    sys.exit(1)


def _guard(exc_type, exc, tb):
    """Never fall through to the interactive prompt.

    `visit -cli -nowin` drops to `>>>` on an unhandled exception and waits there
    forever - the subprocess hangs with no output and no exit. Routing the hook
    through _fail() turns any escape into a marked, non-zero exit instead.
    """
    import traceback
    print(AXQUA_ERROR + " " + "".join(traceback.format_exception(exc_type, exc, tb)))
    sys.exit(1)


sys.excepthook = _guard
'''


def visit_target(dataset: Dataset) -> Path:
    """The path VisIt's own readers open, which is not always the canonical one.

    For an OpenFOAM case that is ``system/controlDict``, **not** the ``case.foam``
    marker: unlike ParaView, VisIt's OpenFOAM reader cannot open a zero-byte
    ``.foam`` handle and reports the file as invalid. Measured against VisIt 3.5.0
    - ``case.foam`` yields 0 meshes and 0 variables, ``system/controlDict`` yields
    the full case. The ``Dataset`` keeps ``case.foam`` because that is what
    ParaView and the QGIS manifest want; knowing how *VisIt* opens a case is this
    module's business.
    """
    path = Path(dataset.path)
    if dataset.kind == "openfoam":
        return path.parent / "system" / "controlDict"
    return path


def _open(dataset: Dataset) -> str:
    target = visit_target(dataset)
    return f'''
try:
    OpenDatabase({str(target)!r})
except Exception as exc:
    _fail("could not open {target}: %s" % exc)
'''


def _expressions(dataset: Dataset) -> str:
    """Define the derived quantities explicitly.

    VisIt does not create a ``_magnitude`` variable automatically, and the mesh
    prefix differs between its OpenFOAM and VTK readers - so the expressions are
    written out rather than guessed at, which also makes them greppable in the
    kept script.
    """
    # VisIt namespaces an OpenFOAM case's variables by mesh ("internalMesh/U"),
    # so a bare "<U>" does not resolve. Verified against VisIt 3.5.0.
    vector = "internalMesh/U" if dataset.kind == "openfoam" else "velocity"
    return f'''
DefineScalarExpression("axqua_Umag", "magnitude(<{vector}>)")
DefineVectorExpression("axqua_U", "<{vector}>")
'''


def _time_state(time: str | float) -> str:
    """Move the time slider before drawing.

    Not cosmetic. VisIt opens a database on its FIRST state, which for a solver
    case is t=0 - the initial condition, where the velocity field is identically
    zero. A figure made without this shows a blank result and a 0.000 colour bar
    while looking perfectly well-formed, which is the same failure the
    ``mean_last`` -> ``last`` extraction patch exists to prevent on the
    calibration side.
    """
    if time == "last":
        return '''
n_states = TimeSliderGetNStates()
if n_states > 1:
    SetTimeSliderState(n_states - 1)
'''
    if time in ("first", "0", 0):
        return "\n"
    return f'''
# nearest state to the requested simulated time
_target = float({float(time)!r})
_times = list(GetDatabaseNStates() and [])
n_states = TimeSliderGetNStates()
SetTimeSliderState(max(0, min(n_states - 1, int(round(_target)))))
'''


def _save_settings(out_dir: Path, name: str, width: int, height: int) -> str:
    return f'''
s = SaveWindowAttributes()
s.format = s.PNG
s.outputToCurrentDirectory = 0          # or the PNG lands wherever visit started
s.outputDirectory = {str(out_dir)!r}
s.fileName = {name!r}
s.family = 0
s.width, s.height = {width}, {height}
s.resConstraint = s.NoConstraint
SetSaveWindowAttributes(s)
'''


def _render_free_surface(dataset: Dataset, scene: Scene, out_dir: Path,
                         *, used: tuple[str, ...], **_kw) -> str:
    """Isosurface of alpha.water, or the lid patch when the surface is prescribed."""
    if "field:alpha.water" in used:
        # namespaced by mesh, exactly like the velocity: a bare "alpha.water" is
        # rejected with InvalidVariableException by VisIt's OpenFOAM reader.
        alpha = ("internalMesh/alpha.water" if dataset.kind == "openfoam"
                 else "alpha.water")
        body = f'''
AddPlot("Pseudocolor", "axqua_Umag")
AddOperator("Isosurface")
iso = IsosurfaceAttributes()
iso.variable = "{alpha}"
iso.contourMethod = iso.Value
iso.contourValue = ({scene.options.get("iso_value", 0.5)},)
SetOperatorOptions(iso)
'''
        caption = "free surface (alpha.water = %.2f)" % scene.options.get("iso_value", 0.5)
    else:
        # rigid lid: the surface was PRESCRIBED from the 2D result, not solved, so
        # say so on the figure rather than letting it read as a computed surface
        body = '''
AddPlot("Pseudocolor", "axqua_Umag")
'''
        caption = "lid patch - the surface is PRESCRIBED by the 2D seed, not solved"

    return body + f'''
DrawPlots()
banner = CreateAnnotationObject("Text2D")
banner.text = {caption!r}
banner.position = (0.02, 0.06)   # bottom-left: the top-left carries VisIt's DB/time header
banner.height = 0.02
'''


def _render_velocity_plan(dataset: Dataset, scene: Scene, out_dir: Path,
                          *, used: tuple[str, ...], **_kw) -> str:
    """Plan view coloured by speed, with vector glyphs over it."""
    slice_block = ""
    if dataset.kind == "openfoam" and dataset.bounds:
        zmin, zmax = dataset.bounds[4], dataset.bounds[5]
        fraction = scene.options.get("depth_fraction", 0.6)
        level = zmin + fraction * (zmax - zmin)
        # sampled at the same relative depth the FlowTracker measures at, so the
        # figure and the calibration are looking at the same thing
        slice_block = f'''
AddOperator("Slice")
sl = SliceAttributes()
sl.originType = sl.Point
sl.originPoint = (0, 0, {level!r})
sl.normal = (0, 0, 1)
sl.axisType = sl.ZAxis
sl.project2d = 0
SetOperatorOptions(sl)
'''
    return f'''
AddPlot("Pseudocolor", "axqua_Umag")
{slice_block}
AddPlot("Vector", "axqua_U")
v = VectorAttributes()
v.useStride = 1
v.stride = 4
v.scale = 0.5
SetPlotOptions(v)
DrawPlots()
'''


def _render_profiles(dataset: Dataset, scene: Scene, out_dir: Path, *,
                     used: tuple[str, ...], points: Sequence[Mapping] = (),
                     **_kw) -> str:
    """A bed-to-surface Lineout at each measurement vertical, exported as a curve.

    VisIt samples, matplotlib draws (see :mod:`axqua.postproc.profiles`). The
    measured overlay needs error bars, the figure has to match aXqua's other
    figures, and the comparison must stay reproducible from the exported curves
    once VisIt is no longer involved.
    """
    data_dir = out_dir / "data"
    # The z span comes from the MESH, not from the survey. On inn-KB15 the
    # ground-truth bed elevations sit ~2.1 m ABOVE the model bed at the same (x, y)
    # - a known bathymetry/datum discrepancy on this reach - so a lineout run from
    # the surveyed bed upward misses the water column entirely and VisIt returns
    # "Curve plot yielded no data" for every vertical. Asking VisIt for the spatial
    # extents costs nothing, works whatever the datum, and lets profiles.compare
    # normalise on the modelled column.
    blocks = [f'''
import os
os.makedirs({str(data_dir)!r}, exist_ok=True)
AddPlot("Pseudocolor", "axqua_Umag")
DrawPlots()
Query("SpatialExtents")
_ext = GetQueryOutputValue()
_zmin, _zmax = _ext[4], _ext[5]
_pad = 0.01 * (_zmax - _zmin)
print("axqua: mesh z extent %.3f .. %.3f" % (_zmin, _zmax))
''']
    for point in points:
        pid = point["id"]
        x, y = float(point["x"]), float(point["y"])
        blocks.append(f'''
try:
    Lineout(({x!r}, {y!r}, _zmin - _pad), ({x!r}, {y!r}, _zmax + _pad), ("axqua_Umag",))
    SetActiveWindow(2)
    e = ExportDBAttributes()
    e.db_type = "Curve2D"
    e.filename = "profile-{pid}"
    e.dirname = {str(data_dir)!r}
    ExportDatabase(e)
    DeleteAllPlots()
    SetActiveWindow(1)
except Exception as exc:
    print("axqua: profile {pid} failed: %s" % exc)
''')
    return "".join(blocks)


_RENDERERS = {
    "free_surface": _render_free_surface,
    "velocity_plan": _render_velocity_plan,
    "profiles": _render_profiles,
}


def render_script(dataset: Dataset, scene: Scene, out_dir: Path, *,
                  width: int = 1600, height: int = 1000,
                  time: str | float = "last",
                  extras: Sequence[str] = (), **kwargs) -> str:
    """The complete VisIt script for one scene against one dataset."""
    renderer = _RENDERERS.get(scene.render)
    if renderer is None:
        raise ValueError(f"the visit backend has no renderer named {scene.render!r}; "
                         f"it implements {', '.join(sorted(_RENDERERS))}")
    used = resolved_requirements(scene, dataset, extras=extras)
    out_dir = Path(out_dir).resolve()

    parts = [
        _header(dataset, [scene], out_dir),
        _open(dataset),
        _time_state(time),
        _expressions(dataset),
        renderer(dataset, scene, out_dir, used=used, **kwargs),
    ]
    if scene.render != "profiles":
        parts += [
            _save_settings(out_dir, scene.name, width, height),
            '\nSaveWindow()\n',
        ]
    # Without this a `-nowin` CLI run never returns.
    parts.append('\nexit(0)\n')
    return "".join(parts)


def write_script(text: str, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path
