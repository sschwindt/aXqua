# Where this copy comes from

This folder is a copy of `OpenFOAM-installer/` of https://github.com/Ecohydraulics/numerical-software-installers at commit `12fcb7e8b19d355a63d983ddcbd25d10b3abc398`, shipped with aXqua so that the OpenFOAM installation does not depend on Olsen's web site. The installer is MIT-licensed (`LICENSE`); what else is in here and under which license is in `THIRD_PARTY.md`.

Changed against that commit:

1. `olsen-sources/` (new): Olsen's source files, unchanged, GPL-3.0-or-later (see its `README.md`). `sediment_installer/installer.py` `acquire_olsen` copies them from there after checking the pinned SHA256 and downloads only a file that is missing; a failed download is reported as an installer error instead of a Python traceback.
2. `sediment_installer/installer.py`: an f-string with nested double quotes (in `write_launchers`) is split, so the installer runs on Python 3.10 and 3.11 (Debian 12, Ubuntu 22.04) and not only from 3.12 on.
3. `sediment_installer/core.py`: `clean_environment` puts `/usr/local/bin` after `/usr/bin:/bin`, so an older compiler in `/usr/local/bin` does not build the solvers against a packaged OpenFOAM (the start scripts already use `/usr/bin:/bin`).
4. `tests/test_olsen_bundle.py` (new); the pin of the search path in `tests/test_core.py`; `test_nul_link_target_is_rejected_before_normalization` no longer mocks `normpath` away entirely, which broke `shutil.rmtree` from Python 3.12 on (the test failed there in the original as well).
5. `sediment_installer/core.py`: `claim_prefix` accepts an existing **empty** folder like an absent one (test `test_an_empty_prefix_is_claimed_like_an_absent_one`). aXqua installs for all users by having an administrator create the folder for the user before the build, since the installer must not run as root.

The installer refuses to continue an installation folder that an earlier version of it started (its identity is a hash of its own files), so after an update a failed installation is repeated in a new folder.
