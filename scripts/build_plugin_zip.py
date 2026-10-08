#!/usr/bin/env python3
"""Build (and validate) the QGIS plugin zip.

Runnable locally and from CI, so the artifact a release ships is the one a developer can
reproduce - rather than something only the workflow knows how to make.

plugins.qgis.org has a short list of hard requirements, and every one of them is checked
here rather than discovered when an upload is rejected:

* the zip contains exactly **one top-level folder**, named after the plugin;
* ``metadata.txt`` carries a name, a version, ``qgisMinimumVersion``, a description, an
  author, a GPL-compatible licence, and working homepage / repository / tracker links;
* no compiled or generated files, no ``__pycache__``, no VCS directories;
* under 25 MB.

The documentation is built into the archive, so that *Help* in the plugin works without
a network connection and shows the documentation of the installed version. That needs
Sphinx (``pip install -r docs/requirements-docs.txt``). Without Sphinx the archive is
built without it and *Help* opens the published documentation.

Usage::

    python scripts/build_plugin_zip.py                 # -> dist/axqua-qgis-<ver>.zip
    python scripts/build_plugin_zip.py --check         # validate only, build nothing
    python scripts/build_plugin_zip.py -o somewhere.zip
    python scripts/build_plugin_zip.py --help-only     # only build the help into the
                                                       # plugin folder (linked installs)
    python scripts/build_plugin_zip.py --no-help       # an archive without the help
"""

from __future__ import annotations

import argparse
import importlib.util
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PLUGIN_DIR = REPO / "qgis_plugin" / "axqua"
DEFAULT_OUT = REPO / "dist"

#: plugins.qgis.org rejects a package over this.
MAX_BYTES = 25 * 1024 * 1024

REQUIRED_FIELDS = ("name", "qgisMinimumVersion", "description", "about", "version",
                   "author", "email", "repository", "tracker", "homepage", "license")

#: Never shipped. ``*_rc.py`` especially: a compiled resource module is built against one
#: Qt major version and fails to import on the other, which is the most common reason a
#: plugin loads on QGIS 3 and not on QGIS 4.
EXCLUDE_DIRS = {"__pycache__", ".git", ".github", ".pytest_cache", ".mypy_cache",
                ".ruff_cache", "__MACOSX", ".idea", ".vscode"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".qrc", ".ui.autosave", ".orig", ".rej", ".swp"}
EXCLUDE_NAMES = {".DS_Store", "Thumbs.db"}


def read_metadata(path: Path) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        # Continuation lines (the changelog) are indented; they belong to the previous key.
        if not line or line.startswith(("#", "[", " ", "\t")) or "=" not in line:
            continue
        key, _, value = line.partition("=")
        fields[key.strip()] = value.strip()
    return fields


def qgis_cannot_read(path: Path) -> str:
    """Why QGIS could not read *path*; empty when it can.

    QGIS finds plugins with ``configparser`` (``findPlugins`` in ``qgis/utils.py``) and
    **skips a plugin whose metadata does not parse, without a message**: the plugin is
    then missing from the Plugin Manager, and nothing says why. :func:`read_metadata`
    above is more forgiving than that, so a file it accepts can still be one QGIS
    drops. A changelog line that starts in the first column did exactly this - it is
    not a continuation line, so it is a syntax error - and the plugin could not be
    enabled in any QGIS for a month.
    """
    import configparser

    parser = configparser.ConfigParser()
    try:
        with path.open(encoding="utf-8") as handle:
            parser.read_file(handle)
    except configparser.Error as exc:
        return " ".join(str(exc).split())
    if not parser.has_section("general"):
        return "there is no [general] section"
    return ""


def validate(plugin_dir: Path) -> tuple[dict[str, str], list[str]]:
    """Check the plugin folder. Returns ``(metadata, problems)``."""
    problems: list[str] = []
    metadata_path = plugin_dir / "metadata.txt"
    if not metadata_path.is_file():
        return {}, [f"no metadata.txt in {plugin_dir}"]

    fields = read_metadata(metadata_path)
    unreadable = qgis_cannot_read(metadata_path)
    if unreadable:
        problems.append("QGIS cannot read metadata.txt and would not list the plugin at "
                        f"all: {unreadable}")
    for key in REQUIRED_FIELDS:
        if not fields.get(key):
            problems.append(f"metadata.txt is missing '{key}'")

    if "GPL" not in fields.get("license", ""):
        problems.append("the licence must be GPL-compatible (the plugin links PyQGIS)")
    if not (plugin_dir / "LICENSE").is_file():
        problems.append("no LICENSE file in the plugin folder")
    if not (plugin_dir / "__init__.py").is_file():
        problems.append("no __init__.py - QGIS would not be able to load the plugin")
    if "classFactory" not in (plugin_dir / "__init__.py").read_text(encoding="utf-8"):
        problems.append("__init__.py does not define classFactory()")

    icon = fields.get("icon", "")
    if icon and not (plugin_dir / icon).is_file():
        problems.append(f"metadata.txt names icon={icon}, which is not there")

    # Documentation is a stated requirement, not a nicety.
    if not (plugin_dir / "README.md").is_file():
        problems.append("no README.md - plugins need at least minimal documentation")

    for path in plugin_dir.rglob("*"):
        if path.is_file() and path.name.endswith("_rc.py"):
            problems.append(f"{path.name} is a compiled Qt resource module; it will not "
                            "import on the other Qt major version")
    return fields, problems


def _sections(plugin_dir: Path):
    """The table of tabs, loaded by path: the plugin package itself needs QGIS."""
    spec = importlib.util.spec_from_file_location(
        "axqua_plugin_sections", plugin_dir / "gui" / "sections.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module          # dataclasses look their module up there
    spec.loader.exec_module(module)
    return module


def build_help(plugin_dir: Path, docs_dir: Path | None = None) -> Path | None:
    """Build the documentation into ``<plugin>/help`` with one redirect page per tab.

    ``help/html/`` holds the documentation; ``help/<tab>.html`` sends the browser to
    the page and the anchor of that tab. Returns the folder, or ``None`` where Sphinx
    is not installed - the plugin then opens the published documentation.
    """
    docs_dir = docs_dir or (REPO / "docs")
    if importlib.util.find_spec("sphinx") is None:
        print("  Sphinx is not installed: the help is not built into the plugin "
              "(Help will open the published documentation)")
        return None
    target = plugin_dir / "help"
    if target.exists():
        shutil.rmtree(target)
    html = target / "html"
    # The tag switches the source listings off (docs/conf.py): they are 10 MB, and the
    # archive has a limit of 25.
    subprocess.run([sys.executable, "-m", "sphinx", "-q", "-b", "html", "-t",
                    "plugin_help", str(docs_dir), str(html)], check=True)
    # what a reader of the pages does not need, and what would only add megabytes
    for junk in (html / ".doctrees", html / "_sources"):
        shutil.rmtree(junk, ignore_errors=True)
    for junk in (html / ".buildinfo", html / "objects.inv"):
        junk.unlink(missing_ok=True)
    # The theme ships every font in five formats. Every browser of the last ten years
    # reads woff2, and the style sheets fall back to a system font without the others.
    for font in html.rglob("*"):
        if font.suffix in {".eot", ".ttf", ".woff", ".svg"} and "fonts" in font.parts:
            font.unlink()
    sections = _sections(plugin_dir)
    for key, page, label in sections.help_keys():
        if not (html / f"{page}.html").is_file():
            raise SystemExit(f"the help for the tab {key!r} needs {page}.html, which "
                             "the documentation build did not produce")
        (target / f"{key}.html").write_text(sections.redirect_page(page, label),
                                            encoding="utf-8")
    size = sum(f.stat().st_size for f in target.rglob("*") if f.is_file())
    print(f"  help: {len(sections.help_keys())} tabs, {size / 1024 / 1024:.1f} MiB "
          f"in {target}")
    return target


def included(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    if any(part in EXCLUDE_DIRS for part in relative.parts):
        return False
    if path.name in EXCLUDE_NAMES or path.suffix in EXCLUDE_SUFFIXES:
        return False
    return path.is_file()


def build(plugin_dir: Path, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    # The single top-level folder QGIS unpacks into python/plugins/, named exactly as the
    # package - anything else and the plugin will not be importable.
    top = plugin_dir.name
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(plugin_dir.rglob("*")):
            if not included(path, plugin_dir):
                continue
            archive.write(path, Path(top) / path.relative_to(plugin_dir))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--plugin-dir", type=Path, default=PLUGIN_DIR)
    parser.add_argument("-o", "--out", type=Path, default=None)
    parser.add_argument("--check", action="store_true",
                        help="validate only; build nothing")
    parser.add_argument("--help-only", action="store_true",
                        help="build the documentation into the plugin folder and stop")
    parser.add_argument("--no-help", action="store_true",
                        help="build the archive without the documentation")
    args = parser.parse_args(argv)

    fields, problems = validate(args.plugin_dir)
    if problems:
        print("The plugin folder is not ready to publish:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    version = fields.get("version", "0.0.0")
    print(f"{fields.get('name')} {version} - metadata ok")
    print(f"  QGIS {fields['qgisMinimumVersion']} to "
          f"{fields.get('qgisMaximumVersion', '(unbounded)')}")
    if args.check:
        return 0
    if not args.no_help:
        build_help(args.plugin_dir)
    if args.help_only:
        return 0

    out = args.out or (DEFAULT_OUT / f"axqua-qgis-{version}.zip")
    build(args.plugin_dir, out)
    size = out.stat().st_size
    print(f"  wrote {out} ({size / 1024:.0f} KiB)")
    if size > MAX_BYTES:
        print(f"the package is over the {MAX_BYTES // 1024 // 1024} MB limit "
              "plugins.qgis.org enforces", file=sys.stderr)
        return 1

    with zipfile.ZipFile(out) as archive:
        tops = {Path(n).parts[0] for n in archive.namelist()}
    if len(tops) != 1:
        print(f"the zip must contain exactly one top-level folder, found: {sorted(tops)}",
              file=sys.stderr)
        return 1
    print(f"  one top-level folder: {tops.pop()}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
