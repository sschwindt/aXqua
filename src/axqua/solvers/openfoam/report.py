"""Has the OpenFOAM run reached a steady discharge balance?

The 3D analogue of :mod:`axqua.flux_convergence`, and deliberately the same
judgement: a free-surface run is converged when the **water** entering equals the
water leaving, measured as a relative imbalance against the prescribed inflow. The
tolerance grades carry over unchanged (``hydrodynamics.flux_tolerance``, 1e-3 for a
result read as discharge/depth/velocity), so a 2D and a 3D run of the same reach are
held to the same standard and the numbers are directly comparable.

The data come from the ``surfaceFieldValue`` monitors
:func:`axqua.solvers.openfoam.dicts._flux_functions` writes into ``controlDict``:
``sum(phi)`` weighted by ``alpha.water`` over each inlet and outlet patch, i.e. the
water discharge alone rather than the water-plus-air flux ``phi`` would give.
OpenFOAM writes one ``surfaceFieldValue.dat`` per monitor under
``postProcessing/<name>/<startTime>/``.

Sign convention: ``phi`` is positive out of the domain, so an inlet reports a
negative value. This module reports magnitudes, as the TELEMAC side does.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

log = logging.getLogger("axqua")

STEADY_WINDOW = 10          # consecutive samples that must hold the tolerance

#: How far the WATER actually entering may fall short of the prescribed discharge
#: before it is a finding. ``variableHeightFlowRateInletVelocity`` sets the velocity
#: proportional to alpha, so the MIXTURE flux integrates to the prescription exactly
#: while the water flux integrates to ``Q * <alpha^2>/<alpha>`` - short by whatever
#: the interface is smeared by. On munich-vsf that ratio was 0.821, steady from t=10
#: to t=97: the case believed 0.135 m3/s and delivered 0.111. Nothing in the output
#: said so, because the balance only ever compared the inflow against the OUTflow,
#: and both were equally short. 2% is tight on purpose - this is the one number a
#: calibration cannot absorb.
DELIVERY_TOLERANCE = 0.02   # shortfall of water inflow against the prescription


@dataclass
class DischargeHistory:
    """Per-patch water discharge against time, and the balance between them."""

    time: np.ndarray
    per_patch: dict[str, np.ndarray] = field(default_factory=dict)
    inflow: np.ndarray | None = None
    outflow: np.ndarray | None = None
    target: float = 0.0
    tolerance: float = 1.0e-3
    steady_time: float | None = None
    converged: bool = False
    #: True when the case runs under a rigid lid. The balance is then STRUCTURAL,
    #: not evidence of anything - see :meth:`lines`.
    rigid_lid: bool = False

    @property
    def imbalance(self) -> np.ndarray:
        """Relative |Q_in| - |Q_out| over |Q_in|, the same measure as in 2D."""
        if self.inflow is None or self.outflow is None:
            return np.zeros(0)
        denom = np.where(np.abs(self.inflow) > 1e-12, np.abs(self.inflow), np.nan)
        return (np.abs(self.inflow) - np.abs(self.outflow)) / denom

    @property
    def final_imbalance(self) -> float | None:
        """|relative imbalance| the run settled at - the number a study minimises.

        The mean of the last ``STEADY_WINDOW`` finite samples, not the single last
        one: the same window already decides convergence, and one final sample on a
        still-oscillating run is noise rather than a settled value.

        ``None`` when there is nothing to read, which is not the same as zero - a
        caller ranking runs must be able to tell "balanced" from "never reported".
        """
        imbalance = np.abs(self.imbalance)
        finite = imbalance[np.isfinite(imbalance)]
        if finite.size == 0:
            return None
        return float(finite[-STEADY_WINDOW:].mean())

    @property
    def delivered(self) -> float | None:
        """Water actually entering, over the discharge the case prescribes.

        The inflow monitor is alpha-weighted, so it is the WATER flux; the
        prescription is handed to a boundary condition that controls the MIXTURE.
        Those are not the same number whenever the inlet interface is smeared over
        more than nothing, and the gap is a straight bias on every result.
        """
        if self.inflow is None or self.target <= 0 or self.inflow.size == 0:
            return None
        settled = np.abs(self.inflow[-STEADY_WINDOW:])
        return float(settled.mean() / self.target)

    def _delivery_lines(self) -> list[str]:
        ratio = self.delivered
        if ratio is None or ratio >= 1.0 - DELIVERY_TOLERANCE:
            return []
        return [
            f"  ! the inlet DELIVERS ONLY {ratio:.1%} of the prescribed discharge "
            f"({self.target * ratio:.4f} against {self.target:g} m3/s). The flow-rate "
            "condition controls the mixture flux, so the water flux falls short by "
            "<alpha^2>/<alpha> - the smearing of the inlet interface. The balance "
            "below cannot see this: inflow and outflow are short by the same amount. "
            "Sharpen the inlet (more layers through the inflow depth) or raise the "
            "prescription to compensate, and state which in the writeup."]

    def lines(self) -> list[str]:
        if self.time.size == 0:
            return ["no discharge monitors found - has the run written any output yet?"]
        out = [f"discharge balance over {self.time.size} samples "
               f"(t = {self.time[0]:.2f} .. {self.time[-1]:.2f} s)"]
        for name, values in self.per_patch.items():
            out.append(f"  {name:<12}: {np.abs(values[-1]):8.4f} m3/s "
                       f"(mean of last 10: {np.abs(values[-10:]).mean():.4f})")
        if self.inflow is not None and self.outflow is not None:
            imb = self.imbalance
            out.append(f"  inflow total : {np.abs(self.inflow[-1]):8.4f} m3/s "
                       f"(prescribed {self.target:g})")
            out.extend(self._delivery_lines())
            out.append(f"  outflow total: {np.abs(self.outflow[-1]):8.4f} m3/s")
            out.append(f"  imbalance    : {100 * imb[-1]:+8.3f}% "
                       f"(tolerance {100 * self.tolerance:g}%)")
        if self.rigid_lid:
            # Under a rigid lid the free surface cannot move, so the water volume in
            # the domain is FIXED and inflow must equal outflow at every step
            # whatever the flow is doing. The balance is then a property of the
            # boundary conditions, not a measurement - it closes within seconds and
            # would read "converged" on a run that had not settled at all. Say so,
            # rather than letting a tautology be read as evidence.
            out.append(f"  the balance closes to {100 * abs(imb[-1]):.3f}%, but under a "
                       "RIGID LID that is structural, not evidence: the lid fixes the "
                       "water volume, so inflow equals outflow by construction.")
            out.append("  Judge steadiness on the VELOCITY FIELD instead (compare "
                       "successive write times); on this reach the mean settles long "
                       "before the local fluctuation does, which is why the "
                       "calibration averages the last n_avg_timesteps writes.")
        elif self.converged:
            out.append(f"  CONVERGED at t = {self.steady_time:.2f} s: the balance held "
                       f"inside {100 * self.tolerance:g}% for {STEADY_WINDOW} "
                       "consecutive samples")
        else:
            out.append("  NOT yet converged - extend openfoam.end_time, or check "
                       "whether the inflow has reached the prescribed discharge at all")
        return out


def _read_monitor(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """Read one ``surfaceFieldValue.dat`` into ``(time, value)``."""
    times, values = [], []
    with open(path) as fh:
        for line in fh:
            if line.startswith("#") or not line.strip():
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            try:
                times.append(float(parts[0]))
                values.append(float(parts[1]))
            except ValueError:
                continue
    return np.asarray(times), np.asarray(values)


def _monitor_files(start: Path) -> list[Path]:
    """Every ``surfaceFieldValue*.dat`` in one start-time folder, in write order.

    A restart into a folder that already holds a file does NOT append to it:
    OpenFOAM opens ``surfaceFieldValue_0.dat`` beside it, then ``_1`` and so on.
    Reading only the unsuffixed name therefore returns the FIRST leg of a staged
    run and silently discards every later one - and every aXqua case is staged,
    spin-up then run. Measured on the dx45 comparison: the reader reported "8
    samples (t = 1.00 .. 8.00 s)" on a run that was at t = 23.8, so the verdict
    was being formed entirely from the spin-up.
    """
    files = sorted(start.glob("surfaceFieldValue*.dat"),
                   key=lambda f: (len(f.stem), f.stem))
    return [f for f in files if f.is_file()]


def _concat_monitor(start_dirs) -> tuple[np.ndarray, np.ndarray]:
    """``(time, value)`` across every leg, sorted, with restart overlaps dropped."""
    chunks = []
    for start in start_dirs:
        for dat in _monitor_files(start):
            chunks.append(_read_monitor(dat))
    if not chunks:
        return np.zeros(0), np.zeros(0)
    time = np.concatenate([c[0] for c in chunks])
    value = np.concatenate([c[1] for c in chunks])
    order = np.argsort(time, kind="stable")
    time, value = time[order], value[order]
    # a restart re-reports its start time; keep the later sample
    keep = np.ones(time.size, dtype=bool)
    keep[:-1] = time[1:] != time[:-1]
    return time[keep], value[keep]


def read_monitors(case_dir: str | Path) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Every ``Q_*`` monitor in the case, concatenated across restart directories.

    A two-stage run writes ``postProcessing/Q_inlet_1/0/`` for the spin-up and
    ``.../30/`` for the production stage; both are read and joined in time order so
    the history spans the whole run rather than only its last leg.
    """
    root = Path(case_dir) / "postProcessing"
    out: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    if not root.is_dir():
        return out
    for monitor in sorted(root.iterdir()):
        if not monitor.is_dir() or not monitor.name.startswith("Q_"):
            continue
        time, value = _concat_monitor(
            sorted(monitor.iterdir(), key=lambda p: _as_float(p.name)))
        if time.size == 0:
            continue
        name = monitor.name[2:].replace("_", "-")
        out[name] = (time, value)
    return out


def _as_float(name: str) -> float:
    try:
        return float(name)
    except ValueError:
        return float("inf")


def analyse(cfg, case_dir: str | Path | None = None, *,
            target: float | None = None) -> DischargeHistory:
    """Build the discharge history and decide whether the run has settled."""
    case_dir = Path(case_dir) if case_dir else cfg.openfoam_case_dir
    monitors = read_monitors(case_dir)
    tolerance = float(cfg.hydrodynamics.flux_tolerance)
    if target is None:
        target = float(cfg.boundaries.prescribed_flowrate or 0.0)
    rigid_lid = getattr(cfg, "rigid_lid", False)
    if not monitors:
        return DischargeHistory(time=np.zeros(0), target=target, tolerance=tolerance,
                                rigid_lid=rigid_lid)

    # the monitors share a write interval, but a restart can leave them ragged;
    # interpolate every patch onto the shortest common time base
    base = min((t for t, _ in monitors.values()), key=lambda t: t[-1] if t.size else 0)
    per_patch = {name: np.interp(base, t, v) for name, (t, v) in monitors.items()}

    inlets = [v for k, v in per_patch.items() if k.startswith("inlet")]
    outlets = [v for k, v in per_patch.items() if k.startswith("outlet")]
    history = DischargeHistory(
        time=base, per_patch=per_patch, target=target, tolerance=tolerance,
        inflow=np.sum(inlets, axis=0) if inlets else None,
        outflow=np.sum(outlets, axis=0) if outlets else None,
        rigid_lid=rigid_lid,
    )
    if history.inflow is not None and history.outflow is not None:
        _find_steady(history)
    for line in history.lines():
        log.info("%s", line)
    return history


def _find_steady(history: DischargeHistory) -> None:
    """First time the relative imbalance holds the tolerance for STEADY_WINDOW samples."""
    imb = np.abs(history.imbalance)
    good = np.isfinite(imb) & (imb <= history.tolerance)
    if good.size < STEADY_WINDOW:
        return
    # a run of STEADY_WINDOW consecutive True values
    window = np.convolve(good.astype(int), np.ones(STEADY_WINDOW, dtype=int), "valid")
    hits = np.flatnonzero(window == STEADY_WINDOW)
    if hits.size:
        history.converged = True
        history.steady_time = float(history.time[hits[0]])


# --------------------------------------------------------------------------- #
# did the 2D seed box the run in?
# --------------------------------------------------------------------------- #

# How much contact is not a finding. The lid stands clear of the water everywhere by
# construction, so anything but numerical noise there is real. The lateral wall is
# different: the domain ends somewhere, and at the inflow and outflow the channel
# necessarily runs into that edge - so a small share of the banks patch is wet in every
# healthy run, and a threshold of zero would cry wolf on all of them.
LID_CONTACT_TOLERANCE = 0.001    # of the lid patch area
WALL_CONTACT_TOLERANCE = 0.02    # of the banks patch area

#: Water leaving through the lid, as a fraction of the prescribed discharge, above
#: which the run is not a result. Area is the wrong scale for a breach and this is
#: the right one: on munich-vsf the lid was wet over 0.47% of its area - a figure
#: that reads as noise - while venting FIFTEEN TIMES the discharge. The pressure
#: outlet silently replaced what left, so the domain ran a steady spurious
#: circulation and every other diagnostic stayed healthy. 1% is already generous:
#: a lid is a boundary water has no business crossing at all.
LID_LEAK_TOLERANCE = 0.01        # of the prescribed discharge


@dataclass
class SurfaceFreedom:
    """Whether the free surface was ever stopped by the mesh rather than the flow."""

    lid_area: float = 0.0        # peak water area on the top patch [m2]
    wall_area: float = 0.0       # peak water area on the lateral wall [m2]
    lid_total: float = 0.0       # the top patch's own area [m2]
    wall_total: float = 0.0      # the banks patch's own area [m2]
    lid_leak: float = 0.0        # peak water flux OUT through the lid [m3/s]
    discharge: float = 0.0       # the prescribed discharge, to scale the leak against
    prescribed: bool = False     # rigid lid: the surface never had freedom to lose
    measured: bool = False       # were the monitors there to read at all?
    #: False while the run has not yet passed its spin-up. The monitors exist and
    #: are being written, but every sample so far belongs to a transient that is
    #: violent by design, so there is no verdict to give. Distinguished from
    #: `free` deliberately: "nothing to report yet" and "the surface was free" look
    #: identical in a dataclass of zeros, and one of them is a false all-clear.
    settled: bool = True

    @property
    def lid_fraction(self) -> float:
        return self.lid_area / self.lid_total if self.lid_total > 0 else 0.0

    @property
    def wall_fraction(self) -> float:
        return self.wall_area / self.wall_total if self.wall_total > 0 else 0.0

    @property
    def leak_fraction(self) -> float:
        """Water lost through the lid, over the discharge the case prescribes."""
        return self.lid_leak / self.discharge if self.discharge > 0 else 0.0

    @property
    def hit_lid(self) -> bool:
        return (self.lid_fraction > LID_CONTACT_TOLERANCE
                or self.leak_fraction > LID_LEAK_TOLERANCE)

    @property
    def hit_wall(self) -> bool:
        return self.wall_fraction > WALL_CONTACT_TOLERANCE

    @property
    def free(self) -> bool:
        return not self.hit_lid and not self.hit_wall

    def lines(self, cfg) -> list[str]:
        if self.measured and not self.settled:
            return ["surface   : no verdict yet - the run has not passed its "
                    f"spin-up ({getattr(cfg.openfoam, 'spinup_time', 0):g} s), and "
                    "the spin-up is a deliberately violent transient whose peaks say "
                    "nothing about the result"]
        if self.prescribed:
            return ["surface   : prescribed by the 2D seed (mode: rigid-lid), not "
                    "solved - this run cannot show a jump, a standing wave or "
                    "superelevation"]
        if not self.measured:
            return []
        of = cfg.openfoam
        if self.free:
            return [f"surface   : free - it stayed clear of the lid "
                    f"({self.lid_fraction:.2%} of that patch ever wet, leaking "
                    f"{self.leak_fraction:.2%} of the discharge) and of the "
                    f"lateral wall ({self.wall_fraction:.2%}), so the 2D seed bounded "
                    "it without constraining it"]
        out = ["surface   : ! CONSTRAINED by the mesh, not by the flow"]
        if self.hit_lid:
            out.append(f"  water reached the lid over up to {self.lid_area:,.1f} m2 "
                       f"({self.lid_fraction:.1%} of it). The freeboard "
                       f"({of.freeboard:g} m) was too small, so the surface could not "
                       "rise as far as the hydraulics wanted. Raise openfoam.freeboard "
                       "and rebuild - the result at that level is not physical.")
            if self.lid_leak > 0:
                out.append(
                    f"  and it did not merely touch: up to {self.lid_leak:,.3f} m3/s "
                    f"LEFT through the lid, {self.leak_fraction:.0%} of the "
                    f"{self.discharge:g} m3/s this case prescribes. Whatever leaves "
                    "that way is replaced through the pressure outlet, so the domain "
                    "carries a spurious circulation of that size while the discharge "
                    "balance still closes. Read no velocity or level off this run.")
        if self.hit_wall:
            out.append(f"  water reached the lateral wall over up to "
                       f"{self.wall_area:,.1f} m2 ({self.wall_fraction:.1%} of it, "
                       f"past the {WALL_CONTACT_TOLERANCE:.0%} that inflow and outflow "
                       "corners account for). The footprint (wet_margin "
                       f"{of.wet_margin:g} m past the 2D water line) stopped the flow "
                       "spreading. Raise openfoam.wet_margin, or set domain: roi.")
        return out


def surface_freedom(cfg, case_dir: str | Path | None = None) -> SurfaceFreedom:
    """Read the ``lidContact`` / ``wallContact`` monitors into a verdict.

    The seed fixes two things the flow has no say in - how high the lid stands and
    how far the footprint reaches - and both are sized from a TELEMAC result that is
    itself a model. If the 3D surface reached either, the answer was set by a meshing
    decision rather than by the hydraulics, and nothing else in the output would say
    so: the run converges, the discharge balances, the picture looks like a river.

    Zero on both is the result you want. Reported per run rather than checked at
    build time, because it is the only moment the question can actually be answered.
    """
    if cfg.openfoam.mode == "rigid-lid":
        return SurfaceFreedom(prescribed=True, measured=True)
    root = Path(case_dir) if case_dir else cfg.openfoam_case_dir
    # Judge the RUN, not the spin-up: see _read_named_monitor. A case with no
    # spin-up stage has spinup_time 0 and nothing is dropped.
    since = float(getattr(cfg.openfoam, "spinup_time", 0.0) or 0.0)
    lid = _read_named_monitor(root, "lidContact", since=since)
    wall = _read_named_monitor(root, "wallContact", since=since)
    leak = _read_named_monitor(root, "lidLeak", since=since)
    if lid is None and wall is None:
        return SurfaceFreedom()
    lid = lid or (np.zeros(0), 0.0)
    wall = wall or (np.zeros(0), 0.0)
    # Positive phi is OUT of the domain, so only the positive part is a leak; a
    # negative sample is air being drawn back in, which is what an atmosphere patch
    # is for. Older runs have no lidLeak folder at all, and read as zero rather than
    # as a failure - the area check still stands on its own.
    leaked = leak[0] if leak is not None else np.zeros(0)
    # The lid/wall monitors are always written, so "both empty after the spin-up
    # filter" means the run has not got there yet - not that nothing happened.
    settled = bool(lid[0].size or wall[0].size)
    return SurfaceFreedom(
        settled=settled,
        lid_area=float(np.max(lid[0])) if lid[0].size else 0.0,
        wall_area=float(np.max(wall[0])) if wall[0].size else 0.0,
        lid_leak=float(np.max(np.maximum(leaked, 0.0))) if leaked.size else 0.0,
        # Defensively: this is a diagnostic, and a verdict that raises because a
        # config is missing a field is worse than one that cannot scale the leak.
        discharge=float(getattr(getattr(cfg, "boundaries", None),
                                "prescribed_flowrate", 0.0) or 0.0),
        lid_total=lid[1], wall_total=wall[1], measured=True)


def _read_named_monitor(root: Path, name: str, *, since: float = 0.0
                        ) -> tuple[np.ndarray, float] | None:
    """``(values, patch area)`` for one non-``Q_`` monitor, across restart folders.

    The patch area comes out of the ``# Area :`` header OpenFOAM writes, which is what
    turns a bare contact area into a fraction - and a fraction is the only form in
    which "did the water touch this?" has a threshold that means the same thing on a
    30 m side channel and a 300 m reach.

    ``since`` drops everything before a time. The caller passes the spin-up length,
    because the spin-up is a deliberately violent transient - a dry-ish domain being
    filled against a prescribed stage - and its peaks are not evidence about the
    result. Judging the lid on them condemns every run: on the dx45 pair the spin-up
    peak was 7.4 m3/s through the lid while the question is what the settled flow does.
    """
    folder = root / "postProcessing" / name
    if not folder.is_dir():
        return None
    starts = sorted(folder.iterdir(), key=lambda p: _as_float(p.name))
    time, values = _concat_monitor(starts)
    area = max((_patch_area(f) for s in starts for f in _monitor_files(s)),
               default=0.0)
    if since > 0 and time.size:
        # strictly after: the sample AT the spin-up end time is the spin-up's last,
        # not the run's first, and a restart re-reports that instant anyway
        values = values[time > since]
    return values, area


def _patch_area(path: Path) -> float:
    """The ``# Area :`` line of a surfaceFieldValue header, or 0 when absent."""
    with open(path) as fh:
        for line in fh:
            if not line.startswith("#"):
                break
            if line.startswith("# Area"):
                try:
                    return float(line.split(":")[1])
                except (IndexError, ValueError):
                    return 0.0
    return 0.0


# --------------------------------------------------------------------------- #
# was the rigid lid applicable, and does the finished run still say so?
# --------------------------------------------------------------------------- #

#: p99 lid step, as a fraction of the local depth, above which the mode is doubtful:
#: half the water depth lost within two cells is a drop, not a slope. A heuristic,
#: which is why the build warns here rather than refusing.
LID_STEP_WARN = 0.5
#: ...and above this the prescribed surface steps further than the water is deep -
#: a weir, a drop structure, or the slots of a fish pass. The build REFUSES here
#: unless ``openfoam.allow_stepped_lid`` is set; the verdict reaches the result
#: either way.
#:
#: Both live in this module, not in `mesh`, so that reading a finished run's verdict
#: costs nothing heavier than numpy - and so the build-time grade and the reported
#: grade are the same two numbers rather than two copies that can drift.
LID_STEP_SEVERE = 1.0


@dataclass
class LidApplicability:
    """The build's rigid-lid verdict, carried forward to the finished run.

    A rigid lid is a slip *wall* on the prescribed free surface, so the applicability
    question - does that surface step sharply on the scale of a cell? - can only be
    asked of the **input**, at build time. The result cannot answer it afterwards: a
    run whose lid is wrong converges, balances its discharge and looks like a river.
    munich-vsf did exactly that for 61 hours, twice, and in both cases the output
    carried no trace of the build-time warning.

    So the numbers are written into the case at build time
    (:data:`axqua.solvers.openfoam.case.BUILD_RECORD`) and read back here, which is
    the same role :class:`SurfaceFreedom` plays for the two-phase case: a statement
    about whether the answer was set by a meshing decision rather than by the flow.
    """

    rigid_lid: bool = False
    median: float | None = None       # lid range within 2 cells / local depth
    p99: float | None = None
    recorded: bool = False            # was there a build record to read at all?

    @property
    def doubtful(self) -> bool:
        return self.p99 is not None and self.p99 > LID_STEP_WARN

    @property
    def severe(self) -> bool:
        return self.p99 is not None and self.p99 > LID_STEP_SEVERE

    def lines(self, cfg) -> list[str]:
        if not self.rigid_lid:
            return []
        if not self.recorded or self.p99 is None:
            return ["lid       : ? this case was built before the lid-step test was "
                    "recorded, so whether a rigid lid applies here is unknown. "
                    "Rebuild to find out - it costs no solver time."]
        head = (f"lid       : the prescribed surface steps {100 * self.p99:.0f}% of "
                f"the local depth within two cells at the 99th percentile "
                f"(median {100 * self.median:.1f}%)")
        if not self.doubtful:
            return [head + " - smooth on the scale of a cell, so the lid is a fair "
                           "approximation here"]
        out = [head]
        if self.severe:
            out.append("  ! THE RIGID LID DOES NOT APPLY TO THIS RESULT. The surface "
                       "steps further than the water is deep, which is a weir, a drop "
                       "structure or a slot - and a lid cannot answer a drop by "
                       "plunging, so it converts the head into velocity instead "
                       "(sqrt(2 g dz): a 1 m step drives 4.4 m/s). The velocity at "
                       "those steps is an artefact of the mode, whatever the "
                       "discharge balance and the Courant number say.")
        else:
            out.append("  ! the rigid lid is doubtful here. Expect the fastest cells "
                       "to sit at the steps and the velocity cap to be load-bearing; "
                       "read bulk routing and levels, not local velocity.")
        out.append("  Use openfoam.mode: vof where the surface has to move, or check "
                   "that the steps are real rather than the 2D result read across a "
                   "structure the lattice is too coarse to seal.")
        return out


def lid_applicability(cfg, case_dir: str | Path | None = None) -> LidApplicability:
    """Read the build record's lid-step test back out of a built case."""
    import json

    from axqua.solvers.openfoam.case import BUILD_RECORD

    rigid = cfg.openfoam.mode == "rigid-lid"
    root = Path(case_dir) if case_dir else cfg.openfoam_case_dir
    path = root / BUILD_RECORD
    if not path.is_file():
        return LidApplicability(rigid_lid=rigid)
    try:
        record = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        log.debug("could not read %s: %s", path, exc)
        return LidApplicability(rigid_lid=rigid)
    p99 = record.get("lid_step_p99")
    return LidApplicability(
        rigid_lid=bool(record.get("rigid_lid", rigid)),
        median=record.get("lid_step_median"), p99=p99,
        recorded=True)


def verdict_lines(cfg, case_dir: str | Path | None = None,
                  history: "DischargeHistory | None" = None) -> list[str]:
    """Everything a finished run has to be read against, in one place."""
    out: list[str] = []
    if history is not None:
        out += history.lines()             # DischargeHistory.lines takes no config
    out += surface_freedom(cfg, case_dir).lines(cfg)
    out += lid_applicability(cfg, case_dir).lines(cfg)
    return out


def write_report(cfg, case_dir: str | Path | None = None, *,
                 out_dir: str | Path | None = None,
                 target: float | None = None) -> tuple[DischargeHistory, list[Path]]:
    """Analyse the run and write ``discharge-convergence.csv`` + ``.png``."""
    case_dir = Path(case_dir) if case_dir else cfg.openfoam_case_dir
    out_dir = Path(out_dir) if out_dir else case_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    history = analyse(cfg, case_dir, target=target)
    if history.time.size == 0:
        return history, []

    written = [_write_csv(history, out_dir / "discharge-convergence.csv")]
    plot = _write_plot(history, out_dir / "discharge-convergence.png")
    if plot is not None:
        written.append(plot)
    lines = verdict_lines(cfg, case_dir)
    for line in lines:
        log.info("%s", line)
    if lines:
        # ...and to a file, because the whole point is that the finding outlives the
        # terminal it was printed in.
        summary = out_dir / "run-verdict.txt"
        summary.write_text("\n".join(lines) + "\n")
        log.info("wrote %s", summary)
        written.append(summary)
    return history, written


def _write_csv(history: DischargeHistory, path: Path) -> Path:
    import pandas as pd

    data = {"time_s": history.time}
    for name, values in history.per_patch.items():
        data[f"Q_{name}_m3s"] = np.abs(values)
    if history.inflow is not None:
        data["Q_in_m3s"] = np.abs(history.inflow)
    if history.outflow is not None:
        data["Q_out_m3s"] = np.abs(history.outflow)
        data["relative_imbalance"] = history.imbalance
    pd.DataFrame(data).to_csv(path, index=False)
    log.info("wrote %s", path)
    return path


def _write_plot(history: DischargeHistory, path: Path) -> Path | None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, (ax_q, ax_i) = plt.subplots(2, 1, figsize=(9, 7), sharex=True)
    for name, values in history.per_patch.items():
        ax_q.plot(history.time, np.abs(values), lw=1.2, label=name)
    if history.target:
        ax_q.axhline(history.target, color="k", ls="--", lw=1,
                     label=f"prescribed {history.target:g} m3/s")
    ax_q.set_ylabel("water discharge [m3/s]")
    ax_q.legend(fontsize=8, ncol=2)
    ax_q.grid(alpha=0.3)
    ax_q.set_title("OpenFOAM boundary discharge and mass balance")

    if history.inflow is not None and history.outflow is not None:
        ax_i.plot(history.time, 100 * np.abs(history.imbalance), lw=1.2, color="C3")
        ax_i.axhline(100 * history.tolerance, color="k", ls=":", lw=1,
                     label=f"tolerance {100 * history.tolerance:g}%")
        if history.converged:
            ax_i.axvline(history.steady_time, color="C2", lw=1,
                         label=f"converged t={history.steady_time:.1f} s")
        ax_i.set_yscale("log")
        ax_i.legend(fontsize=8)
    ax_i.set_ylabel("|relative imbalance| [%]")
    ax_i.set_xlabel("time [s]")
    ax_i.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    log.info("wrote %s", path)
    return path
