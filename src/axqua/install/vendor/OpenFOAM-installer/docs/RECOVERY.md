# Recover from a failed installation

## Environment-loading errors

The error `etc/config.sh/setup: ... /bin/bash: cannot execute binary file` was caused by installer command arguments leaking into OpenFOAM's environment script. OpenFOAM interprets non-option arguments as settings or files to source; the installer accidentally supplied the command executable `/bin/bash`.

The subsequent `pop_var_context: head of shell_variables not a function context` error arose when `set -e` interrupted a nested source/eval/function chain. In the stock v2406 startup scripts, CGAL's optional GMP/MPFR path checks legitimately return 1 for unset paths. Treating these probes as fatal interrupts initialization; some Bash versions then emit the internal context warning. This is not evidence of a damaged archive or missing CGAL package.

The corrected installer sources OpenFOAM at shell top level with empty positional arguments and with `errexit` and `nounset` temporarily disabled. It restores the arguments and shell options, then checks the source status, selected project, API, and ABI before running build commands. Generated solver launchers use the same loader. Only project-level OpenFOAM preferences are loaded (`FOAM_CONFIG_MODE=o`); unrelated user/group preferences are excluded. Regression tests now exercise the actual checksum-pinned v2406 startup scripts, not only a simulated environment.

These failures occur in environment verification after source extraction but before the installer starts compilation. Downloaded archives and extracted sources should remain intact. Do not modify OpenFOAM's `etc/config.sh/setup`, `/bin/bash`, or their permissions. The corrected installer records probe stdout/stderr in `<prefix>/logs/environment-probe.log`, including failed attempts.

## Archive-name error

The archive error for `jouleHeatingSource:V` was caused by the installer's filename validator, not by Linux Mint or a damaged download. A colon is valid in a Linux/WSL filename. The corrected validator permits it on POSIX systems while retaining native Windows, traversal, and link-containment checks. Do not rename or omit the OpenFOAM file, or change archive checksums.

For this error, extraction stops during archive validation, before compilation. Its temporary extraction directory is removed; the verified archive remains in `cache/`. If `--install-system-packages` was used, APT dependencies and ParaView may already be installed. No existing OpenFOAM installation or shell startup file is changed.

## Download Verification Errors

The former message `Upstream checksum mismatch; source/release may have changed` did not establish that the release had changed or that the installer was outdated. It indicated only that the received bytes differed from the pinned SHA256. An incomplete transfer or a download/error page can produce the same result.

A fresh download from the [official ThirdParty-v2406 endpoint](https://dl.openfoam.com/source/v2406/ThirdParty-v2406.tgz) on 5 October 2026 returned 362,339,537 bytes and matched the existing pin:

```text
7b700c566ecdc8eb665206a510d581e05ec6e47ba52b16b578c68e885bc9576b
```

This verifies the response obtained during that check, not the response received on another machine or from another mirror.

The downloader now requires a complete HTTP 200 response, rejects HTML responses, checks the reported Content-Length when present, and retains SHA256 verification. A mismatch reports the expected and received hashes, received byte count, content type, and final mirror path. Expiring redirect tokens are omitted. The rejected temporary file is removed; verified cache files and extracted sources are not removed.

Do not replace a checksum, disable verification, or edit OpenFOAM sources to address this error. Copy the complete error output if it recurs. A source-build download failure occurs before core compilation, although system packages may already have been installed. The standard installation command remains unchanged. A changed installer requires a fresh prefix as described below; no previous cache is required.

## Build-Path Errors and Missing Logs

An ADIOS2 build warning is optional in the stock ThirdParty build. A subsequent `not located in $WM_PROJECT_DIR/src` error is a separate fatal failure. The stock `src/Allwmake` suppresses stderr from `wmake -check-dir`; its message can indicate a failed directory check or an unavailable `wmake` executable, not only an incorrect environment variable.

The installer creates and opens `logs/openfoam-Allwmake.log` before launching compilation. Ordinary build failure does not remove that file. If the entire reported installation prefix is absent, locate the actual tree before attempting another installation. In the same machine and Linux environment used for the build, this single read-only command locates installation markers and logs:

```bash
find "$HOME/.local" -maxdepth 6 -type f \( -name openfoam-Allwmake.log -o -name .sediment-installer.json \) -print
```

Check whether the build directory was moved, removed, or resides on a mount or machine that is no longer available. Do not rename or remove an installation while a build is running, and do not run multiple installers against the same prefix. An absent path alone does not establish which of these conditions occurred. Do not change ADIOS2 options or bypass the OpenFOAM directory check to address an unavailable installation tree.

### Installation Found in Trash

If the search locates the installation in `~/.local/share/Trash/files/`, its log can be read there without restoring or modifying the tree. For the recovered `initfix` installation:

```bash
tail -n 160 -- "$HOME/.local/share/Trash/files/openfoam-sediment-v2406-initfix/logs/openfoam-Allwmake.log"
```

The Trash location establishes preservation, not who moved the tree or when. In particular, a move after build failure does not explain that failure. Read the corresponding Trash record:

```bash
cat -- "$HOME/.local/share/Trash/info/openfoam-sediment-v2406-initfix.trashinfo"
```

Compare its deletion time with the log's last-write time:

```bash
stat -c '%y %n' -- "$HOME/.local/share/Trash/files/openfoam-sediment-v2406-initfix/logs/openfoam-Allwmake.log"
```

The record's `Path` and `DeletionDate` fields contain the original location and local deletion time. Use the recorded path rather than inferring it from the folder's Trash name. See the [freedesktop Trash specification](https://specifications.freedesktop.org/trash/latest/).

In the reported 5 October 2026 failure, the original path was `~/.local/openfoam-sediment-v2406-initfix`. Its Trash record gave `DeletionDate=2026-10-05T17:22:09`; the final build-log modification time was `2026-10-05 17:22:17 +0200`. The approximately 8 s interval supports relocation during the still-running build: CMake retained absolute paths to the unavailable original directory, and OpenFOAM's subsequent directory check failed. These records do not identify the responsible process; the user reported no manual relocation during installation. No installer Trash operation was found in the audited code.

Once no installation or compilation process remains active, use the file manager's Trash Restore operation for this installation only. Restore it to its recorded original path. If that destination already exists, cancel rather than merge or overwrite. Do not empty Trash or restore unrelated failed installations. Do not build inside Trash: CMake-generated build files contain absolute paths.

If the recorded original path is `~/.local/openfoam-sediment-v2406-initfix`, the tree is intact, and the installer code and toolchain are unchanged, resume from the installer directory:

```bash
python3 install.py --prefix "$HOME/.local/openfoam-sediment-v2406-initfix" --examples --smoke-test
```

System prerequisites were checked before the reported core build, so package installation need not be repeated. Retain other original options where applicable. No `--source-cache`, new prefix, or `--rebuild-core` is needed for this recovery: an unsuccessful core build lacks the completion marker and is retried automatically. Do not edit ownership or completion markers. Restoration addresses path availability; it does not establish the cause of an automatic or otherwise unexplained relocation.

## Preserve the failed installation and retry

Installer changes alter the installation identity. The corrected installer will therefore refuse the previous prefix. Preserve that directory and let the corrected installer create a fresh one; do not edit `.sediment-installer.json` to bypass the check.

Leave failed installation directories in place. Run the standard source installation from the corrected installer directory with a new prefix:

```bash
python3 install.py --install-system-packages \
  --prefix "$HOME/.local/openfoam-sediment-v2406-initfix" \
  --examples --smoke-test
```

No previous installation or cache directory is required. The installer downloads and verifies its own archives. This command deliberately omits the optional `--source-cache` argument.

If a retry stopped with `Archive cache directory not found`, remove `--source-cache` and its path from the command. This validation occurs before claiming the new prefix or installing packages; that error alone does not require another prefix or rollback. The supplied cache path was absent, not an OpenFOAM startup failure.

After successful installation, activate the new prefix:

```bash
source "$HOME/.local/openfoam-sediment-v2406-initfix/activate.sh"
```

The old prefix remains available for diagnosis. Retain any original options such as `--jobs`, `--skip-visualization`, or `--visit-platform` as needed.

### Optional Cache Reuse

Only add `--source-cache /actual/existing/cache` after checking that the directory exists and contains downloaded OpenFOAM archives. This optimization is not needed for installation or for the environment-loading fix. It reads the two core `.tgz` archives only; it does not reuse extracted sources, object files, or binaries. Each archive must match its pinned SHA256. Missing archives download into the new installation's cache; mismatched archives are retained and rejected. The old cache is not modified. This option is not used with `--reuse-openfoam`. Windows users can pass `-SourceCache` to `install.ps1`, using the verified WSL Linux path.

### Optional Recoverable Move

Alternatively, move a failed installation aside to reuse its original path. The following commands apply to the **default** installation directory. If `--prefix` was used, replace `install_prefix` with that exact dedicated directory. Exit any activated installer shell before moving an installation.

```bash
install_prefix="$HOME/.local/openfoam-sediment-v2406"
if [ -d "$install_prefix" ] && [ ! -L "$install_prefix" ] \
   && [ -f "$install_prefix/.sediment-installer.json" ] \
   && [ ! -L "$install_prefix/.sediment-installer.json" ]; then
    recovery_dir="$(mktemp -d "${install_prefix}.failed-XXXXXXXX")" \
      && mv -T -- "$install_prefix" "$recovery_dir/install" \
      && printf 'Failed installation preserved at: %s\n' "$recovery_dir/install"
else
    printf 'No marked installation at this path; nothing moved.\n' >&2
fi
```

This is a recoverable move, not a deletion. It retains downloaded archives, logs, and any case files. If the move succeeded, retry from the corrected installer directory:

```bash
python3 install.py --install-system-packages --examples --smoke-test
```

Retain any original options, including a custom `--prefix` or `--jobs`. For an installation that used `--reuse-openfoam`, retain that option and omit `--source-cache`. The saved failed directory need not be deleted before retrying.

## System packages

`apt-get` remains the recommended APT interface for scripts because it preserves backward compatibility. `apt` is the interactive interface; using it manually is appropriate. See the [Debian APT manual](https://manpages.debian.org/bookworm/apt/apt.8.en.html#SCRIPT_USAGE_AND_DIFFERENCES_FROM_OTHER_APT_TOOLS).

Moving the user installation does **not** undo APT installations or upgrades. Inspect the relevant transaction first:

```bash
less /var/log/apt/history.log
```

Older transactions may be in rotated or compressed history files. The `Install` and `Upgrade` entries distinguish newly added packages from upgraded ones. Retain dependencies needed by other applications; do not remove the installer's entire dependency list or run an unreviewed `autoremove`. Exact reversal of package upgrades requires the previous package versions or a preinstallation system snapshot. Leaving these dependencies installed is sufficient for retrying the corrected installer.
