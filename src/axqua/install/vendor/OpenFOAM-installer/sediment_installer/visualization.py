"""Pinned official VisIt binaries; ParaView is installed through signed apt repos.

The callback interface keeps networking, logging and execution in the installer.
No package is selected from an unversioned ``latest`` endpoint.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import shutil
import tempfile
from typing import Callable, Mapping

VISIT_VERSION = "3.5.0"
VISIT_BASE_URL = "https://github.com/visit-dav/visit/releases/download/v3.5.0"
VISIT_INSTALLER = "visit-install3_5_0"
VISIT_INSTALLER_SHA256 = (
    "65b5ad7facc0f7281026c78c2adeff07a31965136da150e3d9d00341907367da"
)
# Verified against the official v3.5.0 release asset digests, 2026-10-05.
VISIT_SHA256 = {
    "debian12": "c0b3602d93134eaf14aa347d4e57db5f72392cd78f9867840a7df30802b9e891",
    "ubuntu22": "ffd3e61731244f96395bf765a0dee4ee31386ac700771de349f6ad758cb86b92",
    "ubuntu24": "037bac788b0f6f11eb6b138a20db6afcebaf914b768a03323003a2bec89697fd",
}
_MARKER = ".sediment-installer-visit.json"
_WORK_MARKER = ".sediment-installer-visit-workdir"


def platform_tag(os_release: Mapping[str, str]) -> str:
    """Return a supported binary's *base* OS, or fail without guessing.

    ID_LIKE alone is deliberately insufficient: Ubuntu itself says Debian-like.
    Mint exposes its Ubuntu base as UBUNTU_CODENAME. Other derivatives can use
    the installer's explicit platform override after verifying their base OS.
    """
    distro = os_release.get("ID", "").lower()
    version = os_release.get("VERSION_ID", "")
    if distro == "debian" and version == "12":
        return "debian12"
    if distro == "ubuntu":
        if version == "22.04":
            return "ubuntu22"
        if version == "24.04":
            return "ubuntu24"
    if distro == "linuxmint":
        base = os_release.get("UBUNTU_CODENAME", "")
        if base == "jammy":
            return "ubuntu22"
        if base == "noble":
            return "ubuntu24"
    raise ValueError(
        "No verified VisIt binary for this OS. Select debian12, ubuntu22 or "
        "ubuntu24 explicitly only after verifying the derivative's base release."
    )


def visit_manifest(tag: str) -> dict[str, str]:
    """Exact reproducible upstream artifact names and SHA256 values."""
    if tag not in VISIT_SHA256:
        raise ValueError(f"Unsupported VisIt platform: {tag!r}")
    archive = f"visit3_5_0.linux-x86_64-{tag}.tar.gz"
    return {
        "version": VISIT_VERSION,
        "platform": tag,
        "archive": archive,
        "archive_sha256": VISIT_SHA256[tag],
        "installer_sha256": VISIT_INSTALLER_SHA256,
    }


def install_visit(
    prefix: Path,
    platform_tag: str,
    download: Callable,
    run: Callable,
) -> Path:
    """Install once into an owned folder and return its launcher.

    download(url, sha256, cache_path) -> Path
    run(argv, cwd=None, log=None, env=None) -> None

    A present unmarked directory, symlink, changed marker, or partial install is
    never overwritten. The caller should expose an explicit recovery action for
    failed installs, rather than silently deleting user files.
    """
    manifest = visit_manifest(platform_tag)
    prefix = Path(prefix).resolve()
    cache = prefix / "cache"
    apps = prefix / "apps"
    for directory in (cache, apps):
        if directory.is_symlink():
            raise RuntimeError(f"Refusing a symlinked installation directory: {directory}")
        directory.mkdir(parents=True, exist_ok=True)
    destination = apps / f"visit-{VISIT_VERSION}-{platform_tag}"
    launcher = destination / "bin" / "visit"
    marker = destination / _MARKER
    if destination.is_symlink():
        raise RuntimeError(f"Refusing a symlinked VisIt destination: {destination}")
    if destination.exists():
        if not marker.is_file() or marker.is_symlink():
            raise RuntimeError(f"Refusing to replace an unowned VisIt directory: {destination}")
        try:
            previous = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise RuntimeError(f"Unreadable VisIt ownership marker: {marker}") from error
        if previous != manifest:
            raise RuntimeError(f"VisIt ownership/version mismatch: {destination}")
        if not launcher.is_file() or not os.access(launcher, os.X_OK):
            raise RuntimeError(
                f"Incomplete previous VisIt install: {destination}. Inspect its log "
                "and move the owned directory aside before retrying."
            )
        return launcher

    archive_name = manifest["archive"]
    archive = Path(download(
        f"{VISIT_BASE_URL}/{archive_name}",
        manifest["archive_sha256"],
        cache / archive_name,
    ))
    installer = Path(download(
        f"{VISIT_BASE_URL}/{VISIT_INSTALLER}",
        VISIT_INSTALLER_SHA256,
        cache / VISIT_INSTALLER,
    ))
    if archive.resolve().parent != cache or archive.name != archive_name:
        raise RuntimeError("VisIt downloader returned an unexpected archive path")
    if installer.resolve().parent != cache or installer.name != VISIT_INSTALLER:
        raise RuntimeError("VisIt downloader returned an unexpected installer path")

    # The upstream script unconditionally removes cwd/distribution. Never run
    # it in the persistent cache or any directory containing user work.
    workdir = Path(tempfile.mkdtemp(prefix=".visit-work-", dir=apps))
    owned_workdir = workdir.resolve()
    work_token = secrets.token_hex(24)
    work_marker = workdir / _WORK_MARKER
    work_marker.write_text(work_token, encoding="ascii")
    try:
        for cached_file in (archive, installer):
            staged_file = workdir / cached_file.name
            try:
                os.link(cached_file, staged_file)
            except OSError:
                shutil.copy2(cached_file, staged_file)
        destination.mkdir()
        # Establish ownership before the upstream installer starts writing there.
        marker.write_text(json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8")
        run(
            ["bash", str(workdir / installer.name), "-c", "none", VISIT_VERSION,
             f"linux-x86_64-{platform_tag}", str(destination)],
            cwd=workdir,
            log=prefix / "logs" / "visit-install.log",
        )
    finally:
        # Validate the exact fresh directory and marker before recursive cleanup.
        # If ownership changed, leave it untouched instead of following links.
        if (workdir.is_symlink() or workdir.resolve() != owned_workdir
                or owned_workdir.parent != apps.resolve()
                or not owned_workdir.name.startswith(".visit-work-")
                or work_marker.is_symlink() or not work_marker.is_file()
                or work_marker.read_text(encoding="ascii") != work_token):
            raise RuntimeError(f"Refusing cleanup of changed VisIt work directory: {workdir}")
        shutil.rmtree(owned_workdir)
    if not launcher.is_file() or not os.access(launcher, os.X_OK):
        raise RuntimeError(f"VisIt installer did not produce an executable launcher: {launcher}")
    return launcher
