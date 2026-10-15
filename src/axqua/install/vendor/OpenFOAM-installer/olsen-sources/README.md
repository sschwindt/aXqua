# Olsen's sediment solvers, as published

These are the source files of `sediDriftFoam` and `sediDriftFoam2` by Nils Reidar B. Olsen (NTNU), unchanged, as published on https://www.pvv.ntnu.no/~nilsol/sediDriftFoam/sourceCode/ and https://www.pvv.ntnu.no/~nilsol/sediDriftFoam2/sourceCode/ (snapshot of 2026-10-05). Each file matches the SHA256 pinned in `../sediment_installer/olsen_sources.json`, and the installer checks that before it copies a file.

They are shipped here because Olsen's site is the only place they are published, it has no versioned releases, and it answered HTTP 502 for every file in October 2026, which stopped every installation at this step. The installer now takes the files from this folder and downloads only a file that is missing here.

## License

The solvers are derived from OpenFOAM and are distributed under the **GNU General Public License, version 3 or (at your option) any later version**, as the headers of `sediDriftFoam.C`, `sediDriftFoam1.C` and `sediDriftFoam2.C` state (Copyright (C) 2011-2018 OpenFOAM Foundation, Copyright (C) 2022-2025 Nils Reidar B. Olsen). The files without a header of their own (`Make/*`, `*.H`, `*.h`) are parts of the same programs and are distributed under the same terms. The license text is `../LICENSES/GPL-3.0-or-later.txt`.

The GPL covers these files and the programs built from them. It does not extend to the installer that copies and compiles them, nor to aXqua, which runs the compiled solvers as separate programs: the files are shipped side by side with them as an aggregate, as section 5 of the GPL-3.0 allows.

Cite Olsen's papers when you use the solvers: https://doi.org/10.2166/hydro.2023.309 and https://doi.org/10.2166/hydro.2025.059.
