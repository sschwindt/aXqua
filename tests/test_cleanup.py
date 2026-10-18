"""Clean Up: what a case leaves on disk that can go (axqua.cleanup, axqua cleanup)."""
from __future__ import annotations

import json
import os
import struct
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from axqua import cleanup


def _record(handle, payload: bytes) -> None:
    handle.write(struct.pack(">i", len(payload)) + payload + struct.pack(">i", len(payload)))


def write_result(path: Path, frames: int, *, npoin: int = 4, partial: bool = False) -> list[np.ndarray]:
    """A SELAFIN result of two variables written the way TELEMAC writes one (big-endian, single precision); returns the values of each time step."""
    values = []
    with path.open("wb") as handle:
        _record(handle, b"test result".ljust(80))
        _record(handle, struct.pack(">2i", 2, 0))
        _record(handle, b"WATER DEPTH     M               ")
        _record(handle, b"VELOCITY U      M/S             ")
        _record(handle, struct.pack(">10i", 1, 0, 0, 0, 0, 0, 0, 0, 0, 0))
        _record(handle, struct.pack(">4i", 2, npoin, 3, 1))
        _record(handle, struct.pack(">6i", 1, 2, 3, 1, 3, 4))
        _record(handle, struct.pack(f">{npoin}i", *range(1, npoin + 1)))
        _record(handle, struct.pack(f">{npoin}f", *np.arange(npoin, dtype=float)))
        _record(handle, struct.pack(f">{npoin}f", *np.zeros(npoin)))
        for step in range(frames):
            _record(handle, struct.pack(">f", 10.0 * step))
            frame = np.random.default_rng(step).random((2, npoin)).astype(">f4")
            values.append(frame)
            for row in frame:
                _record(handle, row.tobytes())
        if partial:
            _record(handle, struct.pack(">f", 10.0 * frames))
            handle.write(struct.pack(">i", npoin * 4) + b"\0\0")   # cut off while writing
    return values


def test_the_layout_of_a_result_is_read_from_its_record_markers(tmp_path):
    path = tmp_path / "r2d.slf"
    write_result(path, 5)
    layout = cleanup.read_layout(path)
    assert len(layout.frames) == 5 and layout.complete
    assert layout.frame_bytes == (4 + 8) + 2 * (16 + 8)
    assert layout.header_end + 5 * layout.frame_bytes == path.stat().st_size


@pytest.mark.parametrize("keep, expected", [("last", [6]), ("every:3", [0, 3, 6]),
                                            ("every:4", [0, 4, 6]), ("every:1", list(range(7)))])
def test_which_time_steps_are_kept(keep, expected):
    assert cleanup.kept_frames(7, keep) == expected


def test_a_wrong_choice_of_time_steps_is_refused():
    with pytest.raises(ValueError):
        cleanup.kept_frames(7, "first")


def test_removing_time_steps_copies_the_kept_ones_byte_for_byte(tmp_path):
    from axqua.core.selafin import read_slf

    path = tmp_path / "r2d.slf"
    values = write_result(path, 6)
    original = path.read_bytes()
    layout = cleanup.read_layout(path)
    freed = cleanup.thin(path, "last")
    assert freed == 5 * layout.frame_bytes
    thinned = path.read_bytes()
    assert thinned[:layout.header_end] == original[:layout.header_end]
    assert thinned[layout.header_end:] == original[layout.frames[-1][0]:]
    result = read_slf(path)          # what aXqua reads of a result is still there
    assert result["time"] == pytest.approx(50.0)
    np.testing.assert_array_equal(np.asarray(result["values"]["WATER DEPTH"], dtype=float),
                                  values[-1][0].astype(float))
    assert cleanup.thin(path, "last") == 0          # nothing left to remove


def test_a_result_whose_last_time_step_is_incomplete_is_left_as_it_is(tmp_path):
    path = tmp_path / "r2d.slf"
    write_result(path, 4, partial=True)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="not written completely"):
        cleanup.thin(path)
    assert path.read_bytes() == before


def _case(tmp_path: Path):
    """A case folder with the leftovers of every category, and a cfg that names its folders."""
    case_file = tmp_path / "reach.axq-case"
    case_file.write_text("project: {}\n")
    (tmp_path / "user-sources").mkdir()
    write_result(tmp_path / "user-sources" / "old-result.slf", 5)   # an input: never offered
    out = tmp_path / "axqua-case"
    sim = out / "simulation"
    sim.mkdir(parents=True)
    write_result(sim / "geometry.slf", 1)
    write_result(sim / "initial-conditions.slf", 3)
    write_result(sim / "r2d.slf", 8)
    write_result(sim / "r2d-short.slf", 2)
    run = sim / "steady2d.cas_2026-01-01-10h00min00s"
    run.mkdir()
    (run / "T2DGEO00001-00000").write_bytes(b"x" * 1000)
    for name in ("steady2d.cas_2026-01-01-10h00min00s.sortie",
                 "steady2d.cas_2026-01-02-10h00min00s.sortie",
                 "steady2d.cas_2026-01-02-10h00min00s_p00001.sortie",
                 "steady2d.cas_2026-01-02-10h00min00s_p00002.sortie"):
        (sim / name).write_text("listing\n")
    post = out / "postprocessing"
    (post / "vtk").mkdir(parents=True)
    (post / "vtk" / "r2d.pvd").write_text("<VTKFile/>")
    foam = out / "openfoam"
    (foam / "system").mkdir(parents=True)
    (foam / "system" / "controlDict").write_text("startFrom latestTime;\n")
    for value in ("0", "1", "2"):
        (foam / value).mkdir()
        (foam / value / "U").write_text("u" * 100)
    for rank in ("processor0", "processor1"):
        for value in ("0", "2"):
            (foam / rank / value).mkdir(parents=True)
            (foam / rank / value / "U").write_text("u" * 50)
    (foam / "previous-run" / "5").mkdir(parents=True)
    (foam / "previous-run" / "5" / "U").write_text("u" * 10)
    cfg = SimpleNamespace(preprocessing_dir=out / "preprocessing", model_dir=sim,
                          postprocessing_dir=post, calibration_dir=out / "calibration-validation",
                          openfoam_case_dir=foam, fine_sediment_case_dir=out / "openfoam-sediment")
    _age(tmp_path)
    return cfg, case_file


def _age(folder: Path, seconds: float = 3600) -> None:
    """Date every file back: what was written in the last minutes is not offered (a run may be using it)."""
    then = time.time() - seconds
    for root, dirs, files in os.walk(folder):
        for name in files + dirs:
            os.utime(os.path.join(root, name), (then, then))


def _items(found):
    return {item["id"]: item for cat in found["categories"] for item in cat["items"]}


def test_the_inventory_lists_the_leftovers_of_a_case_and_nothing_it_needs(tmp_path):
    cfg, case_file = _case(tmp_path)
    found = cleanup.inventory(cfg, case_file)
    items = _items(found)
    keys = {cat["key"] for cat in found["categories"]}
    assert keys == {"telemac-run-folders", "processor-listings", "old-listings", "time-steps",
                    "openfoam-processors", "openfoam-times", "openfoam-previous", "exports"}
    assert set(items) >= {
        "delete:axqua-case/simulation/steady2d.cas_2026-01-01-10h00min00s",
        "thin:axqua-case/simulation/r2d.slf",
        "delete:axqua-case/postprocessing/vtk",
        "delete:axqua-case/openfoam/previous-run",
    }
    offered = " ".join(items)
    for needed in ("geometry.slf", "initial-conditions.slf", "r2d-short.slf", "user-sources",
                   "controlDict"):
        assert needed not in offered
    old = items["delete:axqua-case/simulation/*.sortie"]
    assert [Path(p).name for p in old["paths"]] == ["steady2d.cas_2026-01-01-10h00min00s.sortie"]
    times = items["delete:axqua-case/openfoam/<times>"]
    assert [Path(p).name for p in times["paths"]] == ["1"]     # 0 and the latest stay
    assert found["blocked"] == ""


def test_processor_folders_that_hold_the_only_copy_of_a_result_are_not_offered(tmp_path):
    cfg, case_file = _case(tmp_path)
    for rank in ("processor0", "processor1"):
        (cfg.openfoam_case_dir / rank / "3").mkdir()       # a later time than the case has
    items = _items(cleanup.inventory(cfg, case_file))
    assert not any(item["category"] == "openfoam-processors" for item in items.values())


def test_removal_takes_what_was_chosen_and_thins_results(tmp_path):
    cfg, case_file = _case(tmp_path)
    sim = cfg.model_dir
    done = cleanup.apply(cfg, case_file,
                         ids=["thin:axqua-case/simulation/r2d.slf", "delete:nowhere/else"],
                         categories=["telemac-run-folders", "openfoam-processors"], keep="every:4")
    assert sorted(done["removed"]) == sorted([
        "thin:axqua-case/simulation/r2d.slf",
        "delete:axqua-case/simulation/steady2d.cas_2026-01-01-10h00min00s",
        "delete:axqua-case/openfoam/processor*"])
    assert done["unknown"] == ["delete:nowhere/else"] and done["failed"] == []
    assert len(cleanup.read_layout(sim / "r2d.slf").frames) == 3      # 0, 4 and the last, 7
    assert not (sim / "steady2d.cas_2026-01-01-10h00min00s").exists()
    assert not list(cfg.openfoam_case_dir.glob("processor*"))
    assert (cfg.openfoam_case_dir / "2" / "U").exists()
    assert (tmp_path / "user-sources" / "old-result.slf").exists()
    assert done["freed_bytes"] > 0


def test_nothing_is_removed_while_a_job_of_the_case_runs(tmp_path, monkeypatch):
    from axqua.core.errors import ConfigError

    cfg, case_file = _case(tmp_path)
    monkeypatch.setattr(cleanup, "active_jobs", lambda case, cfg=None: "a job of this case is running")
    with pytest.raises(ConfigError, match="running"):
        cleanup.apply(cfg, case_file, categories=["exports"])
    assert (cfg.postprocessing_dir / "vtk").exists()


def test_a_job_of_the_case_that_has_not_ended_blocks_the_removal(tmp_path, monkeypatch):
    from axqua.jobs import index

    case_file = tmp_path / "reach.axq-case"
    case_file.write_text("")
    rows = [index.JobRow(job_id="J1", root=str(tmp_path), kind="steady", state="RUNNING",
                         source_config=str(case_file)),
            index.JobRow(job_id="J2", root=str(tmp_path), kind="steady", state="COMPLETED",
                         source_config=str(case_file)),
            index.JobRow(job_id="J3", root=str(tmp_path), kind="steady", state="QUEUED",
                         source_config=str(tmp_path / "other.axq-case"))]
    monkeypatch.setattr(index, "list_jobs", lambda **kw: rows)
    reason = cleanup.active_jobs(case_file)
    assert "J1" in reason and "J2" not in reason and "J3" not in reason


def test_what_a_run_outside_the_job_system_still_uses_is_not_offered(tmp_path, monkeypatch):
    """Review of 2026-10-18: a run started from a terminal is no job, and its working folder was offered (ticked) and its result thinned under it."""
    cfg, case_file = _case(tmp_path)
    sim = cfg.model_dir
    run = sim / "steady2d.cas_2026-01-01-10h00min00s"
    # a process works in the folder of the run; the result was written a minute ago
    monkeypatch.setattr(cleanup, "open_paths", lambda: {str(run.resolve())})
    os.utime(sim / "r2d.slf", (time.time() - 60, time.time() - 60))
    found = cleanup.inventory(cfg, case_file)
    offered = " ".join(_items(found))
    assert "steady2d.cas_2026-01-01" not in offered and "r2d.slf" not in offered
    reasons = {b["path"]: b["reason"] for b in found["in_use"]}
    assert "works in it" in reasons["axqua-case/simulation/steady2d.cas_2026-01-01-10h00min00s"]
    assert "min ago" in reasons["axqua-case/simulation/r2d.slf"]
    done = cleanup.apply(cfg, case_file, ids=["thin:axqua-case/simulation/r2d.slf"],
                         categories=["telemac-run-folders"])
    assert done["removed"] == [] and done["unknown"] == ["thin:axqua-case/simulation/r2d.slf"]
    assert run.is_dir() and len(cleanup.read_layout(sim / "r2d.slf").frames) == 8


def test_the_processes_of_this_computer_are_read_from_proc():
    if not Path("/proc/self/cwd").exists():
        pytest.skip("no /proc")
    with open(__file__, "rb"):
        seen = cleanup.open_paths()
        assert os.getcwd() in seen and str(Path(__file__).resolve()) in seen


def test_the_inputs_are_never_walked_into_even_where_an_output_folder_contains_them(tmp_path):
    """Review of 2026-10-18: with the case folder itself as an output folder, an input result of five time steps in user-sources was offered for thinning."""
    cfg, case_file = _case(tmp_path)
    cfg.model_dir = tmp_path                          # results beside the case file
    (tmp_path / "user-sources" / "old.cas_2026-01-01-10h00min00s").mkdir()
    _age(tmp_path)
    found = cleanup.inventory(cfg, case_file)
    assert not any("user-sources" in item_id for item_id in _items(found))
    assert "thin:axqua-case/simulation/r2d.slf" in _items(found)


def test_an_output_folder_that_contains_the_case_folder_is_not_looked_at(tmp_path):
    (tmp_path / "isar").mkdir()
    cfg, case_file = _case(tmp_path / "isar")
    other = tmp_path / "inn" / "axqua-case" / "simulation"
    other.mkdir(parents=True)
    write_result(other / "r2d.slf", 6)
    _age(tmp_path)
    cfg.calibration_dir = tmp_path                    # the folder of all cases
    found = cleanup.inventory(cfg, case_file)
    assert str(tmp_path.resolve()) not in found["folders"]
    assert not any("inn" in item["path"] for item in _items(found).values())


def test_a_job_submitted_with_a_relative_path_or_holding_the_folder_blocks_the_removal(tmp_path, monkeypatch):
    """Review of 2026-10-18: a job submitted as ``axqua submit cases/isar/isar.axq-case`` recorded the relative path, which the cleanup resolved in its own working folder and did not match."""
    from axqua.jobs import index, workspace

    cfg, case_file = _case(tmp_path)
    rows = [index.JobRow(job_id="J1", root=str(tmp_path), kind="steady", state="RUNNING",
                         source_config="cases/somewhere/reach.axq-case")]
    monkeypatch.setattr(index, "list_jobs", lambda **kw: rows)
    assert "J1" in cleanup.active_jobs(case_file, cfg)
    rows.clear()
    assert cleanup.active_jobs(case_file, cfg) == ""
    monkeypatch.setattr(workspace, "holder_of", lambda folder: "J7")
    monkeypatch.setattr(workspace, "waiting_in", lambda folder: ["J7", "J8"])
    reason = cleanup.active_jobs(case_file, cfg)
    assert "J7 (works in simulation)" in reason and "J8 (waits for simulation)" in reason


def test_a_list_of_jobs_that_cannot_be_read_blocks_the_removal(tmp_path, monkeypatch):
    from axqua.core.errors import ConfigError
    from axqua.jobs import index

    cfg, case_file = _case(tmp_path)

    def broken(**kw):
        raise RuntimeError("database disk image is malformed")
    monkeypatch.setattr(index, "list_jobs", broken)
    assert "could not be read" in cleanup.active_jobs(case_file, cfg)
    with pytest.raises(ConfigError, match="could not be read"):
        cleanup.apply(cfg, case_file, categories=["exports"])
    assert (cfg.postprocessing_dir / "vtk").exists()


def test_a_job_records_the_full_path_of_its_case_file(tmp_path, monkeypatch):
    from axqua import jobcli
    from axqua.jobs import submit

    seen = {}

    def record(cfg, kind, **kw):
        seen.update(kw)
        raise RuntimeError("stop here")
    monkeypatch.setattr(submit, "submit_job", record)
    monkeypatch.setattr("axqua.config.load_config", lambda path: SimpleNamespace(name="reach"))
    monkeypatch.chdir(tmp_path)
    jobcli.run_submit(["reach.axq-case", "--kind", "steady", "--json"])
    assert seen["source_config"] == str((tmp_path / "reach.axq-case").resolve())


def test_the_command_lists_as_json_and_removes_by_identifier(tmp_path, monkeypatch, capsys):
    import axqua.config
    from axqua import cli

    cfg, case_file = _case(tmp_path)
    monkeypatch.setattr(axqua.config, "load_config", lambda path: cfg)
    assert cli.main(["cleanup", str(case_file), "--json"]) == 0
    listing = json.loads(capsys.readouterr().out)
    assert listing["ok"] and listing["data"]["bytes"] > 0
    assert cli.main(["cleanup", str(case_file), "--remove", "delete:axqua-case/postprocessing/vtk",
                     "--json"]) == 0
    done = json.loads(capsys.readouterr().out)["data"]
    assert done["removed"] == ["delete:axqua-case/postprocessing/vtk"]
    assert not (cfg.postprocessing_dir / "vtk").exists()


def test_the_size_a_thinned_result_keeps(tmp_path):
    item = {"frames": 10, "header_bytes": 100, "frame_bytes": 50}
    assert cleanup.thinned_size(item, "last") == 150
    assert cleanup.thinned_size(item, "every:5") == 100 + 3 * 50      # 0, 5 and 9
