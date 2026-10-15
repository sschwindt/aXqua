"""Linux implementation; the PowerShell launcher enters WSL2 on Windows."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import subprocess
import sys

from .core import (InstallError, capture, checked_download, claim_prefix,
                   clean_environment, run, safe_extract_tar, sha256, write_generated)

REPO = Path(__file__).resolve().parent.parent
BAW_SHA = "e6f9c3130696122e59bb79e7adcde55aa108b395"
BAW_LIBRARY = "lib_BAW_public_BCs_v2412_20260813.so"
BUILD_PACKAGES = [
    "build-essential", "autoconf", "autotools-dev", "cmake", "gawk", "gnuplot",
    "flex", "bison", "libfl-dev", "git", "wget", "ca-certificates",
    "libreadline-dev", "libncurses-dev", "zlib1g-dev", "libopenmpi-dev", "openmpi-bin",
    "libscotch-dev", "libptscotch-dev", "libfftw3-dev", "libboost-system-dev",
    "libboost-thread-dev", "libcgal-dev", "libgmp-dev", "libmpfr-dev", "libmpc-dev", "libmetis-dev",
]
GUI_PACKAGES = [
    "paraview", "libsm6", "libice6", "fontconfig", "libfreetype6", "libxrender1",
    "libxcb-render0", "libxcb-render-util0", "libxcb-shape0", "libxcb-randr0",
    "libxcb-xfixes0", "libxcb-xkb1", "libxcb-sync1", "libxcb-shm0", "libxcb-icccm4",
    "libxcb-keysyms1", "libxcb-image0", "libxcb-util1", "libxcb-cursor0",
    "libxkbcommon0", "libxkbcommon-x11-0", "libx11-6", "libx11-xcb1", "libxext6",
    "libxfixes3", "libxi6", "libegl1", "libgl1", "libxml2",
]
CORE_ARTIFACTS = {
    "OpenFOAM-v2406": ("https://dl.openfoam.com/source/v2406/OpenFOAM-v2406.tgz",
                      "8d1450fb89eec1e7cecc55c3bb7bc486ccbf63d069379d1d5d7518fa16a4686a"),
    "ThirdParty-v2406": ("https://dl.openfoam.com/source/v2406/ThirdParty-v2406.tgz",
                        "7b700c566ecdc8eb665206a510d581e05ec6e47ba52b16b578c68e885bc9576b"),
}


def read_os_release(path=Path("/etc/os-release")):
    result = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            try:
                tokens = shlex.split(value)
            except ValueError as error:
                raise InstallError("Malformed /etc/os-release") from error
            result[key] = tokens[0] if tokens else ""
    return result


def default_jobs():
    jobs = min(os.cpu_count() or 1, 8)
    try:
        memory = Path("/proc/meminfo").read_text().split("MemTotal:", 1)[1].split()[0]
        jobs = min(jobs, max(1, int(memory) // (2 * 1024 * 1024)))
    except (OSError, IndexError, ValueError):
        pass
    return jobs


def parser():
    p = argparse.ArgumentParser(description="Compile OpenCFD v2406 + Olsen + BAW; install ParaView/VisIt")
    p.add_argument("--prefix", type=Path, default=Path.home() / ".local/openfoam-sediment-v2406")
    p.add_argument("--jobs", type=int, default=default_jobs())
    p.add_argument("--install-system-packages", action="store_true",
                   help="Authorize sudo apt-get for build prerequisites and distro ParaView")
    p.add_argument("--reuse-openfoam", type=Path, metavar="BASHRC",
                   help="Use an existing OpenCFD v2406 bashrc instead of compiling core")
    p.add_argument("--source-cache", type=Path, metavar="DIRECTORY",
                   help="Read verified core tarballs from an earlier cache without modifying it")
    p.add_argument("--rebuild-core", action="store_true", help="Repeat incremental Allwmake")
    p.add_argument("--visit-platform", choices=["debian12", "ubuntu22", "ubuntu24"],
                   help="Explicit base-OS choice for an otherwise unrecognized derivative")
    p.add_argument("--skip-visualization", action="store_true", help="Build-only/CI mode")
    p.add_argument("--examples", action="store_true", help="Also download Olsen's coarse Case A")
    p.add_argument("--smoke-test", action="store_true", help="Short serial BAW interFoam load/run check")
    p.add_argument("--dry-run", action="store_true", help="Print plan; no network, writes or commands")
    return p


def plan(args):
    print("No actions executed. Plan:")
    print(f"  User-owned prefix: {args.prefix}")
    print(f"  OpenCFD v2406: {'reuse ' + str(args.reuse_openfoam) if args.reuse_openfoam else 'compile official pinned source + ThirdParty'}")
    if args.source_cache:
        print(f"  Read-only archive cache: {args.source_cache}; cached files are SHA256-checked")
    print(f"  Compile jobs: {args.jobs}; sudo apt packages: {args.install_system_packages}")
    print("  Build sediDriftFoam, sediDriftFoam2, sediDriftFoam2Rating (opt-in research adaptation)")
    print(f"  Build BAW HydBCsForOF at {BAW_SHA}; native BC available to interFoam")
    print("  Visualization: " + ("skipped explicitly" if args.skip_visualization else "signed distro ParaView + OS-matched VisIt 3.5.0"))
    print(f"  Olsen Case A: {args.examples}; short BAW runtime smoke: {args.smoke_test}")
    if os.name == "nt":
        print("  Windows: use install.ps1; compilation and both GUIs run in WSL2/WSLg")


def identity(args):
    digest = hashlib.sha256()
    # A different installer/patch/source pin must not silently reuse old build objects.
    paths = [*sorted((REPO / "sediment_installer").glob("*.py")),
             *sorted((REPO / "sediment_installer").glob("*.json")),
             *sorted((REPO / "cpp").glob("*.H")), REPO / "postprocess.py"]
    for path in paths:
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    digest.update(str(args.reuse_openfoam.resolve() if args.reuse_openfoam else "source-v2406").encode())
    return digest.hexdigest()


def _source_openfoam_without_arguments(quoted_bashrc: str) -> str:
    """Activate at top level, with no argv and without unsafe strict-shell modes.

    OpenCFD consumes source arguments as settings and uses nonzero returns for
    optional library probes. Do not source it inside a function or with errexit
    or nounset enabled. Restore caller argv/options and reject source failures;
    the caller must also verify the selected project, API and ABI explicitly.
    """
    return f'''_sediment_saved_argv=("$@")
_sediment_saved_shell_flags=$-
set +e +u
set --
source {quoted_bashrc}
_sediment_source_rc=$?
set -- "${{_sediment_saved_argv[@]}}"
unset _sediment_saved_argv
case "$_sediment_saved_shell_flags" in *u*) set -u ;; *) set +u ;; esac
case "$_sediment_saved_shell_flags" in *e*) set -e ;; *) set +e ;; esac
unset _sediment_saved_shell_flags
if [[ "$_sediment_source_rc" != 0 ]]; then
    printf 'OpenFOAM environment sourcing failed (status %s)\\n' "$_sediment_source_rc" >&2
    exit "$_sediment_source_rc"
fi
unset _sediment_source_rc
'''


def foam_command(bashrc: Path, prefix: Path, argv, jobs: int, cwd=None, log=None, capture_output=False):
    # All user paths are positional parameters, never executable shell text.
    script = r'''set -e
bashrc=$1; prefix=$2; jobs=$3; shift 3
export WM_COMPILER_TYPE=system WM_COMPILER=Gcc WM_MPLIB=SYSTEMOPENMPI
export WM_PRECISION_OPTION=DP WM_LABEL_SIZE=32 WM_COMPILE_OPTION=Opt
export WM_NCOMPPROCS="$jobs" FOAM_MODULE_PREFIX=false FOAM_CONFIG_MODE=o
''' + _source_openfoam_without_arguments('"$bashrc"') + r'''
export WM_PROJECT_USER_DIR="$prefix/user"
export FOAM_USER_APPBIN="$prefix/user/platforms/$WM_OPTIONS/bin"
export FOAM_USER_LIBBIN="$prefix/user/platforms/$WM_OPTIONS/lib"
export PATH="$FOAM_USER_APPBIN:$PATH"
export LD_LIBRARY_PATH="$FOAM_USER_LIBBIN:${LD_LIBRARY_PATH:-}"
[[ "${WM_PROJECT_DIR:-}/etc/bashrc" -ef "$bashrc" ]] || { echo 'Wrong OpenFOAM project directory' >&2; exit 1; }
[[ "$(foamEtcFile -show-api)" == 2406 ]] || { echo 'Wrong OpenFOAM API' >&2; exit 1; }
[[ "${WM_OPTIONS:-}" == linux64GccDPInt32Opt ]] || { echo 'Unexpected build ABI' >&2; exit 1; }
exec "$@"
'''
    command = ["/bin/bash", "--noprofile", "--norc", "-c", script, "foam-build",
               str(bashrc), str(prefix), str(jobs), *map(str, argv)]
    if capture_output:
        if log is not None:
            return capture(command, cwd=cwd, env=clean_environment(), log=log)
        return capture(command, cwd=cwd, env=clean_environment())
    run(command, cwd=cwd, log=log, env=clean_environment())


def acquire_core(prefix, source_cache=None):
    if source_cache is not None:
        source_cache = Path(source_cache).expanduser().absolute()
        if not source_cache.is_dir():
            raise InstallError(f"Archive cache directory not found: {source_cache}")
    for root, (url, digest) in CORE_ARTIFACTS.items():
        cached = None if source_cache is None else source_cache / (root + ".tgz")
        if cached is not None and (cached.exists() or cached.is_symlink()):
            if cached.is_symlink() or not cached.is_file():
                raise InstallError(f"Refusing non-regular cached archive: {cached}")
            if sha256(cached) != digest:
                raise InstallError(f"Archive cache checksum mismatch: {cached}; file retained")
            print(f"Using verified read-only archive: {cached}", flush=True)
            archive = cached
        else:
            # Missing files are downloaded only into this installation's owned cache.
            archive = checked_download(url, digest, prefix / "cache" / (root + ".tgz"))
        target = prefix / "src" / root
        marker = target / ".installer-archive-sha256"
        if target.exists():
            if not marker.is_file() or marker.read_text().strip() != digest:
                raise InstallError(f"Foreign/incomplete source tree: {target}")
        else:
            safe_extract_tar(archive, target, root)
            marker.write_text(digest + "\n", encoding="ascii")
    return prefix / "src/OpenFOAM-v2406/etc/bashrc"


def verify_environment(bashrc, prefix, jobs):
    report = foam_command(bashrc, prefix, ["/bin/bash", "-c", r'''
printf 'root=%s\nabi=%s\n' "$WM_PROJECT_DIR" "$WM_OPTIONS"
foamEtcFile -show-api
foamEtcFile -show-patch
gcc --version | head -1
mpirun --version | head -1
'''], jobs, capture_output=True, log=prefix / "logs/environment-probe.log")
    target = prefix / "build-environment.txt"
    if target.exists() and target.read_text() != report + "\n":
        raise InstallError("Toolchain/OpenFOAM changed: choose a new prefix to avoid mixing object ABIs")
    target.write_text(report + "\n", encoding="utf-8")
    print(report)


def build_core(bashrc, prefix, args):
    source = bashrc.parent.parent
    marker = prefix / ".core-built"
    if not marker.exists() or args.rebuild_core:
        foam_command(bashrc, prefix, ["./Allwmake", "-j", str(args.jobs)], args.jobs,
                     cwd=source, log=prefix / "logs/openfoam-Allwmake.log")
    # Allwmake can continue after errors. Verify needed executables, not just its exit code.
    for command in ["simpleFoam", "interFoam", "blockMesh", "checkMesh", "foamToVTK", "setFields"]:
        foam_command(bashrc, prefix, [command, "-help"], args.jobs,
                     log=prefix / "logs/core-verification.log")
    marker.write_text("Required tools verified, not a full scientific validation.\n", encoding="utf-8")


def fetch_baw(prefix):
    destination = prefix / "src/HydBCsForOF"
    env = clean_environment()
    marker = destination / ".installer-git-target"
    if not destination.exists():
        destination.mkdir(parents=True)
        marker.write_text(BAW_SHA + "\n")
        run(["git", "init", str(destination)], env=env)
        run(["git", "-C", str(destination), "remote", "add", "origin", "https://github.com/baw-de/HydBCsForOF.git"], env=env)
    elif destination.is_symlink() or not marker.is_file() or marker.read_text().strip() != BAW_SHA:
        raise InstallError("Foreign BAW source tree; preserving it")
    # Resume an interrupted clone/fetch without resetting an existing HEAD.
    head_check = subprocess.run(["git", "-C", str(destination), "rev-parse", "--verify", "HEAD"],
                                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if head_check.returncode:
        if capture(["git", "-C", str(destination), "remote", "get-url", "origin"], env=env) != "https://github.com/baw-de/HydBCsForOF.git":
            raise InstallError("BAW clone origin changed; preserving checkout")
        run(["git", "-C", str(destination), "fetch", "--depth=1", "origin", BAW_SHA], env=env)
        run(["git", "-C", str(destination), "checkout", "--detach", BAW_SHA], env=env)
    if capture(["git", "-C", str(destination), "rev-parse", "HEAD"], env=env) != BAW_SHA:
        raise InstallError("Existing BAW checkout has a different revision; no reset will be performed")
    run(["git", "-C", str(destination), "diff", "--exit-code", "HEAD"], env=env)
    untracked = capture(["git", "-C", str(destination), "ls-files", "--others", "--exclude-standard"], env=env)
    for name in untracked.splitlines():
        path = Path(name)
        if path.suffix in (".C", ".H", ".h") and "lnInclude" not in path.parts and "Make" not in path.parts:
            raise InstallError(f"Untracked BAW source file would affect reproducibility: {name}")
    return destination


#: Olsen's source files as published, shipped with the installer (GPL-3.0-or-later,
#: see olsen-sources/README.md). His web site has no versioned releases and answered
#: HTTP 502 for weeks in October 2026, so the download is the fallback only.
OLSEN_SOURCES = REPO / "olsen-sources"


def place_bundled(bundled: Path, digest: str, target: Path) -> Path:
    """Copy one bundled source file to *target* after checking its SHA256."""
    if target.is_symlink():
        raise InstallError(f"Refusing symlink source file: {target}")
    if target.exists():
        if sha256(target) != digest.lower():
            raise InstallError(f"Source checksum mismatch: {target}; preserve/investigate it before retrying")
        return target
    if sha256(bundled) != digest.lower():
        raise InstallError(f"Bundled Olsen source does not match its pinned SHA256: {bundled}")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + f".part-{os.getpid()}")
    shutil.copyfile(bundled, temporary)
    os.replace(temporary, target)
    return target


def acquire_olsen(prefix, bundle=None):
    manifest = json.loads((REPO / "sediment_installer/olsen_sources.json").read_text())
    bundle = OLSEN_SOURCES if bundle is None else Path(bundle)
    result = {}
    for name in ("sediDriftFoam", "sediDriftFoam2"):
        source = prefix / "src" / name
        source.mkdir(parents=True, exist_ok=True)
        base = manifest["upstream_root"].rstrip("/") + "/" + name + "/sourceCode/"
        for item, digest in manifest["files"].items():
            if not item.startswith(name + "/"):
                continue
            relative = item.removeprefix(name + "/")
            target = source / relative
            if (bundle / item).is_file():
                place_bundled(bundle / item, digest, target)
                continue
            try:
                checked_download(base + relative, digest, target)
            except (InstallError, OSError) as error:     # urllib's HTTPError is an OSError
                raise InstallError(f"Olsen source {item} is neither bundled in {bundle} nor "
                                   f"downloadable from {base}: {error}") from error
        result[name] = source
    return result


def build_olsen(sources, bashrc, prefix, args):
    from .olsen_patch import patch_source, patch_baseline_source

    def build_copy(source, destination, edits):
        # Compare every input on resume; never silently compile edited/incomplete trees.
        expected = {str(file.relative_to(source)): file.read_bytes()
                    for file in source.rglob("*") if file.is_file()}
        expected.update(edits)
        stamp = {name: hashlib.sha256(data).hexdigest() for name, data in expected.items()}
        marker = destination / ".installer-inputs.json"
        if destination.exists():
            if destination.is_symlink() or not marker.is_file() or json.loads(marker.read_text()) != stamp:
                raise InstallError(f"Foreign/incomplete solver build tree: {destination}; preserving it")
            for name, digest in stamp.items():
                target = destination / name
                if target.is_symlink() or not target.is_file() or sha256(target) != digest:
                    raise InstallError(f"Solver input was edited: {target}; preserving it")
        else:
            shutil.copytree(source, destination)
            for name, data in edits.items():
                (destination / name).write_bytes(data)
            marker.write_text(json.dumps(stamp, sort_keys=True) + "\n")

    for name, source in sources.items():
        destination = prefix / "build" / name
        main_source = name + ".C"
        build_copy(source, destination, {
            main_source: patch_baseline_source((source / main_source).read_bytes(), name)})
        # No broad API rewrite: source pins/build logs make any failure auditable.
        foam_command(bashrc, prefix, ["wmake", "-j", str(args.jobs)], args.jobs,
                     cwd=destination, log=prefix / "logs" / (name + ".log"))
        foam_command(bashrc, prefix, [name, "-help"], args.jobs,
                     log=prefix / "logs/olsen-verification.log")
    source = sources["sediDriftFoam2"]
    destination = prefix / "build/sediDriftFoam2Rating"
    content = (source / "Make/files").read_text()
    old = "$(FOAM_USER_APPBIN)/sediDriftFoam2"
    if content.count(old) != 1:
        raise InstallError("Unexpected Olsen Make/files executable anchor")
    build_copy(source, destination, {
        "sediDriftFoam2.C": patch_source((source / "sediDriftFoam2.C").read_bytes()),
        "Make/files": content.replace(old, "$(FOAM_USER_APPBIN)/sediDriftFoam2Rating").encode(),
        "olsenRatingCurve.H": (REPO / "cpp/olsenRatingCurve.H").read_bytes()})
    foam_command(bashrc, prefix, ["wmake", "-j", str(args.jobs)], args.jobs,
                 cwd=destination, log=prefix / "logs/sediDriftFoam2Rating.log")
    foam_command(bashrc, prefix, ["sediDriftFoam2Rating", "-help"], args.jobs,
                 log=prefix / "logs/olsen-verification.log")


def write_launchers(prefix, bashrc, visit, jobs):
    q = shlex.quote
    common = f'''# Generated by openfoam-sediment-installer
export WM_COMPILER_TYPE=system WM_COMPILER=Gcc WM_MPLIB=SYSTEMOPENMPI
export WM_PRECISION_OPTION=DP WM_LABEL_SIZE=32 WM_COMPILE_OPTION=Opt
export FOAM_CONFIG_MODE=o FOAM_MODULE_PREFIX=false WM_NCOMPPROCS={jobs}
''' + _source_openfoam_without_arguments(q(str(bashrc))) + f'''
[[ "${{WM_PROJECT_DIR:-}}/etc/bashrc" -ef {q(str(bashrc))} ]] || {{ echo 'Wrong OpenFOAM project directory' >&2; exit 1; }}
[[ "$(foamEtcFile -show-api)" == 2406 && "${{WM_OPTIONS:-}}" == linux64GccDPInt32Opt ]] || {{ echo 'Wrong OpenFOAM/ABI' >&2; exit 1; }}
export WM_PROJECT_USER_DIR={q(str(prefix / 'user'))}
export FOAM_USER_APPBIN="$WM_PROJECT_USER_DIR/platforms/$WM_OPTIONS/bin"
export FOAM_USER_LIBBIN="$WM_PROJECT_USER_DIR/platforms/$WM_OPTIONS/lib"
export PATH={q(str(prefix / 'bin'))}:"$FOAM_USER_APPBIN:$PATH"
export LD_LIBRARY_PATH="$FOAM_USER_LIBBIN:${{LD_LIBRARY_PATH:-}}"
'''
    activation = """# Generated by openfoam-sediment-installer
# Run as: source /path/to/activate.sh ; cleans old environment in a new shell.
"""
    # Sourcing this file deliberately opens a clean interactive shell, not overlays Foundation9.
    startup = prefix / "shell-rc.sh"
    write_generated(startup, common + "printf 'OpenCFD %s; sediment stack ready\\n' \"$WM_PROJECT_VERSION\"\n")
    activation += f"/usr/bin/env -i HOME=\"$HOME\" USER=\"${{USER:-$(id -un)}}\" LOGNAME=\"${{LOGNAME:-$(id -un)}}\" TERM=\"${{TERM:-xterm}}\" PATH=/usr/bin:/bin DISPLAY=\"${{DISPLAY:-}}\" WAYLAND_DISPLAY=\"${{WAYLAND_DISPLAY:-}}\" XDG_RUNTIME_DIR=\"${{XDG_RUNTIME_DIR:-}}\" XAUTHORITY=\"${{XAUTHORITY:-}}\" DBUS_SESSION_BUS_ADDRESS=\"${{DBUS_SESSION_BUS_ADDRESS:-}}\" /bin/bash --noprofile --rcfile {q(str(startup))} -i\n"
    write_generated(prefix / "activate.sh", activation)
    for name in ("sediDriftFoam", "sediDriftFoam2", "sediDriftFoam2Rating"):
        # The published mesh/point algorithm and scour.txt writer are not MPI-safe.
        script = "#!/bin/bash\n# Generated by openfoam-sediment-installer\n"
        script += "for arg in \"$@\"; do [[ \"$arg\" != -parallel ]] || { echo 'Olsen solvers: serial only' >&2; exit 2; }; done\n"
        script += "[[ ${OMPI_COMM_WORLD_SIZE:-1} == 1 && ${PMI_SIZE:-1} == 1 ]] || { echo 'Olsen solvers: serial only' >&2; exit 2; }\n"
        # Quoted beforehand: an f-string with nested double quotes needs Python 3.12.
        command = q(common + 'exec "$FOAM_USER_APPBIN/' + name + '" "$@"')
        script += f"exec /usr/bin/env -i HOME=\"$HOME\" USER=\"${{USER:-$(id -un)}}\" LOGNAME=\"${{LOGNAME:-$(id -un)}}\" PATH=/usr/bin:/bin /bin/bash --noprofile --norc -c {command} solver \"$@\"\n"
        write_generated(prefix / "bin" / name, script, executable=True)
    for name, executable in (("paraview", "/usr/bin/paraview" if visit else None), ("visit", str(visit) if visit else None)):
        if executable:
            script = "#!/bin/bash\n# Generated by openfoam-sediment-installer\n"
            script += f"exec /usr/bin/env -u LD_LIBRARY_PATH -u LD_PRELOAD -u PYTHONPATH -u PYTHONHOME -u QT_PLUGIN_PATH -u QT_QPA_PLATFORM_PLUGIN_PATH PATH=/usr/local/bin:/usr/bin:/bin {q(executable)} \"$@\"\n"
            write_generated(prefix / "bin" / name, script, executable=True)
    helper = prefix / "postprocess.py"
    content = (REPO / "postprocess.py").read_text(encoding="utf-8")
    write_generated(helper, content)


def prepare_cases(prefix, baw, args):
    target = prefix / "cases/baw-interFoam"
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(baw / "testcase/inter", target)
    if args.examples:
        # Case inputs are an unversioned public tree, not an immutable Git release.
        # Retain a fresh acquisition and its checksums rather than pretending it is pinned.
        base = prefix / "cases/olsen-upstream"
        inventory_file = base / "acquisition-sha256.json"
        if base.exists() and not inventory_file.is_file():
            raise InstallError(f"Partial/foreign case download: {base}; move it aside before retrying")
        if not base.exists():
            base.mkdir(parents=True)
            run(["wget", "--recursive", "--no-parent", "--no-host-directories", "--cut-dirs=2",
                 "--reject=index.html*", "--directory-prefix=" + str(base),
                 "https://www.pvv.ntnu.no/~nilsol/sediDriftFoam2/cylinder9_case_A/"],
                env=clean_environment(), log=prefix / "logs/case-download.log")
            inventory = {str(path.relative_to(base)): sha256(path) for path in sorted(base.rglob("*")) if path.is_file()}
            inventory_file.write_text(json.dumps(inventory, indent=2) + "\n")


def write_receipt(prefix, args, tag, library):
    paraview = None if args.skip_visualization else capture(
        ["dpkg-query", "-W", "-f=${Package} ${Version}\n", "paraview"], env=clean_environment())
    receipt = {
        "generated_by": "openfoam-sediment-installer", "installer_identity": identity(args),
        "openfoam": "reuse " + str(args.reuse_openfoam) if args.reuse_openfoam else "official base v2406",
        "read_only_source_cache": str(args.source_cache) if args.source_cache else None,
        "core_artifacts": CORE_ARTIFACTS,
        "olsen_sources": json.loads((REPO / "sediment_installer/olsen_sources.json").read_text()),
        "baw_commit": BAW_SHA, "baw_library_sha256": sha256(library),
        "paraview_package": paraview, "visit": None if args.skip_visualization else {"version": "3.5.0", "platform": tag},
        "rating_header_sha256": sha256(REPO / "cpp/olsenRatingCurve.H"),
        "rating_validation": "compiled only; see docs/RATING_CURVE.md",
    }
    target = prefix / "receipt.json"
    if target.exists() and json.loads(target.read_text()).get("generated_by") != receipt["generated_by"]:
        raise InstallError("Foreign receipt.json will not be replaced")
    target.write_text(json.dumps(receipt, indent=2) + "\n")


def smoke_baw(prefix, bashrc, args):
    source = prefix / "cases/baw-interFoam"
    target = prefix / "runs/baw-smoke"
    if target.exists():
        raise InstallError("Smoke-run directory already exists; preserve it and choose a new prefix")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)
    for item in (target / "0/bak").iterdir():
        if item.is_file():
            shutil.copy2(item, target / "0" / item.name)
    for key, value in (("startFrom", "startTime"), ("endTime", "0.05"),
                       ("writeInterval", "0.05"), ("maxCo", "0.5"), ("maxAlphaCo", "0.5")):
        foam_command(bashrc, prefix, ["foamDictionary", "system/controlDict", "-entry", key, "-set", value],
                     args.jobs, cwd=target)
    for command in (["checkMesh", "-constant"], ["setFields"], ["timeout", "10m", "interFoam"]):
        foam_command(bashrc, prefix, command, args.jobs, cwd=target,
                     log=target / "log.smoke")
    text = (target / "log.smoke").read_text(errors="replace")
    if "waterLevel BC on" not in text or "FOAM FATAL" in text:
        raise InstallError("BAW smoke did not demonstrate the loaded water-level BC")


def install(args):
    if sys.platform != "linux":
        raise InstallError("On Windows run install.ps1 (WSL2); native MSVC/MinGW builds are not supported")
    if os.geteuid() == 0:
        raise InstallError("Run as a normal user; --install-system-packages uses sudo only for apt")
    if platform.machine().lower() not in ("x86_64", "amd64"):
        raise InstallError("This pinned visualization/toolchain recipe supports x86_64 only")
    if args.jobs < 1:
        raise InstallError("--jobs must be positive")
    if args.source_cache is not None:
        args.source_cache = args.source_cache.expanduser().absolute()
        if args.reuse_openfoam:
            raise InstallError("--source-cache applies to source builds, not --reuse-openfoam")
        if not args.source_cache.is_dir():
            raise InstallError(f"Archive cache directory not found: {args.source_cache}")
    from .visualization import platform_tag, install_visit
    os_release = read_os_release()
    if os_release.get("ID") not in ("debian", "ubuntu", "linuxmint") and not {"debian", "ubuntu"}.intersection(os_release.get("ID_LIKE", "").split()):
        raise InstallError("Requires Debian or a Debian/Ubuntu derivative with apt-get")
    tag = None if args.skip_visualization else args.visit_platform or platform_tag(os_release)
    prefix = args.prefix.expanduser().absolute()
    if "microsoft" in platform.release().lower() and str(prefix).startswith("/mnt/"):
        raise InstallError("Build inside the WSL Linux filesystem, not /mnt/c (use default prefix)")
    # Avoid system/package writes before validating that this is an owned target.
    claim_prefix(prefix, identity(args))
    if not args.reuse_openfoam and shutil.disk_usage(prefix).free < 15 * 1024 ** 3:
        raise InstallError("Core source compilation needs at least 15 GiB free; 20+ GiB recommended")
    if args.install_system_packages:
        packages = BUILD_PACKAGES + ([] if args.skip_visualization else GUI_PACKAGES)
        run(["sudo", "apt-get", "update"], env=clean_environment())
        run(["sudo", "apt-get", "install", "-y", "--no-install-recommends", *packages], env=clean_environment())
    for tool in ("git", "gcc", "g++", "make", "flex", "bison", "cmake", "mpirun"):
        if shutil.which(tool, path=clean_environment()["PATH"]) is None:
            raise InstallError(f"Missing {tool}; rerun with --install-system-packages")
    if not args.skip_visualization and not Path("/usr/bin/paraview").is_file():
        raise InstallError("Missing distro ParaView; rerun with --install-system-packages")
    bashrc = args.reuse_openfoam.expanduser().resolve() if args.reuse_openfoam else acquire_core(prefix, args.source_cache)
    if not bashrc.is_file():
        raise InstallError(f"OpenFOAM activation file not found: {bashrc}")
    verify_environment(bashrc, prefix, args.jobs)
    if not args.reuse_openfoam:
        build_core(bashrc, prefix, args)
    else:
        for tool in ("interFoam", "checkMesh", "foamToVTK", "setFields"):
            foam_command(bashrc, prefix, [tool, "-help"], args.jobs)
    sources = acquire_olsen(prefix)
    build_olsen(sources, bashrc, prefix, args)
    baw = fetch_baw(prefix)
    foam_command(bashrc, prefix, ["/bin/bash", "-c", ". ./AllWmake"], args.jobs,
                 cwd=baw / "boundaryConditions", log=prefix / "logs/baw-wmake.log")
    library = prefix / "user/platforms/linux64GccDPInt32Opt/lib" / BAW_LIBRARY
    if not library.is_file():
        raise InstallError("BAW library missing after build")
    ldd = foam_command(bashrc, prefix, ["ldd", str(library)], args.jobs, capture_output=True)
    (prefix / "logs/baw-ldd.txt").write_text(ldd + "\n")
    if "not found" in ldd:
        raise InstallError("BAW library has missing shared-library dependencies")
    def visualization_run(argv, cwd=None, log=None, env=None):
        run(argv, cwd=cwd, log=log, env=clean_environment())
    visit = None if args.skip_visualization else install_visit(prefix, tag, checked_download, visualization_run)
    write_launchers(prefix, bashrc, visit, args.jobs)
    prepare_cases(prefix, baw, args)
    if args.smoke_test:
        smoke_baw(prefix, bashrc, args)
    write_receipt(prefix, args, tag, library)
    print(f"\nBuild complete. Enter a clean shell: source {prefix}/activate.sh")
    print(f"Sources, ABI provenance and logs: {prefix}")
    print("Rating extension compiled, NOT scientifically validated. See docs/RATING_CURVE.md.")
    print(f"BAW interFoam case: {prefix}/cases/baw-interFoam")
    print("No login files, existing OpenFOAM installations or user cases were changed.")


def main(argv=None):
    args = parser().parse_args(argv)
    if args.dry_run:
        plan(args)
        return 0
    try:
        install(args)
        return 0
    except (RuntimeError, OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted. Owned downloads/sources/logs retained; rerun to resume.", file=sys.stderr)
        return 130
