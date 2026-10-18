"""What a case leaves on disk that can go, and removing it (``axqua cleanup``, *Clean Up* in the plugin).

A case collects files that nothing needs any more: the working folders that TELEMAC leaves when a run is stopped, the listings of each processor of a parallel run, the processor folders of an OpenFOAM run that was reconstructed, the results an OpenFOAM run set aside, exports for ParaView and VisIt. And it collects results with every time step: a steady TELEMAC run of the Isar writes a result of 1 GB, of which aXqua reads the last time step only. :func:`inventory` lists these per category, with their size; :func:`apply` removes what was chosen.

**What is never offered:** the input files of a case (``user-sources``, also where an output folder contains it), the case file, the built model (geometry, boundary conditions, steering and friction files, initial conditions), the last time step of a result, and the processor folders of an OpenFOAM run that was not reconstructed, which hold the only copy of its results. Only files below the output folders of the case are looked at (an output folder that contains the folder of the case file, and with it other cases, is left out), and :func:`apply` acts only on what a new inventory still lists, so an identifier cannot name another file.

**Nothing is removed while a run may still use it.** A job of the case that is waiting or running blocks the removal; so does a list of jobs that cannot be read, because then nobody knows. A run started from a terminal (``initial_run.py``, ``telemac2d.py``) is no job, so a working folder or a result is also not offered while a process of this computer works in it or has it open, or when it was written to in the last :data:`RECENT_MINUTES` minutes (a run of another user or on another computer has no process here to look at); such items are listed under ``in_use`` with the reason.

**Fewer time steps** rewrites a TELEMAC result (SELAFIN) with the records of the time steps that are kept, copied byte for byte: the values are not converted, the last time step is the one the file had. aXqua reads the last time step (the convergence study, the validation, the seed of OpenFOAM), a hotstart continues from it and QGIS shows it, so all of them keep working; an animation of the run and the wetting report's look back at earlier time steps lose what was removed.
"""

from __future__ import annotations

import os
import re
import shutil
import struct
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath

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
#: The folder of the inputs of a case, never walked into.
SOURCES = "user-sources"
#: A working folder or a result written to in the last minutes may belong to a run that is still going.
RECENT_MINUTES = 15


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

    The new file is written beside the old one and takes its place only when it is complete, so an interruption leaves the result as it was.
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

def _ancestors() -> set[int]:
    """This process and the processes it was started from: the QGIS that runs Clean Up, a shell."""
    pids, pid = set(), os.getpid()
    while pid > 1 and pid not in pids:
        pids.add(pid)
        try:
            # "<pid> (<name>) <state> <parent pid> ...": the name may hold spaces and parentheses
            pid = int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            break
    return pids


def open_paths() -> set[str]:
    """The working folders and the open files of the processes this user may look at, except this process and the ones it was started from.

    Those are left out because they are not runs: the QGIS that runs Clean Up keeps every result it shows open, and a shell may have been started in the folder of the case. Read from ``/proc`` (Linux and WSL); empty where there is none, which leaves the time of the last write as the only sign of a run that is still going.
    """
    found: set[str] = set()
    try:
        entries = [e for e in Path("/proc").iterdir() if e.name.isdigit()]
    except OSError:
        return found
    mine = {str(pid) for pid in _ancestors()}
    for entry in entries:
        if entry.name in mine:
            continue
        try:
            found.add(os.readlink(entry / "cwd"))
        except OSError:
            pass
        try:
            descriptors = list((entry / "fd").iterdir())
        except OSError:
            continue
        for descriptor in descriptors:
            try:
                target = os.readlink(descriptor)
            except OSError:
                continue
            if target.startswith("/"):
                found.add(target.removesuffix(" (deleted)"))
    return found


def _stats(path: Path) -> tuple[int, float]:
    """(bytes, time of the last write) of a file, or of a folder and everything below it, in one walk (links are not followed)."""
    try:
        status = path.lstat()
    except OSError:
        return 0, 0.0
    if path.is_symlink() or not path.is_dir():
        return status.st_size, status.st_mtime
    total, newest = 0, status.st_mtime
    for root, _dirs, files in os.walk(path, followlinks=False):
        for name in files:
            try:
                status = os.lstat(os.path.join(root, name))
            except OSError:
                continue
            total += status.st_size
            newest = max(newest, status.st_mtime)
    return total, newest


def size_of(path: Path) -> int:
    """Bytes in a file, or in a folder and below it (links are not followed)."""
    return _stats(Path(path))[0]


BUSY_PROCESS = "a program of this computer works in it or has it open"


class InUse:
    """What the inventory knows about the files it looks at: their size, when they were written last, and whether a run may still use them (a process works in them or has them open, or they were written to a short while ago). Each file and folder is walked once."""

    def __init__(self, now: float | None = None) -> None:
        self.now = time.time() if now is None else now
        self._held: set[str] | None = None
        self._stats: dict[str, tuple[int, float]] = {}

    def stats(self, path: Path) -> tuple[int, float]:
        key = str(path)
        if key not in self._stats:
            self._stats[key] = _stats(Path(path))
        return self._stats[key]

    def size(self, path: Path) -> int:
        return self.stats(path)[0]

    def by_process(self, path: Path) -> bool:
        """Whether a process works in *path* or has a file in it open."""
        if self._held is None:
            # every open path with all the folders above it, so that each question is one look-up
            held: set[str] = set()
            for name in open_paths():
                pure = PurePosixPath(name)
                held.add(str(pure))
                held.update(str(parent) for parent in pure.parents)
            self._held = held
        return str(Path(path).resolve()) in self._held

    def reason(self, paths) -> str:
        """Why *paths* (one or several) are not offered; '' when they may go."""
        paths = [Path(paths)] if isinstance(paths, (str, Path)) else [Path(p) for p in paths]
        if any(self.by_process(path) for path in paths):
            return BUSY_PROCESS
        newest = max((self.stats(path)[1] for path in paths), default=0.0)
        minutes = (self.now - newest) / 60
        if minutes < RECENT_MINUTES:
            return (f"written to {max(minutes, 0):.0f} min ago, so a run may still be using it "
                    f"(offered once it is {RECENT_MINUTES} min old)")
        return ""


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


def _openfoam_items(base: Path, case: Path, survey: InUse) -> list[Item]:
    from axqua.solvers.openfoam.layout import PREVIOUS_RUN, Layout, time_folders

    size_of = survey.size
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
    """Everything of a case that can go, by category.

    Changes nothing: it reads sizes and times of files, the record markers of results, the processes of this computer and the list of jobs.
    """
    case_file = Path(case_file).resolve()
    base = case_file.parent
    roots = case_roots(cfg, base)
    items: list[Item] = []
    busy: list[dict] = []
    survey = InUse()
    size_of = survey.size
    listings: dict[tuple[Path, str], list[Path]] = {}
    processor_listings: dict[Path, list[Path]] = {}

    for root in roots:
        for folder, dirs, files in os.walk(root, followlinks=False):
            here = Path(folder)
            if (here / "system" / "controlDict").is_file():
                dirs[:] = []            # the inside of an OpenFOAM case is its own business
                if survey.by_process(here):
                    # a solver works in the case folder: none of it is offered, also not the processor folders it is about to write into
                    busy.append({"path": os.path.relpath(here, base), "reason": BUSY_PROCESS})
                else:
                    items += _openfoam_items(base, here, survey)
                continue
            for name in list(dirs):
                path = here / name
                if name == SOURCES:
                    dirs.remove(name)
                elif _RUN_FOLDER.fullmatch(name):
                    dirs.remove(name)
                    size = size_of(path)
                    items.append(_item(base, "telemac-run-folders", "delete", path, size, size,
                                       "left by a run that did not end"))
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

    # a run started from a terminal is no job: what it may still use is not offered
    offered = []
    for item in items:
        reason = survey.reason(item.paths or [item.path])
        if reason:
            busy.append({"path": item.id.split(":", 1)[1], "reason": reason})
        else:
            offered.append(item)
    items = offered

    categories = []
    for key, (title, explanation, default) in CATEGORIES.items():
        mine = sorted((i for i in items if i.category == key), key=lambda i: -i.saves)
        if mine:
            categories.append({"key": key, "title": title, "explanation": explanation,
                               "default": default, "bytes": sum(i.saves for i in mine),
                               "items": [i.as_dict() for i in mine]})
    return {"case": str(case_file), "folders": [str(r) for r in roots],
            "categories": categories, "bytes": sum(i.saves for i in items),
            "in_use": sorted(busy, key=lambda b: b["path"]),
            "blocked": active_jobs(case_file, cfg)}


def case_roots(cfg, base: Path) -> list[Path]:
    """The output folders that are looked at: none inside the inputs, and none that contains the folder of the case file (it would contain other cases as well)."""
    sources = (base / SOURCES).resolve()
    return [root for root in output_folders(cfg)
            if not root.is_relative_to(sources) and not (root != base and base.is_relative_to(root))]


def permitted(path: str | Path, roots: list[Path]) -> bool:
    """Whether *path* may be removed: it lies in one of the output folders *roots* and in no folder of inputs (``user-sources``) below it. The inventory follows the same rule when it walks the folders."""
    where = Path(path).resolve()
    return any(where.is_relative_to(root) and SOURCES not in where.relative_to(root).parts
               for root in roots)


def _same_case(row, case_file: Path, cfg=None) -> bool:
    """Whether the job of *row* was submitted for *case_file*.

    A relative path, which a job submitted from the terminal before 2026-10-18 recorded, says nothing about the folder it was given in. Its file name and the name of the project (``project.name``, which the job list keeps) decide then; many cases share the file name ``case-config.yml``, few also their project name, and a wrong match only makes the cleanup wait.
    """
    source = row.source_config
    if not source:
        return False
    path = Path(source).expanduser()
    if not path.is_absolute():
        name = getattr(cfg, "name", None) if cfg is not None else None
        return path.name == case_file.name and (not name or not row.case_name
                                                or row.case_name == name)
    try:
        return path.resolve() == case_file
    except OSError:
        return False


def active_jobs(case_file: Path, cfg=None) -> str:
    """Why nothing may be removed now ('' when it may): a job of the case is waiting or running, or nobody can tell.

    A job is found by the case file it was submitted for, and a TELEMAC job also by the lock and the queue in the simulation folder of the case (``jobs.workspace``), which do not depend on how the case file was named.
    """
    case_file = Path(case_file).resolve()
    try:
        from axqua.jobs.index import list_jobs
        from axqua.jobs.model import JobState

        rows = list_jobs()
    except Exception as exc:  # noqa: BLE001 - not knowing is not "no job"
        return (f"the list of jobs could not be read ({exc}), so nobody can tell whether a job "
                "of this case is running; nothing is removed while that is unknown.")
    busy, seen = [], set()
    for row in rows:
        if not _same_case(row, case_file, cfg):
            continue
        try:
            terminal = JobState(row.state).is_terminal if row.state else True
        except ValueError:
            terminal = False          # a state this version does not know: take it for one that runs
        if not terminal:
            busy.append(f"{row.job_id} ({row.kind}, {row.state})")
            seen.add(row.job_id)
    folder = getattr(cfg, "model_dir", None) if cfg is not None else None
    if folder and Path(folder).is_dir():
        try:
            from axqua.jobs import workspace

            holder = workspace.holder_of(folder)
            waiting = workspace.waiting_in(folder, drop=False)     # a listing changes nothing
        except Exception as exc:  # noqa: BLE001
            return (f"the lock of the simulation folder could not be read ({exc}); nothing is "
                    "removed while it is unknown whether a job works there.")
        name = Path(folder).name
        for job, what in [(holder, f"works in {name}"), *((w, f"waits for {name}") for w in waiting)]:
            if job and job not in seen:
                busy.append(f"{job} ({what})")
                seen.add(job)
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
    def check(target: Path) -> None:
        if not permitted(target, roots):
            raise ValueError(f"{target} is not in an output folder of the case")

    done, failed, freed = [], [], 0
    for item in chosen:
        try:
            if item["action"] == "thin":
                check(Path(item["path"]))
                freed += thin(item["path"], keep)
            else:
                targets = item["paths"] or [item["path"]]
                for target in map(Path, targets):
                    check(target)
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


__all__ = ["CATEGORIES", "InUse", "Item", "RECENT_MINUTES", "apply", "human", "inventory",
           "kept_frames", "open_paths", "permitted", "read_layout", "thin", "thinned_size"]
