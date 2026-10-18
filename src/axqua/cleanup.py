"""What a case leaves on disk that can go, and removing it (``axqua cleanup``, *Clean Up* in the plugin).

A case collects files that nothing needs any more: the working folders that TELEMAC leaves when a
run is stopped, the listings of each processor of a parallel run, the processor folders of an
OpenFOAM run that was reconstructed, the results an OpenFOAM run set aside, exports for ParaView and
VisIt. And it collects results with every time step: a steady TELEMAC run of the Isar writes a
result of 1 GB, of which aXqua reads the last time step only. :func:`inventory` lists these per
category, with their size; :func:`apply` removes what was chosen.

**What is never offered:** the input files of a case (``user-sources``), the case file, the built
model (geometry, boundary conditions, steering and friction files, initial conditions), the last
time step of a result, and the processor folders of an OpenFOAM run that was not reconstructed,
which hold the only copy of its results. Only files below the output folders of the case are looked
at, and :func:`apply` acts only on what a new inventory still lists, so an identifier cannot name
another file. Nothing is removed while a job of the case is waiting or running.

**Fewer time steps** rewrites a TELEMAC result (SELAFIN) with the records of the time steps that are
kept, copied byte for byte: the values are not converted, the last time step is the one the file
had. aXqua reads the last time step (the convergence study, the validation, the seed of OpenFOAM),
a hotstart continues from it and QGIS shows it, so all of them keep working; an animation of the
run and the wetting report's look back at earlier time steps lose what was removed.
"""

from __future__ import annotations

import os
import re
import shutil
import struct
from dataclasses import asdict, dataclass, field
from pathlib import Path

#: key -> (title, explanation, chosen by default)
CATEGORIES: dict[str, tuple[str, str, bool]] = {
    "telemac-run-folders": (
        "Working folders of interrupted TELEMAC runs",
        "TELEMAC works in a folder named after the steering file and the start time, and deletes "
        "it when the run ends. A run that was stopped or failed leaves it behind, with the input "
        "files split for every processor.", True),
    "processor-listings": (
        "Listings of the single processors",
        "A parallel TELEMAC run writes one listing per processor besides the main listing. aXqua "
        "reads the main listing only.", True),
    "old-listings": (
        "Listings of earlier runs",
        "The listings (.sortie) of earlier runs of a steering file. The newest one of each steering "
        "file is kept: it is the one aXqua reads.", False),
    "time-steps": (
        "Time steps of TELEMAC results",
        "Rewrites a result with fewer time steps. The last time step is always kept: it is the one "
        "aXqua reads, a hotstart continues from and QGIS shows. An animation of the run loses the "
        "time steps that are removed.", False),
    "openfoam-processors": (
        "Processor folders of reconstructed OpenFOAM runs",
        "A parallel OpenFOAM run writes its results into one folder per processor; reconstructPar "
        "joins them into the time folders of the case. Offered only where the case holds the "
        "latest time itself. A run that continues splits the case again.", True),
    "openfoam-times": (
        "Earlier time folders of OpenFOAM results",
        "The time folders between the start (0) and the latest time, which are kept.", False),
    "openfoam-previous": (
        "OpenFOAM results set aside",
        "A run that starts afresh moves the results of the run before into previous-run.", True),
    "exports": (
        "Exports for ParaView and VisIt",
        "VTK files converted from results. Export makes them again.", True),
}

#: The working folder of a TELEMAC run: <steering file>_<YYYY-MM-DD-HHhMMminSSs>
_RUN_FOLDER = re.compile(r".+\.cas_\d{4}-\d{2}-\d{2}-\d{2}h\d{2}min\d{2}s")
#: The listing of one processor of a parallel run
_PROCESSOR_LISTING = re.compile(r".+_p\d{4,6}\.sortie")
#: The listing of a run: <steering file>_<time>.sortie
_LISTING = re.compile(r"(?P<cas>.+\.cas)_(?P<time>\d{4}-\d{2}-\d{2}-\d{2}h\d{2}min\d{2}s)\.sortie")
_RESULT_SUFFIXES = (".slf", ".srf", ".res", ".sel")
#: SELAFIN files that are inputs and hold one time step anyway; never rewritten.
_INPUT_NAMES = re.compile(r"(geometry|initial-conditions)[^/]*\.slf", re.IGNORECASE)


@dataclass
class Item:
    """One thing that can go."""

    id: str                     # "<action>:<path relative to the case folder>"
    category: str
    action: str                 # "delete" or "thin"
    path: str
    bytes: int                  # its size now
    saves: int                  # what removing it frees; for "thin", keeping the last time step
    detail: str = ""
    frames: int = 0             # "thin": time steps in the file
    header_bytes: int = 0       # "thin": size without any time step
    frame_bytes: int = 0        # "thin": size of one time step
    paths: list[str] = field(default_factory=list)   # what a "delete" of several folders removes

    def as_dict(self) -> dict:
        return asdict(self)


# ----------------------------------------------------------------------------------------------
# SELAFIN records
# ----------------------------------------------------------------------------------------------

@dataclass
class Selafin:
    """Where the records of a SELAFIN file are: the header and each time step."""

    path: Path
    endian: str
    header_end: int             # byte offset of the first time step
    frames: list[tuple[int, int]]   # (offset, length) of each time step
    complete: bool              # False when the last time step was not written completely

    @property
    def frame_bytes(self) -> int:
        return self.frames[0][1] if self.frames else 0


def _records(handle, endian: str, size: int):
    """(offset, length of the record with its markers) of every Fortran record."""
    offset = 0
    while offset < size:
        handle.seek(offset)
        head = handle.read(4)
        if len(head) < 4:
            return
        (length,) = struct.unpack(endian + "i", head)
        total = 8 + length
        if length < 0 or offset + total > size:
            yield offset, -1
            return
        yield offset, total
        offset += total


def read_layout(path: str | Path) -> Selafin:
    """The layout of a SELAFIN file, read from its record markers (the data are not read)."""
    path = Path(path)
    size = path.stat().st_size
    with path.open("rb") as handle:
        first = handle.read(4)
        if len(first) < 4:
            raise ValueError(f"{path} is no SELAFIN file")
        endian = ">" if struct.unpack(">i", first)[0] == 80 else "<"
        if struct.unpack(endian + "i", first)[0] != 80:
            raise ValueError(f"{path} is no SELAFIN file (no title record)")
        records = list(_records(handle, endian, size))
        complete = all(length > 0 for _, length in records)
        records = [r for r in records if r[1] > 0]
        if len(records) < 3:
            raise ValueError(f"{path} is no SELAFIN file")
        handle.seek(records[1][0] + 4)
        nbv1, nbv2 = struct.unpack(endian + "2i", handle.read(8))
        nvar = nbv1 + nbv2
        iparam_record = 2 + nvar
        handle.seek(records[iparam_record][0] + 4)
        iparam = struct.unpack(endian + "10i", handle.read(40))
        n_header = iparam_record + 1 + (1 if iparam[9] == 1 else 0) + 1 + 4   # dims, ikle, ipobo, x, y
    if len(records) < n_header:
        raise ValueError(f"{path} has no complete header")
    per_frame = 1 + nvar
    body = records[n_header:]
    whole = len(body) // per_frame
    frames = []
    for index in range(whole):
        chunk = body[index * per_frame:(index + 1) * per_frame]
        frames.append((chunk[0][0], sum(length for _, length in chunk)))
    complete = complete and len(body) == whole * per_frame
    header_end = body[0][0] if body else size
    return Selafin(path, endian, header_end, frames, complete)


def kept_frames(count: int, keep: str) -> list[int]:
    """Which time steps stay: ``last``, or ``every:N`` (every N-th from the first, and the last)."""
    if count <= 0:
        return []
    if keep == "last":
        return [count - 1]
    match = re.fullmatch(r"every:(\d+)", keep)
    if not match or int(match.group(1)) < 1:
        raise ValueError(f"keep is 'last' or 'every:N', not {keep!r}")
    step = int(match.group(1))
    return sorted(set(range(0, count, step)) | {count - 1})


def thin(path: str | Path, keep: str = "last") -> int:
    """Rewrite a SELAFIN result with the time steps *keep* names; returns the bytes freed.

    The new file is written beside the old one and takes its place only when it is complete, so an
    interruption leaves the result as it was.
    """
    path = Path(path)
    layout = read_layout(path)
    if not layout.complete:
        raise ValueError(f"{path.name}: the last time step was not written completely (a run that "
                         "is still writing, or was stopped while writing); left as it is")
    indices = kept_frames(len(layout.frames), keep)
    if len(indices) == len(layout.frames):
        return 0
    before = path.stat().st_size
    partial = path.with_name(path.name + ".thinning")
    try:
        with path.open("rb") as source, partial.open("wb") as target:
            source.seek(0)
            _copy(source, target, layout.header_end)
            for index in indices:
                offset, length = layout.frames[index]
                source.seek(offset)
                _copy(source, target, length)
        shutil.copystat(path, partial)
        os.replace(partial, path)
    finally:
        if partial.exists():
            partial.unlink()
    return before - path.stat().st_size


def _copy(source, target, length: int, block: int = 1 << 22) -> None:
    while length > 0:
        data = source.read(min(block, length))
        if not data:
            raise OSError("the file ended before its last record")
        target.write(data)
        length -= len(data)


# ----------------------------------------------------------------------------------------------
# Inventory
# ----------------------------------------------------------------------------------------------

def size_of(path: Path) -> int:
    """Bytes in a file, or in a folder and below it (links are not followed)."""
    if path.is_symlink() or path.is_file():
        try:
            return path.lstat().st_size
        except OSError:
            return 0
    total = 0
    for root, _dirs, files in os.walk(path, followlinks=False):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                pass
    return total


def output_folders(cfg) -> list[Path]:
    """The folders a case writes into, the outermost of nested ones only."""
    candidates = [cfg.preprocessing_dir, cfg.model_dir, cfg.postprocessing_dir, cfg.calibration_dir,
                  cfg.openfoam_case_dir, cfg.fine_sediment_case_dir,
                  # the per-case script of the mesh study keeps it beside the phase folders
                  Path(cfg.postprocessing_dir).parent / "mesh-convergence"]
    folders: list[Path] = []
    for folder in sorted({Path(c).resolve() for c in candidates if c}, key=lambda p: len(p.parts)):
        if folder.is_dir() and not any(folder.is_relative_to(f) for f in folders):
            folders.append(folder)
    return folders


def _human(n: float) -> str:
    for unit in ("B", "kB", "MB", "GB", "TB"):
        if abs(n) < 1000 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1000
    return f"{n:.1f} TB"


def _item(base: Path, category: str, action: str, path: Path, size: int, saves: int, detail: str,
          **extra) -> Item:
    rel = os.path.relpath(path, base)
    return Item(id=f"{action}:{rel}", category=category, action=action, path=str(path), bytes=size,
                saves=saves, detail=detail, **extra)


def _openfoam_items(base: Path, case: Path) -> list[Item]:
    from axqua.solvers.openfoam.layout import PREVIOUS_RUN, Layout, time_folders

    items = []
    layout = Layout.of(case)
    if layout.folders and not layout.only_in_processor_folders:
        size = sum(size_of(f) for f in layout.folders)
        items.append(_item(base, "openfoam-processors", "delete", case / "processor*", size, size,
                           f"{len(layout.folders)} folders in {case.name}",
                           paths=[str(f) for f in layout.folders]))
    times = [path for value, path in time_folders(case) if value > 0]
    if len(times) > 1:
        earlier = times[:-1]
        size = sum(size_of(p) for p in earlier)
        items.append(_item(base, "openfoam-times", "delete", case / "<times>", size, size,
                           f"{len(earlier)} of {len(times)} times in {case.name}, "
                           f"{times[-1].name} is kept", paths=[str(p) for p in earlier]))
    previous = case / PREVIOUS_RUN
    if previous.is_dir():
        size = size_of(previous)
        items.append(_item(base, "openfoam-previous", "delete", previous, size, size,
                           f"in {case.name}"))
    vtk = case / "VTK"
    if vtk.is_dir():
        size = size_of(vtk)
        items.append(_item(base, "exports", "delete", vtk, size, size, f"in {case.name}"))
    return items


def _result_item(base: Path, path: Path) -> Item | None:
    if _INPUT_NAMES.fullmatch(path.name):
        return None
    try:
        layout = read_layout(path)
    except (OSError, ValueError, struct.error, IndexError):
        return None
    count = len(layout.frames)
    if count < 3 or not layout.complete:
        return None
    size = path.stat().st_size
    saves = (count - 1) * layout.frame_bytes
    return _item(base, "time-steps", "thin", path, size, saves,
                 f"{count} time steps, {_human(layout.frame_bytes)} each", frames=count,
                 header_bytes=layout.header_end, frame_bytes=layout.frame_bytes)


def inventory(cfg, case_file: str | Path) -> dict:
    """Everything of a case that can go, by category. Reads nothing but sizes and record markers."""
    case_file = Path(case_file).resolve()
    base = case_file.parent
    sources = (base / "user-sources").resolve()
    roots = [root for root in output_folders(cfg) if not root.is_relative_to(sources)]
    items: list[Item] = []
    listings: dict[tuple[Path, str], list[Path]] = {}
    processor_listings: dict[Path, list[Path]] = {}

    for root in roots:
        for folder, dirs, files in os.walk(root, followlinks=False):
            here = Path(folder)
            if (here / "system" / "controlDict").is_file():
                items += _openfoam_items(base, here)
                dirs[:] = []            # the inside of an OpenFOAM case is its own business
                continue
            for name in list(dirs):
                path = here / name
                if _RUN_FOLDER.fullmatch(name):
                    size = size_of(path)
                    items.append(_item(base, "telemac-run-folders", "delete", path, size, size,
                                       "left by a run that did not end"))
                    dirs.remove(name)
                elif name == "vtk" and here.resolve() == Path(cfg.postprocessing_dir).resolve():
                    size = size_of(path)
                    items.append(_item(base, "exports", "delete", path, size, size,
                                       "the export of the TELEMAC results"))
                    dirs.remove(name)
                elif path.is_symlink():
                    dirs.remove(name)
            for name in files:
                path = here / name
                if path.is_symlink():
                    continue
                if _PROCESSOR_LISTING.fullmatch(name):
                    processor_listings.setdefault(here, []).append(path)
                elif (match := _LISTING.fullmatch(name)):
                    listings.setdefault((here, match.group("cas")), []).append(path)
                elif path.suffix.lower() in _RESULT_SUFFIXES:
                    item = _result_item(base, path)
                    if item is not None:
                        items.append(item)

    # listings go by folder: one row per run folder, not one per file
    for folder, paths in processor_listings.items():
        size = sum(size_of(p) for p in paths)
        items.append(_item(base, "processor-listings", "delete", folder / "*_p*.sortie", size, size,
                           f"{len(paths)} files", paths=[str(p) for p in sorted(paths)]))
    older: dict[Path, list[Path]] = {}
    for (folder, _cas), paths in listings.items():
        older.setdefault(folder, []).extend(sorted(paths)[:-1])   # the name sorts by time
    for folder, paths in older.items():
        if paths:
            size = sum(size_of(p) for p in paths)
            items.append(_item(base, "old-listings", "delete", folder / "*.sortie", size, size,
                               f"{len(paths)} files", paths=[str(p) for p in sorted(paths)]))

    categories = []
    for key, (title, explanation, default) in CATEGORIES.items():
        mine = sorted((i for i in items if i.category == key), key=lambda i: -i.saves)
        if mine:
            categories.append({"key": key, "title": title, "explanation": explanation,
                               "default": default, "bytes": sum(i.saves for i in mine),
                               "items": [i.as_dict() for i in mine]})
    return {"case": str(case_file), "folders": [str(r) for r in roots],
            "categories": categories, "bytes": sum(i.saves for i in items),
            "blocked": active_jobs(case_file)}


def active_jobs(case_file: Path) -> str:
    """Why nothing may be removed now ('' when it may): a job of the case is waiting or running."""
    try:
        from axqua.jobs.index import list_jobs
        from axqua.jobs.model import JobState

        rows = list_jobs()
    except Exception:  # noqa: BLE001 - no job index is no job
        return ""
    busy = []
    for row in rows:
        try:
            same = row.source_config and Path(row.source_config).resolve() == case_file.resolve()
            terminal = JobState(row.state).is_terminal if row.state else True
        except (ValueError, OSError):
            continue
        if same and not terminal:
            busy.append(f"{row.job_id} ({row.kind}, {row.state})")
    if not busy:
        return ""
    return ("jobs of this case are waiting or running, whose files would be removed under them: "
            + ", ".join(busy) + ". Clean up when they have ended.")


# ----------------------------------------------------------------------------------------------
# Removal
# ----------------------------------------------------------------------------------------------

def apply(cfg, case_file: str | Path, ids: list[str] = (), categories: list[str] = (),
          keep: str = "last") -> dict:
    """Remove the items *ids* and every item of *categories*, as a new inventory lists them."""
    kept_frames(10, keep)              # refuse a wrong *keep* before anything is removed
    found = inventory(cfg, case_file)
    if found["blocked"]:
        from axqua.core.errors import ConfigError
        raise ConfigError(found["blocked"], subject=str(case_file),
                          remedy="Wait until the jobs of the case have ended, or cancel them.")
    roots = [Path(r) for r in found["folders"]]
    by_id = {item["id"]: item for cat in found["categories"] for item in cat["items"]}
    unknown = [i for i in ids if i not in by_id]
    chosen = [by_id[i] for i in ids if i in by_id]
    chosen += [item for cat in found["categories"] if cat["key"] in categories
               for item in cat["items"] if item not in chosen]
    done, failed, freed = [], [], 0
    for item in chosen:
        try:
            if item["action"] == "thin":
                freed += thin(item["path"], keep)
            else:
                targets = item["paths"] or [item["path"]]
                for target in map(Path, targets):
                    if not any(target.resolve().is_relative_to(root) for root in roots):
                        raise ValueError(f"{target} is not in an output folder of the case")
                    freed += size_of(target)
                    if target.is_dir() and not target.is_symlink():
                        shutil.rmtree(target)
                    else:
                        target.unlink()
            done.append(item["id"])
        except (OSError, ValueError) as exc:
            failed.append({"id": item["id"], "error": str(exc)})
    return {"removed": done, "failed": failed, "unknown": unknown, "freed_bytes": freed,
            "keep": keep}


def human(n: float) -> str:
    """Bytes for a person to read (1.2 GB)."""
    return _human(n)


def thinned_size(item: dict, keep: str) -> int:
    """What a result keeps of its size with *keep*."""
    frames = int(item.get("frames") or 0)
    return int(item.get("header_bytes", 0)) + len(kept_frames(frames, keep)) * int(
        item.get("frame_bytes", 0))


__all__ = ["CATEGORIES", "Item", "apply", "human", "inventory", "kept_frames", "read_layout",
           "thin", "thinned_size"]
