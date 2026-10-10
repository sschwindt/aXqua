"""The example cases: a folder that works on its own, and how it reaches a computer.

The data of an example are not in the installed package. They are read from a source -
a folder or an address - through an index that lists every file with its size and
checksum. These tests use folders and a web server on this computer as sources.
"""

from __future__ import annotations

import functools
import hashlib
import http.server
import importlib.util
import json
import threading
from pathlib import Path

import pytest
import yaml

from axqua.core import examples
from axqua.core.errors import ConfigError
from axqua.core.errors import EnvironmentError as AxquaEnvironmentError
from axqua.examplecli import run_example

REPO = Path(__file__).resolve().parents[1]

FILES = {
    "demo.axq-case": b"project: {name: demo}\n",
    "README.md": b"# Demo\n",
    "user-sources/geodata/terrain.tif": bytes(range(256)) * 300,
    "user-sources/geodata/outline.gpkg": b"outline" * 50,
}


def _index(folder: Path, name: str = "demo", files=FILES) -> None:
    listed = [{"path": path, "bytes": len(content),
               "sha256": hashlib.sha256(content).hexdigest()}
              for path, content in files.items()]
    (folder / "examples.json").write_text(json.dumps({"format": 1, "examples": [
        {"name": name, "title": "A demonstration reach", "summary": "Two files.",
         "case": f"{name}.axq-case", "files": listed}]}))


@pytest.fixture
def source(tmp_path) -> Path:
    """A folder that holds one example and its index."""
    folder = tmp_path / "source"
    for path, content in FILES.items():
        target = folder / "demo" / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    _index(folder)
    return folder


@pytest.fixture
def served(source):
    """The same source behind a web server of this computer."""
    handler = functools.partial(http.server.SimpleHTTPRequestHandler,
                                directory=str(source))
    handler.log_message = lambda *args, **kwargs: None
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def _content(folder: Path) -> dict[str, bytes]:
    return {path.relative_to(folder).as_posix(): path.read_bytes()
            for path in sorted(folder.rglob("*")) if path.is_file()}


def test_an_example_arrives_complete_in_a_folder_of_its_own(source, tmp_path):
    said = []
    done = examples.fetch("demo", tmp_path / "work", source=str(source), say=said.append)
    target = tmp_path / "work" / "demo"
    assert _content(target) == FILES
    assert done == {"name": "demo", "title": "A demonstration reach",
                    "folder": str(target), "case": str(target / "demo.axq-case"),
                    "guide": str(target / "README.md"), "files": 4,
                    "megabytes": round(sum(map(len, FILES.values())) / 1e6, 1),
                    "source": str(source)}
    assert "4 files" in said[0] and said[-1].startswith("[*] 4 of 4")
    assert not list((tmp_path / "work").glob(".*"))        # nothing half-done is left
    # while there is one example, it need not be named
    assert examples.fetch("", tmp_path / "other", source=str(source),
                          say=said.append)["name"] == "demo"


def test_the_same_over_the_network(served, tmp_path):
    source, found = examples.available(served)
    assert source == served
    (example,) = found
    assert example.as_dict() == {
        "name": "demo", "title": "A demonstration reach", "summary": "Two files.",
        "case": "demo.axq-case", "files": 4, "bytes": sum(map(len, FILES.values())),
        "megabytes": 0.1}
    examples.fetch("demo", tmp_path / "work", source=served, say=lambda text: None)
    assert _content(tmp_path / "work" / "demo") == FILES


def test_a_folder_that_is_there_already_is_not_touched(source, tmp_path):
    """Results of the user may be in it."""
    mine = tmp_path / "work" / "demo" / "axqua-case" / "result.slf"
    mine.parent.mkdir(parents=True)
    mine.write_bytes(b"a week of computing")
    with pytest.raises(ConfigError, match="is there already") as caught:
        examples.fetch("demo", tmp_path / "work", source=str(source))
    assert "Choose another folder" in caught.value.remedy
    assert mine.read_bytes() == b"a week of computing"
    assert not (tmp_path / "work" / "demo" / "README.md").exists()
    # an empty folder of that name is no obstacle
    (tmp_path / "empty" / "demo").mkdir(parents=True)
    examples.fetch("demo", tmp_path / "empty", source=str(source), say=lambda text: None)
    assert (tmp_path / "empty" / "demo" / "demo.axq-case").is_file()


def test_a_file_that_does_not_match_the_index_is_not_kept(source, tmp_path):
    """Truncated by a broken connection, or changed after the index was written: an
    example with one wrong file must not look like an example."""
    terrain = source / "demo" / "user-sources/geodata/terrain.tif"
    terrain.write_bytes(terrain.read_bytes()[:-1000])
    with pytest.raises(AxquaEnvironmentError, match="terrain.tif") as caught:
        examples.fetch("demo", tmp_path / "work", source=str(source),
                       say=lambda text: None)
    assert "75800 of 76800 bytes" in str(caught.value)
    assert "Nothing was kept" in str(caught.value)
    assert "index of the source is out of date" in caught.value.remedy
    assert not (tmp_path / "work" / "demo").exists()
    assert not list((tmp_path / "work").glob(".*"))
    # the right length with other content is caught by the checksum
    terrain.write_bytes(b"\x00" * len(FILES["user-sources/geodata/terrain.tif"]))
    with pytest.raises(AxquaEnvironmentError, match="the checksum differs"):
        examples.fetch("demo", tmp_path / "work", source=str(source),
                       say=lambda text: None)
    assert not (tmp_path / "work" / "demo").exists()


@pytest.mark.parametrize("path", ["../outside.txt", "/etc/passwd", "a/../../b",
                                  "C:\\Users\\x", "user-sources\\x.tif", ""])
def test_an_index_cannot_name_a_file_outside_its_example(source, tmp_path, path):
    _index(source, files={**FILES, path: b"x"})
    with pytest.raises(ConfigError, match="outside its example"):
        examples.fetch("demo", tmp_path / "work", source=str(source))
    assert not (tmp_path / "work").exists()


def test_an_index_of_another_kind_is_said_to_be_one(source):
    (source / "examples.json").write_text(json.dumps({"format": 7, "examples": []}))
    with pytest.raises(ConfigError, match="not an index of example cases"):
        examples.available(str(source))
    (source / "examples.json").write_text("not json")
    with pytest.raises(ConfigError, match="cannot be read"):
        examples.available(str(source))


def test_an_unknown_example_is_answered_with_the_ones_there_are(source, tmp_path):
    with pytest.raises(ConfigError, match="no example case 'isar'") as caught:
        examples.fetch("isar", tmp_path, source=str(source))
    assert "demo" in caught.value.remedy


def test_where_the_examples_are_looked_for(monkeypatch, tmp_path):
    import axqua

    monkeypatch.delenv(examples.ENV_SOURCE, raising=False)
    monkeypatch.setattr(examples, "checkout_cases", lambda: None)
    monkeypatch.setattr(axqua, "__version__", "1.2.3", raising=False)
    # an installed release: its own tag first, the main branch if that is not there
    assert examples.sources() == [f"{examples.GITHUB}/v1.2.3/cases",
                                  f"{examples.GITHUB}/main/cases"]
    # a version that is no release has no tag
    monkeypatch.setattr(axqua, "__version__", "1.2.3.dev4+g1a2b3c", raising=False)
    assert examples.sources() == [f"{examples.GITHUB}/main/cases"]
    # a source checkout comes before the network
    monkeypatch.setattr(examples, "checkout_cases", lambda: tmp_path)
    assert examples.sources()[0] == str(tmp_path)
    # and what the user names is the only source
    monkeypatch.setenv(examples.ENV_SOURCE, "/data/examples")
    assert examples.sources() == ["/data/examples"]
    assert examples.sources("https://example.org/x") == ["https://example.org/x"]


def test_a_checkout_without_the_data_is_passed_over(source, tmp_path, monkeypatch):
    """A clone may carry the case file and the guide of an example without its data.
    Copying that would produce a case that cannot be built."""
    bare = tmp_path / "bare"
    (bare / "demo").mkdir(parents=True)
    (bare / "demo" / "demo.axq-case").write_bytes(FILES["demo.axq-case"])
    _index(bare)
    monkeypatch.setattr(examples, "sources", lambda explicit="": [str(bare), str(source)])
    done = examples.fetch("demo", tmp_path / "work", say=lambda text: None)
    assert done["source"] == str(source)
    assert _content(tmp_path / "work" / "demo") == FILES


def test_no_source_at_all_names_what_was_tried(tmp_path, monkeypatch, served):
    nowhere = tmp_path / "nowhere"
    monkeypatch.setattr(examples, "sources",
                        lambda explicit="": [str(nowhere), served + "/missing"])
    with pytest.raises(AxquaEnvironmentError) as caught:
        examples.fetch("demo", tmp_path / "work")
    message = str(caught.value)
    assert str(nowhere) in message and "/missing (no examples.json)" in message
    assert examples.ENV_SOURCE in caught.value.remedy
    with pytest.raises(AxquaEnvironmentError):
        examples.available()


def test_the_command_lists_and_gets(source, tmp_path, capsys):
    assert run_example(["list", "--source", str(source), "--json"]) == 0
    answer = json.loads(capsys.readouterr().out)
    assert answer["command"] == "example.list"
    assert [item["name"] for item in answer["data"]["examples"]] == ["demo"]

    assert run_example(["get", "demo", "--folder", str(tmp_path / "work"), "--source",
                        str(source), "--json"]) == 0
    captured = capsys.readouterr()
    answer = json.loads(captured.out)                      # progress is not in the way
    assert answer["data"]["case"] == str(tmp_path / "work" / "demo" / "demo.axq-case")
    assert "[*] 4 of 4" in captured.err

    assert run_example(["get", "demo", "--folder", str(tmp_path / "work"), "--source",
                        str(source), "--json"]) != 0
    answer = json.loads(capsys.readouterr().out)
    assert answer["error"]["code"] == "axqua.config"
    assert answer["error"]["subject"] == "folder"


# ----------------------------------------------- the examples of this repository


def _script():
    spec = importlib.util.spec_from_file_location(
        "build_example_data", REPO / "scripts" / "build_example_data.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _paths(value):
    """Every text in a case file that names a file."""
    if isinstance(value, dict):
        for item in value.values():
            yield from _paths(item)
    elif isinstance(value, list):
        for item in value:
            yield from _paths(item)
    elif isinstance(value, str) and ("/" in value or value.endswith(
            (".gpkg", ".tif", ".csv", ".shp", ".xlsx"))):
        yield value


def test_the_index_of_this_repository_is_what_its_folders_hold():
    """``scripts/build_example_data.py --index`` writes it. A guide that was edited
    without it would be refused by every download as a file that arrived changed."""
    index = REPO / "cases" / examples.INDEX
    found = examples.read_index(str(REPO / "cases"))
    assert found, "no example case is listed"
    script = _script()
    assert sorted(example.name for example in found) == sorted(script.EXAMPLES)
    for example in found:
        folder = REPO / "cases" / example.name
        listed = {path for path, _, _ in example.files}
        assert example.case in listed and "README.md" in listed
        for path, size, digest in example.files:
            file = folder / path
            assert file.is_file(), f"{example.name}/{path} is listed and not there"
            assert file.stat().st_size == size, f"{path}: run the script with --index"
            assert hashlib.sha256(file.read_bytes()).hexdigest() == digest, \
                f"{path}: run 'python scripts/build_example_data.py --index'"
    assert json.loads(index.read_text()) == script.index()


def test_every_tracked_file_of_an_example_is_in_the_index():
    """The example is downloaded file by file from this repository. A file that is
    committed to its folder and missing in the index would never reach a user."""
    import subprocess

    for example in examples.read_index(str(REPO / "cases")):
        listed = {path for path, _, _ in example.files}
        answer = subprocess.run(["git", "ls-files", f"cases/{example.name}"], cwd=REPO,
                                capture_output=True, text=True, check=False)
        if answer.returncode:
            pytest.skip("not a git checkout")
        prefix = f"cases/{example.name}/"
        tracked = {line[len(prefix):] for line in answer.stdout.splitlines()}
        assert tracked, f"nothing of {example.name} is tracked"
        assert tracked == listed, sorted(tracked ^ listed)


def test_the_data_of_an_example_carry_their_license():
    """Reuse on citing aXqua, no liability: the terms travel with the folder."""
    for example in examples.read_index(str(REPO / "cases")):
        listed = {path for path, _, _ in example.files}
        assert {"LICENSE.md", "LICENSE-CC-BY-4.0.txt"} <= listed
        folder = REPO / "cases" / example.name
        summary = (folder / "LICENSE.md").read_text(encoding="utf-8")
        assert "Creative Commons Attribution 4.0 International" in summary
        assert "https://github.com/sschwindt/aXqua" in summary          # the citation
        assert "not liable" in summary
        terms = (folder / "LICENSE-CC-BY-4.0.txt").read_text(encoding="utf-8")
        assert terms.startswith("Attribution 4.0 International")
        assert "Disclaimer of Warranties and Limitation of Liability" in terms


def test_an_example_reads_nothing_outside_its_folder():
    """A downloaded example is a folder somewhere on a computer. A path that leaves
    it, or a file the index does not list, is a case that cannot be built there."""
    for example in examples.read_index(str(REPO / "cases")):
        listed = {path for path, _, _ in example.files}
        case = yaml.safe_load((REPO / "cases" / example.name / example.case).read_text())
        named = sorted(set(_paths(case)))
        assert named, example.name
        for path in named:
            assert not path.startswith(("..", "/", "~")), f"{example.name}: {path}"
            assert path in listed, f"{example.name}: {path} is not in the index"


def test_the_terrain_of_an_example_is_cut_without_changing_the_model(tmp_path):
    """aXqua cuts the terrain to the outline of the model first. The reduced file must
    give that very cut: the same cells, and values within what 32 bits resolve."""
    np = pytest.importorskip("numpy")
    rasterio = pytest.importorskip("rasterio")
    gpd = pytest.importorskip("geopandas")
    from rasterio.transform import from_origin
    from shapely.geometry import Polygon

    from axqua.dem import clip_to_roi

    rng = np.random.default_rng(1)
    values = 800.0 + rng.random((240, 400)) * 30.0             # full 64-bit noise
    values[:, :40] = 0.0                                       # outside the survey
    original = tmp_path / "survey.tif"
    with rasterio.open(original, "w", driver="GTiff", height=240, width=400, count=1,
                       dtype="float64", crs="EPSG:25832",
                       transform=from_origin(676900.13, 5268000.0, 0.25, 0.25)) as dst:
        dst.write(values, 1)
    outline = tmp_path / "outline.gpkg"
    gpd.GeoDataFrame(geometry=[Polygon([(676925.0, 5267950.0), (676980.0, 5267946.0),
                                        (676985.0, 5267985.0), (676930.0, 5267990.0)])],
                     crs="EPSG:25832").to_file(outline, driver="GPKG")

    reduced = tmp_path / "reduced.tif"
    done = _script().reduce_terrain(original, outline, reduced, margin=5.0)
    assert done["deviation"] < 4e-5                            # 0.04 mm at 830 m
    assert 30_000 < done["kept"] < 0.6 * done["cells"]         # the outline and 5 m
    assert reduced.stat().st_size < original.stat().st_size / 2
    with rasterio.open(reduced) as src:
        assert src.dtypes[0] == "float32" and src.nodata == -9999.0
        assert src.res == (0.25, 0.25) and src.crs.to_epsg() == 25832

    first = clip_to_roi(original, outline, tmp_path / "cut-original.tif")
    second = clip_to_roi(reduced, outline, tmp_path / "cut-reduced.tif")
    with rasterio.open(first) as a, rasterio.open(second) as b:
        assert a.transform == b.transform and a.shape == b.shape     # no cell moved
        za, zb = a.read(1), b.read(1)
        inside = za != a.nodata
        assert (inside == (zb != b.nodata)).all() and inside.sum() > 10_000
        assert np.abs(za[inside] - zb[inside]).max() < 4e-5
