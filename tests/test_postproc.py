"""Post-processing: scene availability, and the generated VisIt script.

None of these need VisIt. The generated script *text* is the only part that can
be tested without it, and it is where the failures that matter live - a missing
``exit(0)`` hangs the subprocess forever, a missing ``outputDirectory`` puts the
PNG somewhere nobody looks, and an unset time slider renders the initial
condition while looking perfectly well-formed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from axqua.postproc import scenes as scene_mod
from axqua.postproc import visit
from axqua.postproc.dataset import Dataset


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
def _vof(tmp_path) -> Dataset:
    """A two-phase case: a real interface to contour."""
    return Dataset(solver="openfoam", kind="openfoam", path=tmp_path / "case.foam",
                   times=(0.0, 10.0, 20.0),
                   fields=frozenset({"U", "alpha.water", "k", "p_rgh"}),
                   patches=frozenset({"bed", "banks", "lid", "inlet-1"}),
                   bounds=(0.0, 100.0, 0.0, 50.0, 370.0, 372.0))


def _rigid(tmp_path) -> Dataset:
    """A rigid-lid case: no alpha.water, because every cell IS water."""
    return Dataset(solver="openfoam", kind="openfoam", path=tmp_path / "case.foam",
                   times=(0.0, 10.0), fields=frozenset({"U", "k"}),
                   patches=frozenset({"bed", "banks", "lid"}),
                   notes=("rigid lid: the free surface is prescribed by the 2D "
                          "seed, not solved",))


def _scene(name):
    return next(s for s in scene_mod.SCENES if s.name == name)


# --------------------------------------------------------------------------- #
# the requirement vocabulary
# --------------------------------------------------------------------------- #
def test_requirements_resolve_against_the_dataset(tmp_path):
    dataset = _vof(tmp_path)
    assert dataset.has("field:U")
    assert dataset.has("patch:lid")
    assert dataset.has("times>1")
    assert dataset.has("solver:openfoam")
    assert not dataset.has("field:nonexistent")


def test_an_unknown_requirement_is_not_silently_satisfied(tmp_path):
    """A scene asking for something the vocabulary cannot express must be reported
    unavailable, not rendered against an assumption."""
    assert not _vof(tmp_path).has("nonsense:whatever")


def test_rigid_lid_reaches_free_surface_through_the_lid_fallback(tmp_path):
    scene = _scene("free-surface")
    rigid = _rigid(tmp_path)
    ok, reason = scene_mod.check(scene, rigid)
    assert ok, reason
    used = scene_mod.resolved_requirements(scene, rigid)
    assert used == ("patch:lid", "field:U")
    # the two-phase case takes the primary path instead
    assert scene_mod.resolved_requirements(_scene("free-surface"), _vof(tmp_path)) \
        == ("field:alpha.water", "field:U")


def test_a_scene_reports_why_it_is_unavailable(tmp_path):
    bare = Dataset(solver="openfoam", kind="openfoam", path=tmp_path / "case.foam")
    ok, reason = scene_mod.check(_scene("free-surface"), bare)
    assert not ok
    assert "alpha.water" in reason


def test_profiles_needs_ground_truth_from_the_case(tmp_path):
    dataset = _vof(tmp_path)
    ok, reason = scene_mod.check(_scene("profiles"), dataset)
    assert not ok and "groundtruth:hydraulics" in reason
    ok, _ = scene_mod.check(_scene("profiles"), dataset,
                            extras=["groundtruth:hydraulics"])
    assert ok


def test_a_telemac_dataset_does_not_get_an_openfoam_only_scene(tmp_path):
    telemac = Dataset(solver="telemac", kind="vtu", path=tmp_path / "r2d.vtu",
                      fields=frozenset({"velocity", "WATER DEPTH"}))
    ok, reason = scene_mod.check(_scene("free-surface"), telemac)
    assert not ok and "not applicable to telemac" in reason
    assert scene_mod.check(_scene("velocity-plan"), telemac)[0]


def test_resolve_rejects_an_unknown_scene_by_name():
    with pytest.raises(ValueError, match="unknown scene"):
        scene_mod.resolve(["not-a-scene"])
    assert [s.name for s in scene_mod.resolve(["profiles"])] == ["profiles"]


def test_plan_reports_every_requested_scene(tmp_path):
    rows = scene_mod.plan([_rigid(tmp_path)])
    assert len(rows) == len(scene_mod.SCENES)
    by_name = {scene.name: (dataset, reason) for scene, dataset, reason in rows}
    assert by_name["free-surface"][0] is not None       # via the lid fallback
    assert by_name["profiles"][0] is None               # no ground truth supplied


# --------------------------------------------------------------------------- #
# the generated script
# --------------------------------------------------------------------------- #
def test_the_script_ends_with_exit(tmp_path):
    """Without it, `visit -cli -nowin` drops into an interactive loop and the
    subprocess hangs forever - the most likely silent failure in this path."""
    text = visit.render_script(_vof(tmp_path), _scene("velocity-plan"), tmp_path)
    assert text.rstrip().endswith("exit(0)")


def test_the_script_sets_the_time_slider(tmp_path):
    """VisIt opens a database on its FIRST state - t=0, where the velocity field is
    identically zero. A figure made without this looks well-formed and shows
    nothing."""
    text = visit.render_script(_vof(tmp_path), _scene("velocity-plan"), tmp_path)
    assert "TimeSliderGetNStates()" in text
    assert "SetTimeSliderState" in text


def test_the_script_saves_where_it_was_told(tmp_path):
    out = tmp_path / "figures"
    text = visit.render_script(_vof(tmp_path), _scene("velocity-plan"), out)
    assert "outputToCurrentDirectory = 0" in text
    assert str(out.resolve()) in text
    assert "SaveWindow()" in text


def test_openfoam_is_opened_through_control_dict(tmp_path):
    """VisIt's OpenFOAM reader cannot open a zero-byte case.foam - measured against
    VisIt 3.5.0, where it reports 0 meshes and 0 variables. ParaView can, which is
    why the Dataset still carries case.foam."""
    dataset = _vof(tmp_path)
    target = visit.visit_target(dataset)
    assert target.name == "controlDict"
    assert target.parent.name == "system"
    assert f"OpenDatabase({str(target)!r})" in visit.render_script(
        dataset, _scene("velocity-plan"), tmp_path)


def test_openfoam_variables_are_mesh_prefixed(tmp_path):
    """A bare '<U>' does not resolve against VisIt's OpenFOAM reader."""
    text = visit.render_script(_vof(tmp_path), _scene("velocity-plan"), tmp_path)
    assert "<internalMesh/U>" in text


def test_a_vtu_dataset_uses_its_own_variable_names(tmp_path):
    telemac = Dataset(solver="telemac", kind="vtu", path=tmp_path / "r2d.vtu",
                      fields=frozenset({"velocity"}))
    text = visit.render_script(telemac, _scene("velocity-plan"), tmp_path)
    assert "<velocity>" in text and "internalMesh" not in text
    # a .vtu is opened directly, with no controlDict indirection
    assert visit.visit_target(telemac).name == "r2d.vtu"


def test_the_rigid_lid_figure_says_the_surface_was_prescribed(tmp_path):
    """Otherwise the picture reads as a computed free surface, which it is not."""
    text = visit.render_script(_rigid(tmp_path), _scene("free-surface"), tmp_path)
    assert "PRESCRIBED" in text
    assert "Isosurface" not in text          # nothing to contour under a rigid lid


def test_the_two_phase_figure_contours_the_interface(tmp_path):
    text = visit.render_script(_vof(tmp_path), _scene("free-surface"), tmp_path)
    assert 'AddOperator("Isosurface")' in text
    # mesh-namespaced: a bare "alpha.water" is rejected by VisIt's OpenFOAM reader
    # with InvalidVariableException, which renders a blank PNG and exits 0
    assert 'iso.variable = "internalMesh/alpha.water"' in text
    assert "contourValue = (0.5,)" in text


def test_a_rigid_lid_case_does_not_advertise_alpha_water(tmp_path):
    """0/alpha.water exists in a rigid-lid case but is uniformly 1, so contouring
    it at 0.5 gives an EMPTY isosurface - a blank figure that looks like a
    successful render. The backend must not offer it as a field."""
    from axqua.solvers.openfoam.backend import OpenFoamBackend

    from axqua.config import OpenFoam

    case = tmp_path / "of"
    (case / "constant" / "polyMesh").mkdir(parents=True)
    (case / "constant" / "polyMesh" / "faces").write_text("")
    (case / "0").mkdir()
    for name in ("U", "alpha.water", "k"):
        (case / "0" / name).write_text("")
    (case / "120").mkdir()

    class _Cfg:                       # only what describe_results reads
        openfoam = OpenFoam()
        openfoam_case_dir = case

    cfg = _Cfg()

    cfg.openfoam.mode = "rigid-lid"
    fields = OpenFoamBackend().describe_results(cfg)[0].fields
    assert "U" in fields and "alpha.water" not in fields

    cfg.openfoam.mode = "vof"
    assert "alpha.water" in OpenFoamBackend().describe_results(cfg)[0].fields


def test_profiles_emits_one_lineout_per_vertical(tmp_path):
    points = [{"id": "01", "x": 10.0, "y": 20.0, "z_bed": 370.0, "z_top": 370.5},
              {"id": "02", "x": 11.0, "y": 21.0, "z_bed": 370.1, "z_top": 370.7}]
    text = visit.render_script(_vof(tmp_path), _scene("profiles"), tmp_path,
                               extras=["groundtruth:hydraulics"], points=points)
    assert text.count("Lineout(") == 2
    # the z span comes from the MESH, NOT from the surveyed bed: on inn-KB15 the
    # ground-truth bed sits ~2.1 m above the model bed, so a survey-referenced
    # lineout misses the water column and every curve comes back empty
    assert 'Query("SpatialExtents")' in text
    assert "(10.0, 20.0, _zmin - _pad), (10.0, 20.0, _zmax + _pad)" in text
    assert "370.0" not in text, "the surveyed z must not drive the lineout"
    assert 'e.filename = "profile-01"' in text
    assert "ExportDatabase" in text


def test_every_path_in_the_script_is_absolute(tmp_path):
    text = visit.render_script(_vof(tmp_path), _scene("velocity-plan"), tmp_path)
    for line in text.splitlines():
        if "OpenDatabase(" in line or "outputDirectory" in line:
            assert "'/" in line or '"/' in line, line


def test_an_unknown_renderer_is_refused(tmp_path):
    from axqua.postproc.scenes import Scene

    bogus = Scene(name="x", title="x", solvers=frozenset({"openfoam"}),
                  requires=(), render="no_such_renderer")
    with pytest.raises(ValueError, match="no renderer named"):
        visit.render_script(_vof(tmp_path), bogus, tmp_path)


# --------------------------------------------------------------------------- #
# runtime, without VisIt installed
# --------------------------------------------------------------------------- #
def test_execute_builds_the_headless_cli_command(tmp_path, monkeypatch):
    from axqua.config import PostProcessing

    seen = {}

    def fake_run(self, command, **kw):
        seen["command"] = command
        import types
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(visit.VisitRuntime, "run", fake_run)
    runtime = visit.VisitRuntime(PostProcessing(visit=None))
    runtime.execute(tmp_path / "scene.py")
    assert "-cli -nowin -s" in seen["command"]
    assert str(tmp_path / "scene.py") in seen["command"]


def test_available_never_raises_for_a_missing_launcher():
    assert visit.available("/definitely/not/here/visit") is None


def test_failure_is_read_from_the_output_not_the_exit_status():
    """VisIt's CLI exit status is not reliable, so the marker is what is trusted."""
    import types

    ok = types.SimpleNamespace(returncode=0, stdout="all fine\n", stderr="")
    assert visit.failed(ok) is None

    marked = types.SimpleNamespace(
        returncode=0, stdout=f"{visit.ERROR_MARKER} could not open the case\n",
        stderr="")
    assert visit.failed(marked) == "could not open the case"

    silent = types.SimpleNamespace(returncode=3, stdout="", stderr="")
    assert "code 3" in visit.failed(silent)


# --------------------------------------------------------------------------- #
# config + architecture
# --------------------------------------------------------------------------- #
def test_a_config_without_a_postproc_block_gets_defaults():
    from axqua.config import PostProcessing

    pp = PostProcessing()
    assert pp.backend == "visit" and pp.visit is None
    assert "profiles" in pp.scenes
    pp.validate()


def test_postproc_validate_rejects_nonsense(tmp_path):
    from axqua.config import PostProcessing

    with pytest.raises(ValueError, match="postproc.backend"):
        PostProcessing(backend="paraview").validate()
    with pytest.raises(FileNotFoundError, match="VisIt launcher not found"):
        PostProcessing(visit=tmp_path / "nope").validate()
    with pytest.raises(ValueError, match="image_width"):
        PostProcessing(image_width=0).validate()


def test_postproc_survives_a_config_round_trip(tmp_path):
    import yaml

    from axqua.config import dump_config, load_config

    source = Path("cases/inn-KB15-2025-hydro/case-config.yml")
    if not source.exists():                      # pragma: no cover - case-dependent
        pytest.skip("KB15 case not present")
    cfg = load_config(source)
    cfg.postproc.scenes = ["velocity-plan"]
    cfg.postproc.image_width = 1234
    out = tmp_path / "round-trip.yml"
    dump_config(cfg, out)
    assert yaml.safe_load(out.read_text())["postproc"]["image_width"] == 1234


def test_postproc_never_imports_a_solver():
    """The whole point of the describe_results seam: figures are drawn from data a
    backend hands over, not by reaching into the backend."""
    import re

    root = Path(__file__).resolve().parent.parent / "src" / "axqua" / "postproc"
    offenders = []
    for path in root.rglob("*.py"):
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if re.match(r"\s*(from|import)\s+axqua\.solvers\b", line):
                offenders.append(f"{path.name}:{number}: {line.strip()}")
    assert not offenders, ("postproc imports a solver backend:\n  "
                           + "\n  ".join(offenders))


def test_base_backend_describes_no_results():
    from axqua.core.registry import BaseBackend

    assert BaseBackend().describe_results(object()) == []


def test_the_script_cannot_fall_through_to_the_interactive_prompt(tmp_path):
    """`visit -cli -nowin` drops to `>>>` on an unhandled exception and waits
    forever. Measured: a NameError outside the per-lineout try/except did exactly
    that, and only an external timeout ended it."""
    text = visit.render_script(_vof(tmp_path), _scene("velocity-plan"), tmp_path)
    assert "sys.excepthook" in text
    assert "def _guard(" in text


def test_the_profile_extent_query_uses_the_visit_35_api(tmp_path):
    """VisIt 3.5 has no GetSpatialExtents(); the extents come from a Query."""
    points = [{"id": "01", "x": 1.0, "y": 2.0, "z_bed": 0.0, "z_top": 1.0}]
    text = visit.render_script(_vof(tmp_path), _scene("profiles"), tmp_path,
                               extras=["groundtruth:hydraulics"], points=points)
    assert 'Query("SpatialExtents")' in text and "GetQueryOutputValue()" in text
    assert "GetSpatialExtents()" not in text


def test_a_dataset_is_not_assumed_hashable(tmp_path):
    """Dataset carries a Mapping (vector_fields), so the frozen dataclass cannot go
    into a set - render() deduped that way and raised TypeError after every figure
    had already been produced."""
    import pytest as _pytest

    with _pytest.raises(TypeError):
        {_vof(tmp_path)}          # noqa: B018 - the behaviour being pinned
