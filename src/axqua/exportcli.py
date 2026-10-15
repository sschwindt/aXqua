"""``axqua export``: TELEMAC results for ParaView and VisIt.

``axqua export <case-file>``            convert every result of the case
``axqua export <case-file> --list``     what the case has, and what is exported already
``axqua export <result.slf>``           convert one file, without a case

QGIS reads the result format of TELEMAC directly; ParaView and VisIt do not. The
conversion (:mod:`axqua.postproc.selafin_vtk`) writes one folder per result with a
``.pvd`` file for ParaView and a ``.visit`` file for VisIt, into the folder ``vtk`` of
the postprocessing folder of the case. An OpenFOAM result needs no conversion: both
programs open the case folder.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from axqua.jobcli import _common, _setup_logging, emit, fail, parse_args

log = logging.getLogger("axqua")

FOLDER = "vtk"


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="axqua export",
        description="Convert TELEMAC results into VTK files that ParaView and VisIt "
                    "open. ParaView opens the .pvd file, VisIt the .visit file.")
    p.add_argument("source", type=Path,
                   help="a case file, or one TELEMAC result file (.slf)")
    p.add_argument("--list", action="store_true", dest="list_only",
                   help="list the results of the case; convert nothing")
    p.add_argument("--result", action="append", default=[], metavar="NAME",
                   help="a result of the case by file name (r2d.slf); may be given "
                        "several times; default: every result that exists")
    p.add_argument("--frames", default="all",
                   help="which time steps: all (default), last, 'every N', or one "
                        "number; a negative number counts from the end")
    p.add_argument("--z", default="flat", dest="zsource",
                   choices=("flat", "bed", "surface"),
                   help="elevation of the nodes of a 2D result: in the plane "
                        "(default; areas and volumes are then those of the model), "
                        "at the bed, or at the water surface")
    p.add_argument("--geometry", type=Path,
                   help="for one result file: the geometry file of its model. A case "
                        "names its own. TELEMAC stores the coordinates of a result in "
                        "single precision, which is half a meter at a UTM northing; "
                        "the geometry file has them exactly")
    p.add_argument("--out", type=Path, help="folder to write into")
    return _common(p)


def _is_result(path: Path) -> bool:
    return path.suffix.lower() in (".slf", ".srf", ".sel", ".res")


def _describe(name: str, label: str, path: Path, out_dir: Path) -> dict:
    """One row of ``--list``: what the file is, and whether it is exported."""
    from axqua.core.selafin import SelafinFile

    row = {"name": name, "label": label, "path": str(path),
           "megabytes": round(path.stat().st_size / 1e6, 1)}
    try:
        slf = SelafinFile(path)
    except (OSError, ValueError) as exc:
        row["error"] = str(exc)
        return row
    row.update(kind="3d" if slf.nplan > 1 else "2d", frames=len(slf),
               t_first=slf.times[0] if slf.times else None,
               t_last=slf.times[-1] if slf.times else None,
               variables=list(slf.var_names), points=slf.npoin, cells=slf.nelem)
    pvd, visit = out_dir / f"{path.stem}.pvd", out_dir / f"{path.stem}.visit"
    exported = pvd.is_file() and visit.is_file()
    row["exported"] = {
        "pvd": str(pvd) if exported else "", "visit": str(visit) if exported else "",
        # A result that was computed again after its export is not what the files show.
        "up_to_date": bool(exported and pvd.stat().st_mtime >= path.stat().st_mtime)}
    return row


def run_export(argv: list[str]) -> int:
    args = parse_args(_parser(), argv, "export")
    _setup_logging(args)
    command = "export"
    try:
        from axqua.core.errors import ConfigError

        source = args.source.expanduser()
        if not source.is_file():
            raise ConfigError(f"{source} does not exist", subject=str(source))

        geometry = args.geometry.expanduser() if args.geometry else None
        if geometry is not None and not geometry.is_file():
            raise ConfigError(f"{geometry} does not exist", subject="geometry")
        if _is_result(source):
            out_dir = (args.out or source.parent / FOLDER).expanduser()
            candidates = [(source.name, "TELEMAC result", source)]
        else:
            from axqua.config import load_config
            from axqua.solvers.telemac import spec

            cfg = load_config(source)
            out_dir = (args.out or Path(cfg.postprocessing_dir) / FOLDER).expanduser()
            if geometry is None:
                model = cfg.model_path(cfg.geometry_slf)
                geometry = model if model.is_file() else None
            candidates = [row for row in spec.result_files(cfg) if row[2].is_file()]
            if args.result:
                known = {name: (name, label, path) for name, label, path in candidates}
                missing = [name for name in args.result if name not in known]
                if missing:
                    raise ConfigError(
                        f"the case has no result {missing[0]}", subject="result",
                        remedy="Its results are: "
                               + (", ".join(known) or "none yet; run a simulation"))
                candidates = [known[name] for name in args.result]

        if args.list_only:
            rows = [_describe(name, label, path, out_dir)
                    for name, label, path in candidates]
            lines = [f"{row['name']}: {row['label']}, {row.get('frames', '?')} time "
                     f"step(s), {row['megabytes']} MB"
                     + (", exported" if (row.get("exported") or {}).get("up_to_date")
                        else "") for row in rows]
            return emit(command, {"folder": str(out_dir), "results": rows},
                        as_json=args.as_json,
                        lines=lines or ["this case has no results yet"])

        if not candidates:
            raise ConfigError("this case has no results to export",
                              subject=str(source),
                              remedy="Run a simulation first.")
        from axqua.postproc.selafin_vtk import export_selafin

        exports = []
        for name, _label, path in candidates:
            log.info("exporting %s", name)
            exports.append(export_selafin(path, out_dir, geometry=geometry,
                                          frames=args.frames,
                                          zsource=args.zsource).as_dict())
        lines = []
        for done in exports:
            lines += [f"{Path(done['source']).name}: {len(done['files'])} time "
                      f"step(s), {done['megabytes']} MB",
                      f"  ParaView: {done['pvd']}", f"  VisIt:    {done['visit']}"]
            lines += [f"  note: {note}" for note in done["notes"]]
        return emit(command, {"folder": str(out_dir), "exports": exports},
                    as_json=args.as_json, lines=lines)
    except BaseException as exc:  # noqa: BLE001
        return fail(command, exc, as_json=args.as_json, verbose=args.verbose)


__all__ = ["run_export"]
