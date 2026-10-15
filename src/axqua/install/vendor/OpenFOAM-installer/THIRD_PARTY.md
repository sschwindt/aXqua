# Sources, reproducibility and licenses

The installer/control Python and PowerShell code is MIT-licensed (LICENSE). `cpp/olsenRatingCurve.H` and the generated modified Olsen solver are **GPL-3.0-or-later**, matching Olsen's solver; the GPL text is included in `LICENSES/GPL-3.0-or-later.txt`. The installer downloads dependencies rather than relicensing them. Preserve all upstream notices; redistribution of compiled/source bundles requires complying with their own licenses.

| Component | Source / pin | License |
|---|---|---|
| OpenCFD OpenFOAM | [official v2406 source archives](https://dl.openfoam.com/source/v2406/); June 2024 base release, not Debian patch 260127 | GPLv3 or later |
| ThirdParty | matching `ThirdParty-v2406.tgz`; each bundled dependency retains its own terms | multiple |
| Olsen | [sediDriftFoam](https://www.pvv.ntnu.no/~nilsol/sediDriftFoam/sourceCode/), [sediDriftFoam2](https://www.pvv.ntnu.no/~nilsol/sediDriftFoam2/sourceCode/); file SHA256 snapshot 2026-10-05, the source files shipped in `olsen-sources/` | GPLv3 or later |
| BAW HydBCsForOF | [commit e6f9c3130696122e59bb79e7adcde55aa108b395](https://github.com/baw-de/HydBCsForOF/tree/e6f9c3130696122e59bb79e7adcde55aa108b395) | GPLv3 |
| ParaView | OS-matched package from configured signed Debian/Ubuntu apt repositories | BSD-style; dependencies retain own terms |
| VisIt-DAV | [official v3.5.0](https://github.com/visit-dav/visit/releases/tag/v3.5.0); Debian12/Ubuntu22/Ubuntu24 x86-64 assets | BSD-3-Clause; bundled dependencies retain own terms |

Olsen's source files are shipped unchanged in `olsen-sources/` and copied from there after their SHA256 is checked; the download from his site is used only for a file that is not shipped (see `olsen-sources/README.md`). Archive hashes are pinned in `sediment_installer/installer.py`; Olsen file hashes in `olsen_sources.json`; VisIt hashes in `visualization.py`. The OpenFOAM/ThirdParty hashes were computed from the official HTTPS archives; VisIt digests came from official release metadata. These detect changed bytes, not independent cryptographic publisher signatures. ParaView integrity is supplied by apt's signed repository metadata; its version follows your OS/security updates and is recorded by dpkg, not a universal cross-OS pin.

Downloads whose bytes change fail closed. Review changed upstream code and update source/patch pins deliberately; do not replace checksums blindly. Olsen's optional case download is unversioned: its acquisition hashes are recorded after download, not claimed as preauthenticated reference values. Installed `receipt.json`, `build-environment.txt` and logs retain source pins, visualization package/version, compiler, MPI and API/patch/ABI. Existing system v2406 can be reused explicitly; default builds a separate user-local base v2406.

BAW's library filename contains `v2412`, but it is compiled against the selected v2406 ABI. Its README lists v2212–v2412 compatibility; a runtime smoke test is still required. Cite Thorenz (2024), [DOI 10.3929/ethz-b-000675949](https://doi.org/10.3929/ethz-b-000675949), for BAW methods, and Olsen's [2023](https://doi.org/10.2166/hydro.2023.309)/[2025](https://doi.org/10.2166/hydro.2025.059) papers for the sediment solvers.

The rating extension is a distinct mesh-stage coupling, **not** a port of BAW's physical-pressure/VOF BC. It cannot create a Hirano active layer, MPM/Einstein closure, conservative horizontal Exner discretization, or validated unsteady flood-routing model. See docs/RATING_CURVE.md before claiming hydraulic/morphological validity.
