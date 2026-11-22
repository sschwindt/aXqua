"""A TELEMAC case that aXqua did not build, imported for post-processing (``axqua case import``).

A steering file (``.cas``) names its geometry, its boundary conditions and its results, and that is all the post-processing of aXqua needs: the results on the map in QGIS, the export for ParaView and VisIt, the movie and the print layout. :func:`import_steering` writes a case file that points at the folder of the steering file (``project.model_dir``) and names its files (``outputs``), with ``project.imported_from`` set to the steering file (the user's order of 2026-11-20).

Such a case has no terrain model and no outline (``geodata``), so it cannot be built, and :meth:`axqua.config.Config.refuse_if_imported` refuses every job of it: a build or a run would write into the folder of a model that aXqua did not make. Its check (:func:`check`) asks only for the steering file and the geometry file; a result that does not exist yet is a warning (:func:`missing_results`), because the steering file may still be run with TELEMAC.

A SELAFIN file names no coordinate reference system, so the import takes the EPSG code as an argument (the plugin offers the one of the QGIS project). TELEMAC reads the names in a steering file relative to its folder, and so does the case.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

__all__ = ["Steering", "check", "import_steering", "missing_results", "read"]

#: The keywords of a steering file that the case takes over.
KEYWORDS = ("GEOMETRY FILE", "BOUNDARY CONDITIONS FILE", "RESULTS FILE",
            "3D RESULT FILE", "2D RESULT FILE", "NUMBER OF HORIZONTAL LEVELS")


@dataclass(frozen=True)
class Steering:
    """What a steering file names, as written in it (relative to its folder, or absolute)."""

    path: Path
    solver: str                       # telemac2d | telemac3d
    geometry: str
    boundary: str = ""
    results: str = ""                 # TELEMAC-2D: RESULTS FILE
    results3d: str = ""               # TELEMAC-3D: 3D RESULT FILE
    results2d: str = ""               # TELEMAC-3D: 2D RESULT FILE

    @property
    def result_names(self) -> list[str]:
        return [name for name in (self.results, self.results2d, self.results3d) if name]


def _plain(value: str | None) -> str:
    """A file name of a steering file without its quotes."""
    return (value or "").strip().strip("'\"").strip()


def read(cas: str | os.PathLike) -> Steering:
    """What the steering file *cas* names; :class:`ConfigError` where it names no geometry or no result."""
    from axqua.core.errors import ConfigError
    from axqua.solvers.telemac.steering import read_cas_values

    path = Path(cas).expanduser()
    if not path.is_file():
        raise ConfigError(f"there is no steering file {path}", subject=str(path),
                          remedy="Select the .cas file of a TELEMAC case.")
    try:
        values = read_cas_values(path, *KEYWORDS)
    except (OSError, UnicodeError) as exc:
        raise ConfigError(f"{path.name} cannot be read: {exc}", subject=str(path)) from exc
    names = {key: _plain(value) for key, value in values.items()}
    three_d = bool(names["3D RESULT FILE"] or names["NUMBER OF HORIZONTAL LEVELS"])
    steering = Steering(
        path=path.resolve(), solver="telemac3d" if three_d else "telemac2d",
        geometry=names["GEOMETRY FILE"], boundary=names["BOUNDARY CONDITIONS FILE"],
        results="" if three_d else names["RESULTS FILE"],
        results3d=names["3D RESULT FILE"], results2d=names["2D RESULT FILE"])
    if not steering.geometry:
        raise ConfigError(f"{path.name} names no GEOMETRY FILE", subject=str(path),
                          remedy="Select the steering file the TELEMAC case runs with.")
    if not steering.result_names:
        keyword = "3D RESULT FILE or 2D RESULT FILE" if three_d else "RESULTS FILE"
        raise ConfigError(f"{path.name} names no {keyword}, so it has no result to "
                          "post-process", subject=str(path),
                          remedy=f"Add the keyword {keyword} to the steering file.")
    return steering


def _relative(path: Path, base: Path) -> str:
    """*path* as the case file writes it: relative to *base* where it lies in it, so that the case and the model can be moved together; absolute otherwise."""
    try:
        text = os.path.relpath(path, base)
    except ValueError:                       # another drive on Windows
        return str(path)
    return str(path) if text == ".." or text.startswith(".." + os.sep) else text


def import_steering(cas: str | os.PathLike, case_file: str | os.PathLike | None = None,
                    *, name: str = "", crs_epsg: int = 25832) -> dict:
    """Write a case file for the TELEMAC case of the steering file *cas*; returns what was written.

    *case_file* defaults to ``<name of the steering file>.axq-case`` beside it. An
    existing case file is never replaced. The answer: ``{case, steering, solver,
    results: [{name, path, exists}], missing: [...]}``.
    """
    from axqua.config import load_config
    from axqua.core.errors import ConfigError

    steering = read(cas)
    folder = steering.path.parent
    target = Path(case_file).expanduser() if case_file else folder / f"{steering.path.stem}.axq-case"
    if target.suffix != ".axq-case":
        target = target.with_name(target.name + ".axq-case")
    target = target.resolve()
    if target.exists():
        raise ConfigError(f"{target} already exists", subject=str(target),
                          remedy="Choose another name for the case file.")
    base = target.parent
    outputs = {"cas_file": steering.path.name, "geometry_slf": steering.geometry}
    if steering.boundary:
        outputs["boundary_cli"] = steering.boundary
    if steering.solver == "telemac2d":
        outputs["results_slf"] = steering.results
    else:
        if steering.results3d:
            outputs["results3d_slf"] = steering.results3d
        if steering.results2d:
            outputs["results2d_from_3d_slf"] = steering.results2d
    data = {"project": {"name": name or steering.path.stem, "crs_epsg": int(crs_epsg),
                        "model_dir": _relative(folder, base),
                        "imported_from": _relative(steering.path, base)},
            "telemac": {"solver": steering.solver},
            "outputs": outputs}
    _write(target, data, steering.path)
    cfg = load_config(target)
    results = [{"name": result, "path": str(cfg.model_path(result)),
                "exists": cfg.model_path(result).is_file()}
               for result in steering.result_names]
    return {"case": str(target), "steering": str(steering.path), "solver": steering.solver,
            "geometry": str(cfg.model_path(steering.geometry)), "results": results,
            "missing": missing_results(cfg)}


def _write(target: Path, data: dict, cas: Path) -> None:
    import yaml

    header = (f"# aXqua case imported from the TELEMAC steering file {cas.name}\n"
              "# (axqua case import). It is post-processed only: aXqua does not build "
              "or run it.\n")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(header + yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
                         encoding="utf-8")
    temporary.replace(target)


def check(cfg) -> None:
    """The check of an imported case: its steering file and its geometry file exist."""
    for what, path in (("project.imported_from", cfg.imported_from),
                       ("the geometry file of the steering file",
                        cfg.model_path(cfg.geometry_slf))):
        if path is None or not Path(path).is_file():
            raise FileNotFoundError(f"{what} not found: {path}")


def missing_results(cfg) -> list[str]:
    """The results an imported case names that do not exist yet."""
    names = [cfg.results_slf] if cfg.telemac.solver == "telemac2d" else [
        getattr(cfg, "results2d_from_3d_slf", ""), getattr(cfg, "results3d_slf", "")]
    return [str(cfg.model_path(name)) for name in names
            if name and not cfg.model_path(name).is_file()]
