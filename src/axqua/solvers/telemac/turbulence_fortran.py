"""Fortran routines aXqua generates for the turbulence models of TELEMAC-2D.

A routine of a given name in the ``FORTRAN FILE`` folder (``<model>/<user_fortran_dir>/``) replaces the one TELEMAC ships. aXqua writes two, each only for the model that needs it (:func:`write_routines`, called by :func:`axqua.steering.write_cas`), and removes them again when the case changes its model. A file of the same name that aXqua did not write is the user's and is never touched. Neither routine goes into TELEMAC-3D (:func:`three_d_routines`).

**Depth-averaged LES** (``TURBULENCE MODEL : 4``, ``smagor.f``). A depth-averaged model resolves at best the horizontal eddies from the depth scale up. The eddies smaller than the depth are three-dimensional, and in a river they are generated at the bed; no refinement of the plan resolves them. TELEMAC-2D's Smagorinski model computes only the sub-grid viscosity of the resolved horizontal eddies,

.. math:: \\nu_{SGS} = (C_s \\Delta)^2 \\sqrt{2 S_{ij} S_{ij}}, \\qquad C_s = 0.1,

and leaves out the turbulence generated at the bed. Depth-averaged large-eddy simulation adds it as an eddy viscosity of the bed shear (Hinterberger, Froehlich and Rodi 2007, J. Hydraul. Eng. 133(8)):

.. math:: \\nu_t = \\nu + (C_s \\Delta)^2 \\sqrt{2 S_{ij} S_{ij}} + \\alpha\\, u_* h, \\qquad u_* = \\sqrt{C_f/2}\\,|U|,

with ``alpha = hydrodynamics.les_bed_coefficient`` (0.0667 = kappa/6, the depth average of the parabolic eddy viscosity, the default of TELEMAC's own mixing-length model) and the friction coefficient ``C_f`` that TELEMAC computes from the bed roughness of each node. The routine is the v9.1.1 ``smagor.f`` with the bed term added. On the Inn KB15 baseline mesh (300 s from the pre-wetted start) it raised the median eddy viscosity of the flowing water from 0.0021 to 0.0062 m2/s.

**k-epsilon on a bed that wets and dries** (``TURBULENCE MODEL : 3``, ``corvis.f``). TELEMAC-2D computes the viscosity as ``C_mu k^2 / epsilon`` at every node, dry ones included, clips epsilon only from below (1e-8) and k not at all from above, and cuts the shear production only below 2 cm of water (``kepsil.f``). A run continued from a pre-wetted or dry-start state also starts with k = 0 and epsilon at its floor. On the Inn KB15 ladder (pre-wetted, GMRES for k-epsilon) this diverged twice: on the coarse mesh k reached 147 m2/s2 at a dry node after 12 s, with an eddy viscosity of 57,000 m2/s, and the run stopped after 26 s with ``LIMIT VALUES TRESPASSED``; on the mesh finer by 1.3, k grew from 0.18 to 482 m2/s2 within a second at a node 0.5 m deep near the outflow while epsilon stayed at 0.2, and the run stopped after 10 s. ``CORVIS``, the hook TELEMAC calls right after the viscosity is computed, therefore (1) resets k and epsilon to their floors and the viscosity to the molecular value at the nodes shallower than ``hydrodynamics.keps_shallow_depth`` (0.02 m, TELEMAC's own threshold for the shear production; its Spalart-Allmaras model does the same below 1 cm) and (2) keeps the turbulent length scale ``C_mu^(3/4) k^(3/2) / epsilon`` at most ``hydrodynamics.keps_length_limit`` (1) depths by raising epsilon. In the steady flow the length is about 3 cm against a depth of 0.9 m, so the limiter acts only in the start-up transient. With both, the coarse, baseline and finer KB15 meshes ran without a single failure of the k-epsilon solve (against 655 to 785 in the first 1,000 s without), at a median viscosity of 0.0025 to 0.004 m2/s in the flowing water.
"""

from __future__ import annotations

import logging
from pathlib import Path

from axqua.config import Config

log = logging.getLogger("axqua")

#: First characters of every generated routine; only such a file is replaced or removed.
MARKER = "!     aXqua generated:"
#: The routine of each model.
SMAGOR = "smagor.f"
CORVIS = "corvis.f"
ROUTINES = (SMAGOR, CORVIS)


def _dbl(value: float) -> str:
    return f"{float(value):.6E}".replace("E", "D")


def smagor_text(alpha: float) -> str:
    """Source of the ``SMAGOR`` subroutine with the bed term ``alpha u* h`` (fixed form)."""
    return f"""{MARKER} depth-averaged LES, Smagorinski + bed turbulence
!     Written by aXqua (axqua.solvers.telemac.turbulence_fortran) for
!     TELEMAC-2D v9.1.1. It replaces the shipped smagor.f: the same
!     sub-grid viscosity of the resolved horizontal eddies, plus the
!     eddy viscosity ALPHAB*U**H of the turbulence generated at the
!     bed, which a depth-averaged mesh never resolves (depth-averaged
!     LES, Hinterberger et al. 2007).
!     NU = PROPNU + CS2*(2 SIJ SIJ)**0.5*(MESH SIZE)**2 + ALPHAB*U**H
!     U* = SQRT(CF/2) * |U|
!
      SUBROUTINE SMAGOR
     &(VISC,U,V,MESH,T1,T2,T3,T4,MSK,MASKEL,PROPNU)
!
      USE BIEF
      USE DECLARATIONS_SPECIAL
      USE DECLARATIONS_TELEMAC2D, ONLY : CF,H
!
      IMPLICIT NONE
!
      LOGICAL, INTENT(IN) :: MSK
      DOUBLE PRECISION, INTENT(IN)   :: PROPNU
      TYPE(BIEF_MESH), INTENT(INOUT) :: MESH
      TYPE(BIEF_OBJ), INTENT(INOUT)  :: VISC,T1,T2,T3,T4
      TYPE(BIEF_OBJ), INTENT(IN)     :: MASKEL,U,V
!
      INTEGER N,NPOIN,NBEDLE,IELMU
      DOUBLE PRECISION CS,CS2,ALPHAB,USBEDL,HBEDLE
!
      INTRINSIC SQRT,MAX,MIN
!
      CS = 0.1D0
      CS2 = CS**2
      ALPHAB = {_dbl(alpha)}
!
      IELMU = U%ELM
!
!     GRADIENTS TIMES THE INTEGRAL OF THE TEST FUNCTIONS, TAKEN AS THE
!     SQUARE OF THE MESH SIZE (AS IN THE SHIPPED ROUTINE)
!
      CALL VECTOR(T1,'=','GRADF          X',IELMU,
     &            1.D0,U,U,U,U,U,U,MESH,MSK,MASKEL)
      CALL VECTOR(T2,'=','GRADF          Y',IELMU,
     &            1.D0,U,U,U,U,U,U,MESH,MSK,MASKEL)
      CALL VECTOR(T3,'=','GRADF          X',IELMU,
     &            1.D0,V,V,V,V,V,V,MESH,MSK,MASKEL)
      CALL VECTOR(T4,'=','GRADF          Y',IELMU,
     &            1.D0,V,V,V,V,V,V,MESH,MSK,MASKEL)
!
      IF(NCSIZE.GT.1) THEN
        CALL PARCOM(T1,2,MESH)
        CALL PARCOM(T2,2,MESH)
        CALL PARCOM(T3,2,MESH)
        CALL PARCOM(T4,2,MESH)
      ENDIF
!
      NPOIN = VISC%DIM1
!
      DO N = 1,NPOIN
        VISC%R(N)=PROPNU+SQRT((2*T1%R(N)**2+2*T4%R(N)**2
     &                                       +(T2%R(N)+T3%R(N))**2))*CS2
      ENDDO
!
!     TURBULENCE GENERATED AT THE BED, ON THE LINEAR NODES
!
      NBEDLE = MIN(NPOIN,H%DIM1,CF%DIM1,U%DIM1,V%DIM1)
      DO N = 1,NBEDLE
        HBEDLE = MAX(H%R(N),0.D0)
        USBEDL = SQRT(MAX(0.5D0*CF%R(N),0.D0)*(U%R(N)**2+V%R(N)**2))
        VISC%R(N) = VISC%R(N) + ALPHAB*USBEDL*HBEDLE
      ENDDO
!
      RETURN
      END
"""


def corvis_text(shallow: float, length_limit: float) -> str:
    """Source of the ``CORVIS`` subroutine: the k-epsilon reset on shallow nodes and the length-scale limiter (fixed form)."""
    return f"""{MARKER} k-epsilon: shallow nodes, length <= depth
!     Written by aXqua (axqua.solvers.telemac.turbulence_fortran) for
!     TELEMAC-2D v9.1.1. TELEMAC calls CORVIS right after it computes
!     the viscosity CMU*K**2/EPSILON.
!     1. On nodes shallower than HSHALW, dry ones included, k and
!        epsilon go back to their floors and the viscosity to the
!        molecular one: there k-epsilon has no physical meaning.
!     2. Elsewhere the turbulent length scale CMU**0.75*K**1.5/EPSILON
!        is kept at most RLMAXK depths, by raising epsilon: the run
!        starts with k = 0 and epsilon at its floor, and in the
!        start-up transient k grows far faster than epsilon follows.
!        In the steady flow the limiter does not act.
!
      SUBROUTINE CORVIS
!
      USE BIEF
      USE DECLARATIONS_SPECIAL
      USE DECLARATIONS_TELEMAC2D, ONLY : ITURB,AK,EP,H,VISC,KMIN,EMIN,
     &                                   PROPNU,CMU
!
      IMPLICIT NONE
!
      INTEGER N,NSHALW
      DOUBLE PRECISION HSHALW,RLMAXK,CMU34,ELIMKE
!
      IF(ITURB.NE.3) RETURN
      HSHALW = {_dbl(shallow)}
      RLMAXK = {_dbl(length_limit)}
      CMU34 = CMU**0.75D0
      NSHALW = MIN(H%DIM1,AK%DIM1,EP%DIM1,VISC%DIM1)
      DO N = 1,NSHALW
        IF(H%R(N).LT.HSHALW) THEN
          AK%R(N) = KMIN
          EP%R(N) = EMIN
          VISC%R(N) = PROPNU
        ELSEIF(RLMAXK.GT.0.D0) THEN
          ELIMKE = CMU34*MAX(AK%R(N),0.D0)**1.5D0/(RLMAXK*H%R(N))
          IF(EP%R(N).LT.ELIMKE) THEN
            EP%R(N) = ELIMKE
            VISC%R(N) = PROPNU + CMU*AK%R(N)**2/EP%R(N)
          ENDIF
        ENDIF
      ENDDO
!
      RETURN
      END
"""


def is_generated(path: str | Path) -> bool:
    """Whether *path* is a routine aXqua wrote (its first line starts with :data:`MARKER`)."""
    try:
        with open(path, encoding="ascii", errors="replace") as f:
            return f.readline().startswith(MARKER)
    except OSError:
        return False


def routines_for(cfg: Config, model: int) -> dict[str, str]:
    """``{file name: source}`` of the routines TELEMAC-2D *model* needs for *cfg*."""
    h = cfg.hydrodynamics
    if model == 4 and float(getattr(h, "les_bed_coefficient", 0.0)) > 0.0:
        return {SMAGOR: smagor_text(float(h.les_bed_coefficient))}
    shallow = float(getattr(h, "keps_shallow_depth", 0.0))
    limit = float(getattr(h, "keps_length_limit", 0.0))
    if model == 3 and (shallow > 0.0 or limit > 0.0):
        return {CORVIS: corvis_text(shallow, limit)}
    return {}


def write_routines(cfg: Config, model: int) -> Path | None:
    """Write the routines of *model* into ``<model>/<user_fortran_dir>/`` and remove the generated ones it does not need.

    Returns the folder (the ``FORTRAN FILE``) when the model needs a routine, else None. A routine of the same name that aXqua did not write is the user's: it is used as it is, with a warning.
    """
    try:
        folder = Path(cfg.model_path(cfg.user_fortran_dir))
    except Exception:                            # noqa: BLE001 - bare test configs
        return None
    wanted = routines_for(cfg, model)
    for name in ROUTINES:
        path = folder / name
        if name in wanted:
            continue
        if path.exists() and is_generated(path):
            path.unlink()
    if not wanted:
        try:
            folder.rmdir()                       # only when nothing else is in it
        except OSError:
            pass
        return None
    folder.mkdir(parents=True, exist_ok=True)
    for name, text in wanted.items():
        path = folder / name
        if path.exists() and not is_generated(path):
            log.warning("%s is not aXqua's; it is used as it is", path)
            continue
        path.write_text(text, encoding="ascii")
        log.info("  turbulence routine %s (%s)", path, text.splitlines()[0][len(MARKER):].strip())
    return folder


def three_d_routines(folder: str | Path) -> bool:
    """Whether a ``user_fortran`` folder holds Fortran other than the generated 2D routines, i.e. something to link into TELEMAC-3D."""
    folder = Path(folder)
    if not folder.is_dir():
        return False
    for f in folder.iterdir():
        if f.suffix.lower() not in (".f", ".f90") or f.name.startswith("."):
            continue
        if f.name in ROUTINES and is_generated(f):
            continue
        return True
    return False
