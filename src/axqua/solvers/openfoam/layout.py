"""How an OpenFOAM case lies on disk: its time folders, the processor folders of a parallel run, and what a run leaves in it.

Standard library only, because the result listing (:meth:`OpenFoamBackend.describe_results`) and the capability table read it, and both have to stay cheap. Every part of the backend that asks which folders are times asks :func:`time_folders`.
"""

from __future__ import annotations

import logging
import math
import re
import shutil
from pathlib import Path

log = logging.getLogger("axqua")

#: Where the results of the run before are kept when a run starts afresh or the case is built anew.
PREVIOUS_RUN = "previous-run"
#: What a run and its reports write into the case besides its time and processor folders (``planview.RESULT_NAME``, the files of ``report.write_report``, the VTK conversion).
RUN_PRODUCTS = ("postProcessing", "VTK", "depth-averaged.slf", "discharge-convergence.csv",
                "discharge-convergence.png", "run-verdict.txt")

_PROCESSOR = re.compile(r"processor(\d+)")
#: The single folder of a collated decomposition (``processors4``, or ``processors4_0-3`` per host).
_COLLATED = re.compile(r"processors(\d+)(_\d+-\d+)?")


def time_folders(folder: str | Path) -> list[tuple[float, Path]]:
    """The time folders in *folder* (the folders whose name is a finite number), sorted by time; none for a folder that does not exist."""
    folder = Path(folder)
    found = []
    for path in folder.iterdir() if folder.is_dir() else ():
        try:
            value = float(path.name)
        except ValueError:
            continue
        if math.isfinite(value) and path.is_dir():
            found.append((value, path))
    return sorted(found, key=lambda item: item[0])


def latest_time(folders) -> float | None:
    """The latest time after the start among the time folders in *folders*, None without one."""
    later = [value for folder in folders for value, _ in time_folders(folder) if value > 0]
    return max(later) if later else None


def processor_folders(case_dir: str | Path) -> list[Path]:
    """The ``processor<N>`` folders of a parallel run and the ``processors<N>`` folders of a collated one."""
    case_dir = Path(case_dir)
    return sorted(path for path in (case_dir.iterdir() if case_dir.is_dir() else ())
                  if path.is_dir() and (_PROCESSOR.fullmatch(path.name)
                                        or _COLLATED.fullmatch(path.name)))


class Layout:
    """How a case lies on disk for a parallel run, read in one pass: the number of domains it is split into (the ``processor<N>`` folders, or the ``processors<N>`` folder of a collated decomposition), the latest time in them and at the top level, and whether the active controlDict continues from the latest time."""

    def __init__(self, count: int, folders: list[Path], parallel: float | None,
                 top: float | None, continues: bool) -> None:
        self.count, self.folders = count, folders
        self.parallel, self.top, self.continues = parallel, top, continues

    @classmethod
    def of(cls, case_dir: str | Path) -> "Layout":
        case_dir = Path(case_dir)
        if not case_dir.is_dir():
            return cls(0, [], None, None, False)
        single, collated, count = [], [], 0
        for path in processor_folders(case_dir):
            if (found := _COLLATED.fullmatch(path.name)):
                collated.append(path)
                count = max(count, int(found.group(1)))
            else:
                single.append(path)
        count, folders = (count, collated) if collated else (len(single), single)
        control = case_dir / "system" / "controlDict"
        text = control.read_text(errors="replace") if control.is_file() else ""
        continues = re.search(r"^\s*startFrom\s+latestTime\s*;", text, re.M) is not None
        return cls(count, folders, latest_time(folders), latest_time([case_dir]), continues)

    @property
    def only_in_processor_folders(self) -> bool:
        """The processor folders hold a later time than the case itself: results of a stage that was not reconstructed."""
        return self.parallel is not None and self.parallel > (self.top or 0)


def processor_count(case_dir: str | Path) -> int:
    """The number of domains the case is split into (0 when it is not split)."""
    return Layout.of(case_dir).count


def set_aside_runs(case_dir: str | Path) -> list[str]:
    """Move what earlier runs left in the case into ``previous-run/``, which replaces the one before; returns the names moved.

    A stage that starts afresh needs the case without them: a later time at the top level or in the processor folders is where the next stage would continue from, and the discharge monitors in ``postProcessing`` would be joined to those of the new run. So does a case built anew: its results cannot be continued on the new mesh, its processor folders hold the old mesh itself, and the depth-averaged result and the reports would be listed as results of the new case. They are moved rather than deleted, because a result that only the processor folders hold may be the only copy of hours of computing, and the new run has produced nothing yet. Only the run before is kept, so that the folder does not grow with every run.
    """
    case_dir = Path(case_dir)
    leftovers = [path for value, path in time_folders(case_dir) if value > 0]
    leftovers += processor_folders(case_dir)
    leftovers += [case_dir / name for name in RUN_PRODUCTS if (case_dir / name).exists()]
    if not leftovers:
        return []
    kept = case_dir / PREVIOUS_RUN
    if kept.exists():
        shutil.rmtree(kept)
    kept.mkdir()
    for path in leftovers:
        path.rename(kept / path.name)
    log.info("moved what the earlier run left into %s (%s)", kept, ", ".join(p.name for p in leftovers))
    return [path.name for path in leftovers]
