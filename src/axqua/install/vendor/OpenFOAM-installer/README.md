# OpenFOAM sediment stack

One installer for **Debian 12 / Ubuntu 22.04 or 24.04 / matching derivatives**, and **Windows via WSL2 + WSLg** (x86-64). Builds OpenCFD **v2406**, Olsen's `sediDriftFoam` and `sediDriftFoam2`, a separate experimental `sediDriftFoam2Rating`, and BAW's boundary library; installs distribution ParaView and official VisIt **3.5.0**. Requires internet, a normal user with sudo, ~20 GiB free, and preferably 8 GiB RAM. Core compilation can take hours.

## Install

Download/clone this repository, open a terminal in it, then (minimal Linux images may first need `sudo apt install python3`):

**Debian/Ubuntu:**
```bash
python3 install.py --install-system-packages --examples --smoke-test
```

**Windows:** first run `wsl --install -d Ubuntu-24.04` in administrator PowerShell, restart if requested, and launch Ubuntu once to create your Linux user. Then, in ordinary PowerShell:
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -InstallSystemPackages -Examples -SmokeTest
```
WSL2 performs the build; WSLg displays both Linux visualization GUIs. Debian 12 WSL users can add `-Distro Debian`. This is not a native MSVC/MinGW build. [WSL setup](https://learn.microsoft.com/windows/wsl/install).

Use `--dry-run` / `-DryRun` to preview; `--jobs 4` / `-Jobs 4` limits compilation. Already have v2406? Add `--reuse-openfoam /usr/lib/openfoam/openfoam2406/etc/bashrc` (Windows: `-ReuseOpenfoam` with the Linux path). No existing OpenFOAM, login files, or user cases are replaced; interrupted builds retain logs/cache. Use a new `--prefix` when changing installer/toolchain pins.

The installer uses `apt-get`, the backward-compatible interface recommended for scripts; `apt` is intended for interactive use. [Debian APT manual](https://manpages.debian.org/bookworm/apt/apt.8.en.html#SCRIPT_USAGE_AND_DIFFERENCES_FROM_OTHER_APT_TOOLS).

Earlier archive and shell-startup failures (`jouleHeatingSource:V`, `/bin/bash: cannot execute binary file`, `pop_var_context`) are corrected. Retry in a new `--prefix` (Windows: `-Prefix`, using a Linux path). No previous cache is required; the installer downloads its own archives. Cache reuse is optional and requires a verified existing directory. [Recovery and diagnostics](docs/RECOVERY.md).

## Run and visualize

In the Linux/WSL terminal:
```bash
source ~/.local/openfoam-sediment-v2406/activate.sh   # opens a clean OpenFOAM shell
cd /path/to/your/case
sediDriftFoam2                                    # serial original moving-bed solver
touch case.foam
paraview case.foam
```
In **ParaView**, select `internalMesh`, `bedWall`, `freeSurface` and `Conc`/`U`/`p`, click **Apply**, then **Play**. For **VisIt**, export per-time geometry and open the printed volume or patch `.visit` file:
```bash
python3 ~/.local/openfoam-sediment-v2406/postprocess.py .
visit
```
VisIt: **Open** a `.visit` file → **Pseudocolor** (`Conc`) → **Draw** → animate. Keep volume and bed/surface sequences separate. [Visualization details](docs/VISUALIZATION.md).

For Olsen's stage–discharge extension, copy `examples/ratingCurveProperties` to your case's `constant/`, replace the example Q/stage values, set `enabled true`, and run **`sediDriftFoam2Rating`**. It is opt-in, serial and quasi-steady; not a validated transient/ALE model. The fixed-mesh first solver has no movable stage. [Required tests and limitations](docs/RATING_CURVE.md).

BAW's native rating BC is for **interFoam**, not Olsen's `p` field. Its prepared case is under the install prefix's `cases/baw-interFoam`; load `lib_BAW_public_BCs_v2412_20260813.so` in `controlDict`. Configure matching `waterLevel_alpha_prgh` rating entries in `p_rgh` and `alpha.water`. [BAW instructions](https://github.com/baw-de/HydBCsForOF).

Installer tests: `python3 -m unittest discover -s tests -v`. GitHub Actions also tests the actual v2406 startup scripts on Ubuntu 22.04/24.04; full compilation and the BAW smoke run require the explicit full-build workflow. [Source pins/licenses](THIRD_PARTY.md). For GitHub, use this folder's contents as the repository root. No automatic simulation of your real case is performed.
