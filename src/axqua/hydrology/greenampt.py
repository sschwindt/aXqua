"""Green-Ampt infiltration: the per-node algorithm of aXqua's runoff routine, in Python.

TELEMAC-2D v9.1.1 ships one Green-Ampt routine (``RAINFALL-RUNOFF MODEL : 3``), the
integral form of Travert et al. (2022). De Vos et al. (2024, Proceedings of the XXXth
TELEMAC User Conference, hdl 20.500.11970/114347) showed that it does not let ponded
water infiltrate once the rain stops, and that its capacity rises when the rain
intensifies after ponding, through the term ``c = P - F``. They recommend the explicit
solution of Sadeghi et al. (2024, Vadose Zone Journal 23(4)) with the ponding time of
Chu (1978) for unsteady rain. The first implementation in TELEMAC is Broich et al.
(2022, Proceedings of the XXVIIIth TELEMAC User Conference, pp. 143-145).

aXqua generates its own ``runoff_greenampt.f`` (:mod:`axqua.solvers.telemac.greenampt`)
with three kernels, and this module is the same algorithm in Python. It serves as the
reference the tests check against the exact Green-Ampt solution, and as a quick
estimate of how much of a storm a soil takes up:

``sadeghi`` (the default)
    Before ponding the capacity is the exact ``Ks (1 + a/F)``, so all rain infiltrates.
    A node ponds when that capacity falls below the rain intensity (Chu's criterion for
    a hyetograph of blocks), or, as aXqua's extension, when water from upslope stands
    on it deeper than ``runon_depth`` (the gap de Vos et al. name). After ponding the
    capacity follows Sadeghi's explicit solution in the time since ponding, shifted by
    the time the ponded curve needs to reach the depth already infiltrated (their
    eq. 4 to 9). Ponded water keeps infiltrating after the rain.
``exact``
    The capacity ``Ks (1 + a/F)`` on the infiltrated depth the routine tracks per node,
    limited by the water available. This is Travert's form with ``c = 0``, which de Vos
    et al. name as the repair of its anomaly, plus the infiltration of ponded water.
``travert``
    The equations TELEMAC ships, for comparison: capacity ``Ks (1 + (a + c dtheta)/F)``
    with ``c = P - F``, and only rain infiltrates.

In all three, ``a = psi (theta_s - theta_i)`` is the product of the suction head at the
wetting front and the moisture deficit, so a soil is two numbers per node, ``Ks`` and
``a`` (``dtheta`` is needed by ``travert`` alone). Evaporation is taken from the surface
water left after infiltration and never below the water there is.

The per-step update is written so that the Fortran routine can repeat it: TELEMAC calls
the source terms once per sub-iteration of a time step, so the routine computes the step
from the state at its start and commits it only when the next step begins.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

#: The kernels, the first being the default.
KERNELS = ("sadeghi", "exact", "travert")

#: Constants of Sadeghi's explicit solution as de Vos et al. (2024) give them (eq. 7).
SADEGHI_C0 = 1.4127
SADEGHI_C1 = 0.6843
SADEGHI_C2 = 9.43456

#: A capacity that stands for "no limit" before anything has infiltrated (m/s).
UNLIMITED = 1.0e30

MM_PER_H = 1.0 / 3.6e6       # 1 mm/h in m/s


def sadeghi_capacity(ks: float, a: float, t_ga: float) -> float:
    """Infiltration capacity [m/s] of a ponded soil after Sadeghi et al. (2024).

    *t_ga* is the time on the ponded Green-Ampt curve [s] (de Vos et al., eq. 6). Within
    about 5 % of the exact capacity at the start of ponding and within 1 % once
    ``Ks t / a`` exceeds 1 (checked in the tests).
    """
    if ks <= 0.0:
        return 0.0
    if a <= 0.0:
        return ks
    t_ga = max(t_ga, 1.0e-9)
    c1 = SADEGHI_C0 + SADEGHI_C1 * math.sqrt(1.0 + 2.0 * SADEGHI_C2 * a / (ks * t_ga))
    return ks * (1.0 + 2.0 * a / (ks * t_ga * c1))


def ponded_time(ks: float, a: float, depth: float) -> float:
    """Time [s] the ponded Green-Ampt curve needs to infiltrate *depth* [m].

    The exact inverse of Green-Ampt under ponding from a dry start,
    ``t = (F - a ln(1 + F/a)) / Ks``; with ``F`` the depth at ponding it is the time
    shift ``t_s`` of de Vos et al. (eq. 5).
    """
    if ks <= 0.0 or depth <= 0.0:
        return 0.0
    if a <= 0.0:
        return depth / ks
    return (depth - a * math.log1p(depth / a)) / ks


def exact_capacity(ks: float, a: float, infiltrated: float) -> float:
    """Capacity [m/s] from the infiltrated depth, ``Ks (1 + a/F)``."""
    if ks <= 0.0:
        return 0.0
    if infiltrated <= 0.0:
        return UNLIMITED
    return ks * (1.0 + max(a, 0.0) / infiltrated)


@dataclass
class Soil:
    """Green-Ampt parameters of one node: ``Ks`` [mm/h], ``a`` [m], ``dtheta`` [-]."""

    ks_mm_h: float
    a: float
    dtheta: float = 0.0

    @property
    def ks(self) -> float:
        return float(self.ks_mm_h) * MM_PER_H

    @classmethod
    def from_parameters(cls, ks_mm_h: float, suction: float, theta_s: float,
                        theta_i: float) -> "Soil":
        dtheta = max(float(theta_s) - float(theta_i), 0.0)
        return cls(ks_mm_h=float(ks_mm_h), a=float(suction) * dtheta, dtheta=dtheta)


@dataclass
class NodeState:
    """What the routine keeps per node between steps."""

    infiltrated: float = 0.0     # F [m]
    evaporated: float = 0.0      # cumulative evaporation [m]
    ponded: bool = False
    t_ponding: float = 0.0       # [s]
    f_ponding: float = 0.0       # infiltrated depth at ponding [m]


@dataclass
class Step:
    """The result of one step at one node."""

    infiltration: float          # depth infiltrated in the step [m]
    evaporation: float           # depth evaporated in the step [m]
    source: float                # net surface source PLUIE [m/s]
    capacity: float              # capacity used [m/s]
    state: NodeState = field(default_factory=NodeState)


def step(state: NodeState, soil: Soil, *, t_end: float, dt: float, rain: float,
         evaporation: float, depth: float, rain_total: float, kernel: str = "sadeghi",
         ponding_by_runon: bool = True, runon_depth: float = 0.001) -> Step:
    """Advance one node over the step ``[t_end - dt, t_end]``.

    *rain* and *evaporation* are rates [m/s] over the step, *depth* the water depth at
    its start [m], *rain_total* the rain fallen before the step [m] (``travert`` only).
    Returns the new state without changing *state*, so a step can be computed again.
    """
    if kernel not in KERNELS:
        raise ValueError(f"unknown Green-Ampt kernel {kernel!r}; one of {KERNELS}")
    ks, a = soil.ks, soil.a
    f0 = state.infiltrated
    h0 = max(depth, 0.0)
    supply = rain * dt + h0
    infiltration = 0.0
    capacity = 0.0
    new = NodeState(**vars(state))
    if ks > 0.0:
        if kernel == "travert":
            capacity = (1.0e6 if f0 <= 0.0
                        else ks * (1.0 + (a + (rain_total - f0) * soil.dtheta) / f0))
            infiltration = min(rain * dt, capacity * dt)
        elif kernel == "exact" or not state.ponded:
            capacity = exact_capacity(ks, a, f0)
            infiltration = min(supply, capacity * dt)
        else:
            t_ga = (t_end - 0.5 * dt) - state.t_ponding + ponded_time(ks, a,
                                                                     state.f_ponding)
            capacity = sadeghi_capacity(ks, a, max(t_ga, 0.5 * dt))
            infiltration = min(supply, capacity * dt)
    infiltration = max(infiltration, 0.0)
    evaporated = min(max(evaporation, 0.0) * dt, max(supply - infiltration, 0.0))
    new.infiltrated = f0 + infiltration
    new.evaporated = state.evaporated + evaporated
    if kernel == "sadeghi" and ks > 0.0 and not state.ponded:
        by_rain = rain > ks and new.infiltrated >= a * ks / (rain - ks)
        by_runon = ponding_by_runon and h0 > runon_depth
        if by_rain or by_runon:
            new.ponded = True
            new.t_ponding = t_end
            new.f_ponding = new.infiltrated
    source = (rain * dt - infiltration - evaporated) / dt
    return Step(infiltration=infiltration, evaporation=evaporated, source=source,
                capacity=min(capacity, UNLIMITED), state=new)


@dataclass
class ColumnResult:
    """A soil column under a flat pond: the time series of :func:`simulate_column`."""

    time: np.ndarray            # end of each step [s]
    depth: np.ndarray           # surface water [m]
    infiltrated: np.ndarray     # cumulative [m]
    evaporated: np.ndarray      # cumulative [m]
    rain: np.ndarray            # cumulative [m]
    capacity: np.ndarray        # [m/s]

    @property
    def runoff_ratio(self) -> float:
        """Share of the rain that is left at the surface at the end."""
        total = float(self.rain[-1])
        return float(self.depth[-1]) / total if total > 0 else 0.0


def simulate_column(soil: Soil, rain_rate, evaporation_rate=None, *, duration: float,
                    dt: float = 1.0, kernel: str = "sadeghi",
                    ponding_by_runon: bool = True,
                    runon_depth: float = 0.001) -> ColumnResult:
    """Run the algorithm on one node whose surface water stays where it falls.

    *rain_rate* and *evaporation_rate* are callables of time [s] returning rates
    [m/s], or numbers. This is a closed, flat cell: what TELEMAC computes on a flat
    closed basin of one soil, which is how the generated routine is checked against
    this reference.
    """
    def _as_fn(value):
        if value is None:
            return lambda _t: 0.0
        if callable(value):
            return value
        return lambda _t, v=float(value): v

    rain_fn, evap_fn = _as_fn(rain_rate), _as_fn(evaporation_rate)
    n = int(math.ceil(duration / dt))
    out = {k: np.zeros(n) for k in ("time", "depth", "infiltrated", "evaporated",
                                    "rain", "capacity")}
    state = NodeState()
    depth = rain_total = 0.0
    for k in range(n):
        t_end = (k + 1) * dt
        t_mid = t_end - 0.5 * dt
        rate = rain_fn(t_mid)
        result = step(state, soil, t_end=t_end, dt=dt, rain=rate,
                      evaporation=evap_fn(t_mid), depth=depth, rain_total=rain_total,
                      kernel=kernel, ponding_by_runon=ponding_by_runon,
                      runon_depth=runon_depth)
        state = result.state
        depth = max(depth + result.source * dt, 0.0)
        rain_total += rate * dt
        out["time"][k] = t_end
        out["depth"][k] = depth
        out["infiltrated"][k] = state.infiltrated
        out["evaporated"][k] = state.evaporated
        out["rain"][k] = rain_total
        out["capacity"][k] = result.capacity
    return ColumnResult(**out)


def exact_ponded_infiltration(ks: float, a: float, rain: float, t: float) -> float:
    """Cumulative infiltration [m] under constant rain, exact (Mein and Larson, 1973).

    All rain infiltrates until ponding at ``F_p = a Ks / (i - Ks)``; afterwards ``F``
    solves ``F - a ln(1 + F/a) = Ks (t - t_p) + F_p - a ln(1 + F_p/a)``. *ks* and
    *rain* in m/s, *a* in m, *t* in s.
    """
    from scipy.optimize import brentq

    if rain <= ks:
        return rain * t
    f_p = a * ks / (rain - ks)
    t_p = f_p / rain
    if t <= t_p:
        return rain * t
    target = ks * (t - t_p) + f_p - a * math.log1p(f_p / a)
    return float(brentq(lambda f: f - a * math.log1p(f / a) - target, f_p,
                        f_p + rain * t + a))
