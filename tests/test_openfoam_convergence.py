"""The mesh convergence study of the OpenFOAM free-surface model.

No OpenFOAM: the backend is a stand-in that records what it was asked to build, and
the result seen from above is a small SELAFIN whose depth and velocity converge with
the plan spacing at second order, as a consistent discretization does.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from axqua.core import selafin
from axqua.core.capabilities import Capability
from axqua.solvers.openfoam import convergence as ofc


def _cfg(tmp_path: Path, cell_size: float = 1.0):
    return SimpleNamespace(
        name="lattice", postprocessing_dir=tmp_path / "post", openfoam_dir=None,
        openfoam=SimpleNamespace(cell_size=cell_size, cell_size_factor=None),
        boundaries=SimpleNamespace(prescribed_flowrate=2.4))


class _Backend:
    def __init__(self):
        self.built: list[tuple[float, Path, object]] = []
        self.runs = 0

    def build(self, cfg, capability=None, *, seed=None, **_):
        self.built.append((cfg.openfoam.cell_size, Path(cfg.openfoam_dir), seed))
        Path(cfg.openfoam_dir).mkdir(parents=True, exist_ok=True)
        return SimpleNamespace(mesh=SimpleNamespace(n_cells=int(1000 / cfg.openfoam.cell_size ** 2)))

    def run(self, cfg, capability=None, *, n_processors=None, **_):
        self.runs += 1


def _plan_view(cfg, folder: Path):
    """Depth and velocity that approach 0.40 m and 0.80 m/s as dx**2."""
    dx = cfg.openfoam.cell_size
    xs, ys = np.meshgrid(np.arange(5.0), np.arange(5.0))
    x, y = xs.ravel() * 10.0, ys.ravel() * 10.0
    triangles = []
    for j in range(4):
        for i in range(4):
            a = j * 5 + i
            triangles += [(a, a + 1, a + 6), (a, a + 6, a + 5)]
    ikle = np.array(triangles) + 1
    depth = np.full(x.size, 0.40 + 0.02 * dx ** 2)
    speed = np.full(x.size, 0.80 - 0.03 * dx ** 2)
    out = Path(folder) / "depth-averaged.slf"
    selafin._write_selafin(out, x, y, ikle, np.zeros(x.size, dtype=int),
                           [("WATER DEPTH", "M", depth), ("SCALAR VELOCITY", "M/S", speed)],
                           title="test", date=(2026, 1, 1, 0, 0, 0), double_precision=True)
    return out, {}


PROBES = np.array([[12.0, 13.0], [25.0, 20.0], [31.0, 8.0]])


def test_the_ladder_refines_the_plan_spacing_of_the_case(tmp_path):
    levels = ofc.openfoam_levels(_cfg(tmp_path, 2.0))
    assert [label for label, _, _ in levels] == ["coarser x1.30", "baseline",
                                                 "finer x1.30", "finer x1.69"]
    assert [round(dx, 4) for _, dx, _ in levels] == [2.6, 2.0, round(2 / 1.3, 4),
                                                     round(2 / 1.69, 4)]


def test_every_level_is_built_from_the_same_seed_and_compared_from_above(tmp_path):
    backend = _Backend()
    report = ofc.run_openfoam_mesh_convergence(
        _cfg(tmp_path), seed="/case/simulation/r2d.slf", probes=PROBES,
        backend=backend, plan_view=_plan_view, ask=lambda *a, **k: True)
    assert [round(dx, 3) for dx, _, _ in backend.built] == [1.3, 1.0, 0.769, 0.592]
    assert {seed for _, _, seed in backend.built} == {"/case/simulation/r2d.slf"}
    assert backend.runs == 4
    assert report.converged
    assert all(level.validity is None for level in report.levels)   # no TELEMAC closure
    np.testing.assert_allclose(report.levels[1].qoi["WATER DEPTH"], 0.42)
    np.testing.assert_allclose([level.scale for level in report.levels],
                               [1.3, 1.0, 1 / 1.3, 1 / 1.69])
    folder = tmp_path / "post" / "openfoam-mesh-convergence" / "baseline"
    assert json.loads((folder / "level.json").read_text())["channel_size"] == 1.0

    # a second study takes the finished levels without building anything again
    # (a level of a study before 2026-10-17 stored its scale against the first level)
    stored = json.loads((folder / "level.json").read_text())
    stored["scale"] = 1 / 1.3
    (folder / "level.json").write_text(json.dumps(stored))
    again = _Backend()
    resumed = ofc.run_openfoam_mesh_convergence(
        _cfg(tmp_path), probes=PROBES, backend=again, plan_view=_plan_view,
        ask=lambda *a, **k: True)
    assert again.built == [] and len(resumed.levels) == 4
    np.testing.assert_allclose([level.scale for level in resumed.levels],
                               [1.3, 1.0, 1 / 1.3, 1 / 1.69])
    assert json.loads((folder / "level.json").read_text())["scale"] == 1.0   # corrected


def test_the_backend_runs_the_study_and_writes_its_report(tmp_path, monkeypatch):
    from axqua.solvers.openfoam.backend import BACKEND

    recorded = []
    ctx = SimpleNamespace(ask=lambda *a, **k: True, check_cancelled=lambda: None,
                          progress=lambda **kw: None,
                          record=lambda *a, **k: recorded.append(a[0]))
    original = ofc.run_openfoam_mesh_convergence
    monkeypatch.setattr(ofc, "run_openfoam_mesh_convergence",
                        lambda cfg, **kw: original(cfg, probes=PROBES, backend=_Backend(),
                                                   plan_view=_plan_view, **kw))
    report = BACKEND.study(_cfg(tmp_path), Capability.MESH_CONVERGENCE, ctx=ctx,
                           seed="r2d.slf", n_processors=2, answers={"resume": True})
    assert report.converged
    folder = tmp_path / "post" / "openfoam-mesh-convergence"
    assert (folder / "openfoam-mesh-convergence.xlsx").is_file()
    assert recorded == ["Openfoam mesh convergence summary"]


def test_the_study_is_a_job_kind_that_waits_for_the_case_and_gets_its_seed():
    from axqua.jobs import workspace
    from axqua.jobs.model import KIND_META, JobKind, parse_kind

    meta = KIND_META[JobKind.OPENFOAM_MESH_CONVERGENCE]
    assert (meta.solver, meta.capability, meta.verb) == ("openfoam",
                                                          Capability.MESH_CONVERGENCE,
                                                          "study")
    assert parse_kind("of-meshconv") is JobKind.OPENFOAM_MESH_CONVERGENCE
    assert "openfoam-mesh-convergence" in workspace.QUEUED_WITH_THE_CASE
    options = meta.options.from_dict({"n_processors": 4, "auto_extend": True})
    assert options.answers == {"resume": True, "extend": False}


def test_a_run_on_more_ranks_than_the_case_was_built_for_is_decomposed_for_them(
        tmp_path, monkeypatch):
    """The MPI ranks of a job (or of a mesh study) decide the decomposition. The case
    was built for one processor, decomposePar made one domain, and mpirun stopped:
    "Number of processor directories = 1 is not equal to the number of ranks = 4"."""
    from axqua.config import Hydrodynamics, OpenFoam
    from axqua.solvers.openfoam import runtime as of_runtime
    from axqua.solvers.openfoam.backend import BACKEND

    case = tmp_path / "openfoam"
    (case / "constant" / "polyMesh").mkdir(parents=True)
    (case / "constant" / "polyMesh" / "faces").write_text("")
    (case / "system").mkdir()
    (case / "system" / "decomposeParDict").write_text("numberOfSubdomains 1;\n")
    seen = {}

    class _Runtime:
        def __init__(self, _of):
            pass

        def check_mesh(self, _case):
            return True, [], None

        def decompose(self, case_dir, n_processors=None):
            seen["decomposed for"] = n_processors

        def run_stage(self, *a, n_processors=None, **k):
            seen.setdefault("ranks", []).append(n_processors)
            return SimpleNamespace(returncode=0)

        def reconstruct(self, _case):
            seen["reconstructed"] = True

    monkeypatch.setattr(of_runtime, "OpenFoamRuntime", _Runtime)
    cfg = SimpleNamespace(openfoam=OpenFoam(n_processors=1),
                          hydrodynamics=Hydrodynamics(), openfoam_case_dir=case,
                          log_file="axqua.log")
    BACKEND.run(cfg, None, n_processors=4)
    assert seen["decomposed for"] == 4
    assert set(seen["ranks"]) == {4} and seen["reconstructed"]
    assert cfg.openfoam.n_processors == 1                  # the case is not changed


def _runtime(tmp_path, monkeypatch, case_file_names=1):
    """An OpenFoamRuntime whose commands are recorded. Activating a stage writes its startFrom, and decomposePar leaves what the real one leaves: one processor folder per domain with the one top-level time the active controlDict starts from (checked with v2406 on 2026-10-18)."""
    from axqua.config import OpenFoam
    from axqua.solvers.openfoam.runtime import OpenFoamRuntime

    tmp_path.mkdir(parents=True, exist_ok=True)
    bashrc = tmp_path / "bashrc.sh"
    bashrc.write_text("# stub\n")
    case = tmp_path / "case"
    (case / "system").mkdir(parents=True)
    (case / "0").mkdir()
    (case / "system" / "decomposeParDict").write_text(
        f"numberOfSubdomains {case_file_names};\n")
    (case / "system" / "controlDict").write_text("startFrom startTime;\n")   # as built
    runtime = OpenFoamRuntime(OpenFoam(bashrc=bashrc, n_processors=case_file_names))
    commands = []

    def times(folder):
        return sorted((p.name for p in folder.iterdir() if p.name[0].isdigit()), key=float)

    def run(command, **kw):
        commands.append(command)
        if command.startswith("decomposePar"):
            for old in case.glob("processor*"):
                shutil.rmtree(old)
            continues = "latestTime" in (case / "system" / "controlDict").read_text()
            carried = times(case)[-1] if continues else "0"
            found = re.search(r"decomposeParDict-(\d+)", command)
            for i in range(int(found.group(1)) if found else case_file_names):
                (case / f"processor{i}" / carried).mkdir(parents=True)
        return SimpleNamespace(returncode=0)

    def activate(case_dir, stage, cfg=None, **_):
        start = "latestTime" if stage == "run" else "startTime"
        (Path(case_dir) / "system" / "controlDict").write_text(f"startFrom {start};\n")

    monkeypatch.setattr(runtime, "run", run)
    monkeypatch.setattr("axqua.solvers.openfoam.dicts.activate", activate)
    return runtime, case, commands


def _stage(runtime, case, stage, n):
    return runtime.run_stage(case, stage, end_time=1.0, n_processors=n, show_progress=False)


def _spun_up(case, time="8.5"):
    """What a spin-up on the split case writes, without reconstructing."""
    for folder in case.glob("processor*"):
        (folder / time).mkdir()


def test_the_runtime_decomposes_for_the_ranks_it_runs_on(tmp_path, monkeypatch):
    """What the backend does, the per-case openfoam_run.py scripts get as well."""
    runtime, case, commands = _runtime(tmp_path, monkeypatch)
    runtime.decompose(case, n_processors=4)                 # the tracked scripts
    _stage(runtime, case, "spinup", 4)
    _spun_up(case)
    _stage(runtime, case, "run", 4)
    assert commands[0] == "decomposePar -force -decomposeParDict system/decomposeParDict-4"
    assert len(commands) == 3 and all(
        c.endswith("-parallel -decomposeParDict system/decomposeParDict-4") for c in commands[1:])
    assert (case / "system" / "decomposeParDict").read_text() == "numberOfSubdomains 1;\n"

    # munich-vsf's script decomposes without its ranks: split again, once, before the first stage
    runtime, case, commands = _runtime(tmp_path / "vsf", monkeypatch)
    runtime.decompose(case)
    _stage(runtime, case, "spinup", 4)
    _spun_up(case)
    _stage(runtime, case, "run", 4)
    assert commands[:2] == ["decomposePar -force",
                            "decomposePar -force -decomposeParDict system/decomposeParDict-4"]
    assert len(commands) == 4


def test_a_case_whose_file_names_the_ranks_is_run_with_it(tmp_path, monkeypatch):
    runtime, case, commands = _runtime(tmp_path, monkeypatch, case_file_names=4)
    runtime.decompose(case, n_processors=4)
    _stage(runtime, case, "run", 4)
    assert commands[0] == "decomposePar -force"
    assert commands[1].endswith("interFoam -parallel")
    assert not list((case / "system").glob("decomposeParDict-*"))


def test_a_stage_continues_from_results_only_the_processor_folders_hold(tmp_path,
                                                                        monkeypatch):
    """The scripts allow a spin-up without reconstructing and the production stage in a second call, which decomposes before its stages: that call used to delete the spin-up."""
    import pytest

    from axqua.core.errors import SolverError

    runtime, case, commands = _runtime(tmp_path, monkeypatch)
    runtime.decompose(case, n_processors=8)
    _stage(runtime, case, "spinup", 8)
    _spun_up(case)
    # the second call of the script
    assert runtime.decompose(case, n_processors=8) is None        # nothing is deleted
    _stage(runtime, case, "run", 8)
    assert sum(c.startswith("decomposePar") for c in commands) == 1
    assert (case / "processor0" / "8.5").is_dir()
    # on other ranks, or on one processor, the stage would not start from the spin-up
    with pytest.raises(SolverError, match="would delete it") as raised:
        _stage(runtime, case, "run", 4)
    assert "on 8 ranks" in raised.value.remedy
    with pytest.raises(SolverError, match="one processor does not read"):
        _stage(runtime, case, "run", 1)
    assert sum(c.startswith("decomposePar") for c in commands) == 1
    # a stage that starts afresh replaces them: a later time left there would be where the next stage continues
    _stage(runtime, case, "spinup", 8)
    assert not (case / "processor0" / "8.5").exists()


def test_a_reconstructed_result_is_split_again_from_its_latest_time(tmp_path, monkeypatch):
    runtime, case, commands = _runtime(tmp_path, monkeypatch, case_file_names=4)
    runtime.decompose(case, n_processors=4)
    _stage(runtime, case, "spinup", 4)
    _spun_up(case, "10")
    (case / "10").mkdir()                                      # reconstructPar
    _stage(runtime, case, "run", 8)                            # other ranks: nothing is lost
    assert commands[-2] == "decomposePar -force -decomposeParDict system/decomposeParDict-8"
    assert (case / "processor7" / "10").is_dir()


def test_a_collated_decomposition_is_counted_by_its_folder(tmp_path):
    from axqua.solvers.openfoam.runtime import Layout, processor_count

    (tmp_path / "processors4" / "0").mkdir(parents=True)
    assert processor_count(tmp_path) == 4 and Layout.of(tmp_path).parallel is None
    (tmp_path / "processors4" / "1.5").mkdir()
    assert Layout.of(tmp_path).only_in_processor_folders
    assert processor_count(tmp_path / "missing") == 0


def test_a_case_keeps_its_own_decomposition_file(tmp_path):
    from axqua.solvers.openfoam import dicts
    from axqua.solvers.openfoam.runtime import _dictionary_option

    system = tmp_path / "system"
    system.mkdir()
    own = "numberOfSubdomains 4;\nmethod hierarchical;\ncoeffs { n (2 2 1); }\n"
    (system / "decomposeParDict").write_text(own)
    assert dicts.decomposition_of(tmp_path) == 4
    assert dicts.decomposition_for(tmp_path, 4) is None        # the user's file is used
    other = dicts.decomposition_for(tmp_path, 6)
    assert other == system / "decomposeParDict-6" and "scotch" in other.read_text()
    assert (system / "decomposeParDict").read_text() == own    # and never changed
    assert _dictionary_option(tmp_path, other) == " -decomposeParDict system/decomposeParDict-6"
    assert _dictionary_option(tmp_path, None) == ""
    # the calibration template is decomposed by others with its own file
    assert dicts.write_decomposition(tmp_path, 4) is False
    assert dicts.write_decomposition(tmp_path, 2) is True
    assert dicts.decomposition_of(tmp_path) == 2
