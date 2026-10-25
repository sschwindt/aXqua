"""Generate ``runoff_greenampt.f``: Green-Ampt infiltration, rain and evaporation.

TELEMAC-2D v9.1.1 calls ``RUNOFF_GREENAMPT`` from ``prosou.f`` when ``RAINFALL-RUNOFF MODEL : 3`` and ``RAIN OR EVAPORATION : YES``. A routine of that name in the ``FORTRAN FILE`` folder replaces the shipped one, which is how this module brings in what TELEMAC lacks (see :mod:`axqua.hydrology.greenampt` for the physics and the papers):

* the infiltration kernel of de Vos et al. (2024): Sadeghi's explicit solution with Chu's ponding for unsteady rain, ponded water infiltrating after the rain, and ponding by run-on as aXqua's extension; or the exact capacity on the infiltrated depth, or TELEMAC's own equations for comparison;
* ``Ks`` and ``a = psi (theta_s - theta_i)`` per node, read by TELEMAC from the geometry as private variables (only ``Ks`` is spatial in the shipped routine);
* a hyetograph and an evaporation series from ``FORMATTED DATA FILE 1`` (``forcing.txt``), where the shipped routine knows one constant rate;
* evaporation of the surface water left after infiltration (the shipped routine stops on negative rain).

The routine computes each step from the state at the start of the step and commits it when the next step begins, because ``PROSOU`` runs once per sub-iteration of a step (``NUMBER OF SUB-ITERATIONS FOR NON-LINEARITIES``): updating the state on every call would count the infiltration of a step several times. At every listing printout it writes the volumes of rain, infiltration and evaporation so far as one line ``AXQUA HYDROLOGY t rain infiltrated evaporated`` [s, m3], which the report of the run reads (:mod:`axqua.hydrology.report`).

The per-node update is line for line the one of :func:`axqua.hydrology.greenampt.step`; the tests compare a TELEMAC run on a closed flat basin with that reference.
"""

from __future__ import annotations

import logging
from pathlib import Path

from axqua.config import Config
from axqua.hydrology.greenampt import KERNELS, SADEGHI_C0, SADEGHI_C1, SADEGHI_C2
from axqua.solvers.telemac.fortran import FIXED_FORM_LIMIT, _dbl

log = logging.getLogger("axqua")

#: The private variables, in the order of TELEMAC's private arrays: name and unit, 16 characters each. The first three are read from the geometry, the fourth is written by the routine. TELEMAC matches the first 16 characters (fonstr.f).
PRIVATE_VARIABLES = (
    ("KS", "MM/H"),
    ("GA SUCTION A", "M"),
    ("GA DELTA THETA", ""),
    ("INFILTRATED", "M"),
)

#: Graphic printout letters of the four private arrays in TELEMAC-2D.
PRIVATE_LETTERS = ("N", "O", "R", "Z")

#: The marker of the volume line in the listing.
BUDGET_MARKER = "AXQUA HYDROLOGY"


def private_name(index: int) -> str:
    """The 32-character name of private variable *index* (0-based)."""
    name, unit = PRIVATE_VARIABLES[index]
    return f"{name:<16}{unit:<16}"


def write_runoff_routine(cfg: Config) -> Path:
    """Write ``<model>/<user_fortran>/runoff_greenampt.f`` for the case; returns the folder (the ``FORTRAN FILE``)."""
    h = cfg.hydrology
    kernel = h.green_ampt if h.infiltration == "green-ampt" else "exact"
    if kernel not in KERNELS:
        raise ValueError(f"unknown Green-Ampt solution {kernel!r}")
    kernel_id = KERNELS.index(kernel) + 1
    body: list[str] = []

    def stmt(text: str) -> None:
        line = "      " + text
        if len(line) > FIXED_FORM_LIMIT:
            raise ValueError(f"generated Fortran line exceeds 72 columns: {line!r}")
        body.append(line)

    def cont(text: str) -> None:
        line = "     &  " + text
        if len(line) > FIXED_FORM_LIMIT:
            raise ValueError(f"generated Fortran line exceeds 72 columns: {line!r}")
        body.append(line)

    def comment(text: str = "") -> None:
        line = f"!     {text}" if text else "!"
        if len(line) > FIXED_FORM_LIMIT:
            raise ValueError(f"generated Fortran comment exceeds 72 columns: {line!r}")
        body.append(line)

    comment("axqua-generated RUNOFF_GREENAMPT (replaces TELEMAC's routine)")
    comment(f"  infiltration: {kernel}"
            + (" (de Vos et al. 2024, Sadeghi + Chu)" if kernel == "sadeghi" else ""))
    comment(f"  ponding by run-on: {'yes' if h.ponding_by_runon else 'no'}"
            f" (deeper than {h.runon_depth:g} m)")
    comment("  Ks [mm/h], a = psi (theta_s - theta_i) [m] and dtheta per")
    comment("  node from the private variables of the geometry; rain and")
    comment("  evaporation [mm/h] from FORMATTED DATA FILE 1, constant from")
    comment("  each row to the next. The state of a step is committed when")
    comment("  the next step begins: PROSOU runs once per sub-iteration.")
    body.append("      SUBROUTINE RUNOFF_GREENAMPT")
    body.append("     & (PLUIE,ACCINF,ACCROFF,ACCROF_OLD,RAIN_MPS,KS,")
    body.append("     &  RAIN_HDUR,FILES,FO2,NPOIN,MESH,RFM,FCAPA)")
    stmt("USE BIEF")
    stmt("USE DECLARATIONS_TELEMAC2D, ONLY: DT,LT,AT,HN,ENTET,T2DFO1,")
    cont("PRIVE,VOLU2D")
    stmt("USE INTERFACE_PARALLEL, ONLY : P_SUM")
    stmt("USE DECLARATIONS_SPECIAL")
    stmt("IMPLICIT NONE")
    stmt("INTEGER         , INTENT(IN)    :: NPOIN,FO2")
    stmt("DOUBLE PRECISION, INTENT(IN)    :: RAIN_MPS,RAIN_HDUR")
    stmt("DOUBLE PRECISION, INTENT(INOUT) :: ACCROFF(NPOIN),RFM(NPOIN)")
    stmt("DOUBLE PRECISION, INTENT(INOUT) :: FCAPA(NPOIN)")
    stmt("TYPE(BIEF_OBJ)  , INTENT(INOUT) :: PLUIE,ACCROF_OLD,KS")
    stmt("TYPE(BIEF_OBJ)  , INTENT(INOUT) :: ACCINF")
    stmt("TYPE(BIEF_FILE) , INTENT(IN)    :: FILES(*)")
    stmt("TYPE(BIEF_MESH) , INTENT(INOUT) :: MESH")
    # every local carries the HMP_ prefix, like the generated USER_RAIN: BIEF and
    # DECLARATIONS_SPECIAL bring a large namespace and a clash does not compile
    stmt(f"INTEGER, PARAMETER :: HMP_KRN = {kernel_id}")
    stmt(f"LOGICAL, PARAMETER :: HMP_RUN = "
         f"{'.TRUE.' if h.ponding_by_runon else '.FALSE.'}")
    stmt(f"DOUBLE PRECISION, PARAMETER :: HMP_HRN = {_dbl(float(h.runon_depth))}")
    stmt(f"DOUBLE PRECISION, PARAMETER :: HMP_SC0 = {_dbl(SADEGHI_C0)}")
    stmt(f"DOUBLE PRECISION, PARAMETER :: HMP_SC1 = {_dbl(SADEGHI_C1)}")
    stmt(f"DOUBLE PRECISION, PARAMETER :: HMP_SC2 = {_dbl(SADEGHI_C2)}")
    stmt("DOUBLE PRECISION, PARAMETER :: HMP_BIG = 1.D30")
    stmt("DOUBLE PRECISION, ALLOCATABLE, SAVE :: HMP_TT(:),HMP_RR(:)")
    stmt("DOUBLE PRECISION, ALLOCATABLE, SAVE :: HMP_EE(:)")
    stmt("DOUBLE PRECISION, ALLOCATABLE, SAVE :: HMP_F0(:),HMP_F1(:)")
    stmt("DOUBLE PRECISION, ALLOCATABLE, SAVE :: HMP_E0(:),HMP_E1(:)")
    stmt("DOUBLE PRECISION, ALLOCATABLE, SAVE :: HMP_TP0(:),HMP_TP1(:)")
    stmt("DOUBLE PRECISION, ALLOCATABLE, SAVE :: HMP_FP0(:),HMP_FP1(:)")
    stmt("LOGICAL, ALLOCATABLE, SAVE :: HMP_PD0(:),HMP_PD1(:)")
    stmt("LOGICAL, SAVE :: HMP_DEJA = .FALSE.")
    stmt("INTEGER, SAVE :: HMP_LT = -1, HMP_LTP = -1, HMP_IX = 1")
    stmt("INTEGER, SAVE :: HMP_NT = 0")
    stmt("DOUBLE PRECISION, SAVE :: HMP_P0 = 0.D0, HMP_P1 = 0.D0")
    stmt("INTEGER HMP_I,HMP_UL")
    stmt("DOUBLE PRECISION HMP_TM,HMP_RI,HMP_RE,HMP_KS,HMP_A,HMP_DTH")
    stmt("DOUBLE PRECISION HMP_H0,HMP_SUP,HMP_INF,HMP_CAP,HMP_EVA,HMP_F")
    stmt("DOUBLE PRECISION HMP_TGA,HMP_C1,HMP_VR,HMP_VI,HMP_VE,HMP_AR")
    comment()
    comment("first call: read the forcing table, allocate the state")
    stmt("IF(.NOT.HMP_DEJA) THEN")
    stmt("  HMP_UL=FILES(T2DFO1)%LU")
    stmt("  REWIND(HMP_UL)")
    stmt("  READ(HMP_UL,*)")
    stmt("  READ(HMP_UL,*)")
    stmt("  READ(HMP_UL,*) HMP_NT")
    stmt("  IF(HMP_NT.LT.2) THEN")
    stmt("    WRITE(LU,*) 'RUNOFF_GREENAMPT: FORCING NEEDS 2 ROWS AT LEAST'")
    stmt("    CALL PLANTE(1)")
    stmt("    STOP")
    stmt("  ENDIF")
    stmt("  ALLOCATE(HMP_TT(HMP_NT),HMP_RR(HMP_NT),HMP_EE(HMP_NT))")
    stmt("  DO HMP_I=1,HMP_NT")
    stmt("    READ(HMP_UL,*) HMP_TT(HMP_I),HMP_RR(HMP_I),HMP_EE(HMP_I)")
    stmt("  ENDDO")
    stmt("  ALLOCATE(HMP_F0(NPOIN),HMP_F1(NPOIN),HMP_E0(NPOIN))")
    stmt("  ALLOCATE(HMP_E1(NPOIN),HMP_TP0(NPOIN),HMP_TP1(NPOIN))")
    stmt("  ALLOCATE(HMP_FP0(NPOIN),HMP_FP1(NPOIN))")
    stmt("  ALLOCATE(HMP_PD0(NPOIN),HMP_PD1(NPOIN))")
    stmt("  HMP_F0=0.D0")
    stmt("  HMP_F1=0.D0")
    stmt("  HMP_E0=0.D0")
    stmt("  HMP_E1=0.D0")
    stmt("  HMP_TP0=0.D0")
    stmt("  HMP_TP1=0.D0")
    stmt("  HMP_FP0=0.D0")
    stmt("  HMP_FP1=0.D0")
    stmt("  HMP_PD0=.FALSE.")
    stmt("  HMP_PD1=.FALSE.")
    stmt("  DO HMP_I=1,NPOIN")
    stmt("    KS%R(HMP_I)=PRIVE%ADR(1)%P%R(HMP_I)")
    stmt("  ENDDO")
    stmt("  HMP_DEJA=.TRUE.")
    stmt("  WRITE(LU,*) 'RUNOFF_GREENAMPT (AXQUA): ',HMP_NT,")
    cont(f"' FORCING ROWS, KERNEL {kernel.upper()}'")
    stmt("ENDIF")
    comment()
    comment("commit the step before, once per step")
    stmt("IF(LT.NE.HMP_LT) THEN")
    stmt("  IF(HMP_LT.GE.0) THEN")
    stmt("    HMP_F0=HMP_F1")
    stmt("    HMP_E0=HMP_E1")
    stmt("    HMP_TP0=HMP_TP1")
    stmt("    HMP_FP0=HMP_FP1")
    stmt("    HMP_PD0=HMP_PD1")
    stmt("    HMP_P0=HMP_P1")
    stmt("  ENDIF")
    stmt("  HMP_LT=LT")
    stmt("ENDIF")
    comment()
    comment("rain and evaporation [m/s] in force at the middle of [AT-DT, AT]")
    stmt("HMP_TM=AT-0.5D0*DT")
    stmt("HMP_RI=0.D0")
    stmt("HMP_RE=0.D0")
    stmt("IF(HMP_TM.GE.HMP_TT(1)) THEN")
    stmt("  DO")
    stmt("    IF(HMP_IX.GE.HMP_NT) EXIT")
    stmt("    IF(HMP_TM.LT.HMP_TT(HMP_IX+1)) EXIT")
    stmt("    HMP_IX=HMP_IX+1")
    stmt("  ENDDO")
    stmt("  IF(HMP_IX.LT.HMP_NT) THEN")
    stmt("    HMP_RI=MAX(HMP_RR(HMP_IX),0.D0)/3.6D6")
    stmt("    HMP_RE=MAX(HMP_EE(HMP_IX),0.D0)/3.6D6")
    stmt("  ENDIF")
    stmt("ENDIF")
    stmt("HMP_P1=HMP_P0+HMP_RI*DT")
    comment()
    comment("per node: infiltration, evaporation, net surface source")
    stmt("DO HMP_I=1,NPOIN")
    stmt("  HMP_KS=PRIVE%ADR(1)%P%R(HMP_I)/3.6D6")
    stmt("  HMP_A=MAX(PRIVE%ADR(2)%P%R(HMP_I),0.D0)")
    stmt("  HMP_DTH=MAX(PRIVE%ADR(3)%P%R(HMP_I),0.D0)")
    stmt("  HMP_F=HMP_F0(HMP_I)")
    stmt("  HMP_H0=MAX(HN%R(HMP_I),0.D0)")
    stmt("  HMP_SUP=HMP_RI*DT+HMP_H0")
    stmt("  HMP_INF=0.D0")
    stmt("  HMP_CAP=0.D0")
    stmt("  HMP_PD1(HMP_I)=HMP_PD0(HMP_I)")
    stmt("  HMP_TP1(HMP_I)=HMP_TP0(HMP_I)")
    stmt("  HMP_FP1(HMP_I)=HMP_FP0(HMP_I)")
    stmt("  IF(HMP_KS.GT.0.D0) THEN")
    stmt("    IF(HMP_KRN.EQ.3) THEN")
    comment("      TELEMAC's equations: c = P - F, only rain infiltrates")
    stmt("      IF(HMP_F.LE.0.D0) THEN")
    stmt("        HMP_CAP=1.D6")
    stmt("      ELSE")
    stmt("        HMP_CAP=HMP_KS*(1.D0+(HMP_A+(HMP_P0-HMP_F)*HMP_DTH)/HMP_F)")
    stmt("      ENDIF")
    stmt("      HMP_INF=MIN(HMP_RI*DT,HMP_CAP*DT)")
    stmt("    ELSEIF(HMP_KRN.EQ.2.OR.(.NOT.HMP_PD0(HMP_I))) THEN")
    comment("      exact capacity on the infiltrated depth")
    stmt("      IF(HMP_F.LE.0.D0) THEN")
    stmt("        HMP_CAP=HMP_BIG")
    stmt("      ELSE")
    stmt("        HMP_CAP=HMP_KS*(1.D0+HMP_A/HMP_F)")
    stmt("      ENDIF")
    stmt("      HMP_INF=MIN(HMP_SUP,HMP_CAP*DT)")
    stmt("    ELSE")
    comment("      ponded: Sadeghi et al. (2024), time on the ponded curve")
    stmt("      HMP_TGA=HMP_TM-HMP_TP0(HMP_I)")
    stmt("      IF(HMP_A.GT.0.D0.AND.HMP_FP0(HMP_I).GT.0.D0) THEN")
    stmt("        HMP_TGA=HMP_TGA+(HMP_FP0(HMP_I)-HMP_A*")
    cont("LOG(1.D0+HMP_FP0(HMP_I)/HMP_A))/HMP_KS")
    stmt("      ENDIF")
    stmt("      HMP_TGA=MAX(HMP_TGA,0.5D0*DT,1.D-9)")
    stmt("      IF(HMP_A.LE.0.D0) THEN")
    stmt("        HMP_CAP=HMP_KS")
    stmt("      ELSE")
    stmt("        HMP_C1=HMP_SC0+HMP_SC1*SQRT(1.D0+2.D0*HMP_SC2*HMP_A")
    cont("/(HMP_KS*HMP_TGA))")
    stmt("        HMP_CAP=HMP_KS*(1.D0+2.D0*HMP_A/(HMP_KS*HMP_TGA*HMP_C1))")
    stmt("      ENDIF")
    stmt("      HMP_INF=MIN(HMP_SUP,HMP_CAP*DT)")
    stmt("    ENDIF")
    stmt("  ENDIF")
    stmt("  HMP_INF=MAX(HMP_INF,0.D0)")
    stmt("  HMP_EVA=MIN(HMP_RE*DT,MAX(HMP_SUP-HMP_INF,0.D0))")
    stmt("  HMP_F1(HMP_I)=HMP_F+HMP_INF")
    stmt("  HMP_E1(HMP_I)=HMP_E0(HMP_I)+HMP_EVA")
    comment("  ponding (Chu's criterion, or water from upslope), sadeghi only")
    stmt("  IF(HMP_KRN.EQ.1.AND.HMP_KS.GT.0.D0.AND.")
    cont(".NOT.HMP_PD0(HMP_I)) THEN")
    stmt("    IF((HMP_RI.GT.HMP_KS.AND.HMP_F1(HMP_I).GE.")
    cont("HMP_A*HMP_KS/(HMP_RI-HMP_KS)).OR.")
    cont("(HMP_RUN.AND.HMP_H0.GT.HMP_HRN)) THEN")
    stmt("      HMP_PD1(HMP_I)=.TRUE.")
    stmt("      HMP_TP1(HMP_I)=AT")
    stmt("      HMP_FP1(HMP_I)=HMP_F1(HMP_I)")
    stmt("    ENDIF")
    stmt("  ENDIF")
    stmt("  PLUIE%R(HMP_I)=(HMP_RI*DT-HMP_INF-HMP_EVA)/DT")
    stmt("  FCAPA(HMP_I)=MIN(HMP_CAP,HMP_BIG)")
    stmt("  RFM(HMP_I)=HMP_RI*DT")
    stmt("  ACCINF%R(HMP_I)=HMP_F1(HMP_I)")
    stmt("  PRIVE%ADR(4)%P%R(HMP_I)=HMP_F1(HMP_I)")
    stmt("ENDDO")
    comment()
    comment("volumes so far, once per listing printout")
    stmt("IF(ENTET.AND.LT.NE.HMP_LTP) THEN")
    stmt("  HMP_LTP=LT")
    stmt("  HMP_AR=0.D0")
    stmt("  HMP_VI=0.D0")
    stmt("  HMP_VE=0.D0")
    stmt("  DO HMP_I=1,NPOIN")
    stmt("    HMP_AR=HMP_AR+VOLU2D%R(HMP_I)")
    stmt("    HMP_VI=HMP_VI+HMP_F1(HMP_I)*VOLU2D%R(HMP_I)")
    stmt("    HMP_VE=HMP_VE+HMP_E1(HMP_I)*VOLU2D%R(HMP_I)")
    stmt("  ENDDO")
    stmt("  IF(NCSIZE.GT.1) THEN")
    stmt("    HMP_AR=P_SUM(HMP_AR)")
    stmt("    HMP_VI=P_SUM(HMP_VI)")
    stmt("    HMP_VE=P_SUM(HMP_VE)")
    stmt("  ENDIF")
    stmt("  HMP_VR=HMP_P1*HMP_AR")
    stmt(f"  WRITE(LU,'(A,4(1X,ES16.8))') '{BUDGET_MARKER}',AT,")
    cont("HMP_VR,HMP_VI,HMP_VE")
    stmt("ENDIF")
    stmt("RETURN")
    stmt("END")

    out_dir = cfg.model_path(cfg.user_fortran_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "runoff_greenampt.f"
    path.write_text("\n".join(body) + "\n")
    log.info("  wrote RUNOFF_GREENAMPT (%s%s) -> %s", kernel,
             ", ponding by run-on" if h.ponding_by_runon and kernel == "sadeghi" else "",
             path)
    return out_dir
