"""Small, testable filesystem/download/process primitives. MIT licensed."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import shutil
import subprocess
import tarfile
import tempfile
import time
import urllib.parse
import urllib.request


class InstallError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def clean_environment() -> dict[str, str]:
    """Do not inherit Foundation/OpenCFD, conda or a visualization library path."""
    keep = {"HOME", "USER", "LOGNAME", "TERM", "LANG", "LC_ALL", "DISPLAY",
            "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS",
            "XAUTHORITY", "http_proxy", "https_proxy", "no_proxy",
            "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "SSL_CERT_FILE"}
    env = {key: value for key, value in os.environ.items() if key in keep}
    env["PATH"] = "/usr/bin:/bin:/usr/local/bin"
    env["LANG"] = env.get("LANG", "C.UTF-8")
    return env


def run(argv, cwd=None, log=None, env=None):
    """No shell=True. Stream long builds to console and an optional log."""
    command = [str(arg) for arg in argv]
    print("+ " + " ".join(command), flush=True)
    if log is not None:
        log = Path(log)
        log.parent.mkdir(parents=True, exist_ok=True)
    stream = log.open("a", encoding="utf-8") if log else None
    try:
        proc = subprocess.Popen(command, cwd=cwd, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8", errors="replace")
        assert proc.stdout is not None
        for line in proc.stdout:
            print(line, end="", flush=True)
            if stream:
                stream.write(line)
                stream.flush()
        if proc.wait():
            raise InstallError(f"Command failed ({proc.returncode}); see {log or 'output above'}")
    finally:
        if stream:
            stream.close()


def capture(argv, env=None, cwd=None, log=None) -> str:
    result = subprocess.run([str(arg) for arg in argv], cwd=cwd, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace")
    if log is not None:
        log = Path(log)
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as stream:
            stream.write("=== captured stdout ===\n" + result.stdout)
            stream.write("\n=== captured stderr ===\n" + result.stderr + "\n")
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or f"Command failed: {argv}"
        raise InstallError(detail + (f"; see {log}" if log is not None else ""))
    return result.stdout.strip()


def checked_download(url: str, expected_sha256: str, destination: Path) -> Path:
    destination = Path(destination)
    if not url.startswith("https://") or len(expected_sha256) != 64:
        raise InstallError("Downloads require HTTPS and a pinned SHA256")
    try:
        int(expected_sha256, 16)
    except ValueError as error:
        raise InstallError("Invalid SHA256") from error
    if destination.is_symlink():
        raise InstallError(f"Refusing symlink cache file: {destination}")
    if destination.exists():
        if sha256(destination) != expected_sha256.lower():
            raise InstallError(f"Cache checksum mismatch: {destination}; preserve/investigate it before retrying")
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Unique temp file; never truncate an existing user's .part file.
    fd, temp_name = tempfile.mkstemp(prefix=destination.name + ".part-", dir=destination.parent)
    temporary = Path(temp_name)
    digest = hashlib.sha256()
    try:
        request = urllib.request.Request(url, headers={
            "User-Agent": "openfoam-sediment-installer/1", "Accept-Encoding": "identity"})
        with os.fdopen(fd, "wb") as out, urllib.request.urlopen(request, timeout=120) as response:
            if not response.geturl().startswith("https://"):
                raise InstallError("Refusing redirect to non-HTTPS download")
            # SourceForge redirects may contain expiring tokens. Report the mirror
            # path without query/fragment data, not its signed download URL.
            final_url = urllib.parse.urlsplit(response.geturl())
            location = urllib.parse.urlunsplit((final_url.scheme, final_url.netloc,
                                               final_url.path, "", ""))
            if response.status != 200:
                raise InstallError(f"Unexpected download HTTP status {response.status}: {location}; "
                                   "a complete HTTP 200 response is required")
            content_type = response.headers.get("Content-Type", "unknown").split(";", 1)[0].strip().lower()
            if content_type in {"text/html", "application/xhtml+xml"}:
                raise InstallError(f"Unexpected HTML download response: {location}; "
                                   "expected the pinned file, not a download/error page")
            content_length = response.headers.get("Content-Length")
            try:
                expected_bytes = int(content_length) if content_length is not None else None
                if expected_bytes is not None and expected_bytes < 0:
                    raise ValueError
            except ValueError as error:
                raise InstallError(f"Invalid download Content-Length: {location}") from error
            last_report = time.monotonic()
            total = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                digest.update(chunk)
                total += len(chunk)
                if time.monotonic() - last_report > 15:
                    print(f"Downloading {destination.name}: {total // (1024 * 1024)} MiB", flush=True)
                    last_report = time.monotonic()
        details = (f"\n  Expected SHA256: {expected_sha256.lower()}"
                   f"\n  Received SHA256: {digest.hexdigest()}"
                   f"\n  Received bytes: {total}"
                   f"\n  Content-Type: {content_type}"
                   f"\n  Download location: {location}")
        if expected_bytes is not None and total != expected_bytes:
            raise InstallError(f"Incomplete download: received {total} of {expected_bytes} bytes; "
                               "rejecting unverified download" + details)
        if digest.hexdigest() != expected_sha256.lower():
            raise InstallError("Upstream checksum mismatch: received bytes do not match the pinned file; "
                               "rejecting unverified download" + details)
        if destination.exists():
            raise InstallError(f"Cache file appeared concurrently: {destination}")
        temporary.rename(destination)
    finally:
        # This is only our exact newly-created temporary file.
        if temporary.exists():
            temporary.unlink()
    return destination


def _archive_path(name: str) -> PurePosixPath:
    # Official OpenFOAM tutorials contain POSIX names such as jouleHeatingSource:V.
    # A colon is ordinary on Linux/WSL, but denotes a drive/alternate stream on NTFS.
    if not name or "\\" in name or "\0" in name or (os.name == "nt" and ":" in name):
        raise InstallError(f"Unsafe archive name: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise InstallError(f"Archive traversal: {name}")
    return path


def safe_extract_tar(archive: Path, destination: Path, root_name: str) -> Path:
    """Extract one known archive root. No devices, traversal or escaping links."""
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise InstallError(f"Extraction destination exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".extract-", dir=destination.parent))
    try:
        with tarfile.open(archive, "r:*") as tar:
            members = tar.getmembers()
            for member in members:
                path = _archive_path(member.name)
                if not path.parts or path.parts[0] != root_name:
                    raise InstallError(f"Unexpected archive root: {member.name}")
                if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
                    raise InstallError(f"Unsafe archive entry type: {member.name}")
                if member.issym() or member.islnk():
                    target = member.linkname
                    if (not target or "\\" in target or "\0" in target or target.startswith("/")
                            or (os.name == "nt" and ":" in target)):
                        raise InstallError(f"Unsafe archive link: {member.name}")
                    normalized = posixpath.normpath(str(path.parent / target) if member.issym() else target)
                    link = _archive_path(normalized)
                    if not link.parts or link.parts[0] != root_name:
                        raise InstallError(f"Archive link escapes root: {member.name}")
            # Per-entry resolved checks also protect against previously-created symlinks.
            hardlinks = []
            for member in members:
                output = staging.joinpath(*_archive_path(member.name).parts)
                if not output.resolve().is_relative_to(staging.resolve()):
                    raise InstallError(f"Resolved archive path escapes: {member.name}")
                output.parent.mkdir(parents=True, exist_ok=True)
                if member.isdir():
                    output.mkdir(exist_ok=True)
                elif member.isfile():
                    if output.exists() or output.is_symlink():
                        raise InstallError(f"Duplicate/conflicting archive path: {member.name}")
                    source = tar.extractfile(member)
                    if source is None:
                        raise InstallError(f"Missing archive content: {member.name}")
                    with source, output.open("xb") as stream:
                        shutil.copyfileobj(source, stream)
                    output.chmod(member.mode & 0o755)
                elif member.issym():
                    output.symlink_to(member.linkname)
                elif member.islnk():
                    hardlinks.append((output, member.linkname))
            for output, name in hardlinks:
                target = staging.joinpath(*_archive_path(name).parts)
                if not target.resolve().is_relative_to(staging.resolve()) or not target.is_file():
                    raise InstallError(f"Invalid hardlink target: {name}")
                os.link(target, output)
        extracted = staging / root_name
        if not extracted.is_dir() or extracted.is_symlink():
            raise InstallError("Archive root is not a directory")
        extracted.rename(destination)
        return destination
    finally:
        # staging is an exact mkdtemp path created by us; never remove destination.
        shutil.rmtree(staging)


def claim_prefix(prefix: Path, identity: str):
    prefix = Path(prefix)
    if prefix.is_symlink():
        raise InstallError("Install prefix may not be a symlink")
    resolved = prefix.resolve()
    if resolved == Path.home().resolve() or resolved == Path(resolved.anchor):
        raise InstallError("Choose a dedicated installation subdirectory, not HOME/root")
    if any(character.isspace() for character in str(resolved)):
        raise InstallError("OpenFOAM build prefix must not contain whitespace")
    marker = prefix / ".sediment-installer.json"
    if marker.is_symlink():
        raise InstallError("Refusing symlink installation marker")
    # An empty directory is claimed like an absent one: an administrator may have
    # created it for the user, to install for all users without building as root.
    if prefix.exists() and (marker.is_file() or not prefix.is_dir() or any(prefix.iterdir())):
        if not marker.is_file():
            raise InstallError(f"Foreign/unmarked prefix: {prefix}; choose an absent or empty directory")
        if json.loads(marker.read_text(encoding="utf-8")) != {"identity": identity}:
            raise InstallError("Installer/source pin changed; choose a new prefix")
    else:
        prefix.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps({"identity": identity}) + "\n", encoding="utf-8")


def write_generated(path: Path, content: str, executable=False):
    """Only update identically-labelled installer-generated files."""
    label = "# Generated by openfoam-sediment-installer"
    if label not in content:
        raise InstallError("Generated file needs ownership label")
    if path.is_symlink():
        raise InstallError(f"Refusing generated-file symlink: {path}")
    if path.exists() and path.read_text(encoding="utf-8") != content:
        raise InstallError(f"Generated target was edited or differs: {path}; preserving it")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")
    if executable:
        path.chmod(0o755)
