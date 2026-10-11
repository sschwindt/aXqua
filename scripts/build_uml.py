#!/usr/bin/env python3
"""Draw the UML diagrams of the code documentation from the source code.

The diagrams of ``docs/code/`` are not drawn by hand. This script reads the Python
sources without importing them, so it needs none of the packages aXqua needs, and
writes for each diagram a description in the DOT language and the figure:

``docs/img/uml-packages``   the packages and which of them depends on which
``docs/img/uml-case``       the case description: one class per block of a case file
``docs/img/uml-backends``   the simulation codes behind one interface, and capabilities
``docs/img/uml-jobs``       a job: what is submitted, how it is run, what it reports
``docs/img/uml-computer``   the profile of the computer, and messages

The ``.dot`` files follow from the sources alone and are compared by a test
(``--check``), so that a diagram cannot fall behind the code. The ``.svg`` files are
drawn from them by Graphviz (``dot``), which is only needed to change a diagram.

Usage::

    python scripts/build_uml.py            # write the .dot and .svg files
    python scripts/build_uml.py --check    # compare the .dot files with the sources
"""

from __future__ import annotations

import argparse
import ast
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "src" / "axqua"
OUT = REPO / "docs" / "img"

FONT = 'fontname="Helvetica,Arial,sans-serif"'

# ------------------------------------------------------------------------- packages

#: ``key -> (title, where)`` of the packages, from the user interface down to the core.
PACKAGES = {
    "plugin": ("QGIS plugin", "qgis_plugin/axqua"),
    "cli": ("Command line", "axqua.cli, axqua.*cli"),
    "jobs": ("Job system", "axqua.jobs"),
    "install": ("Installation of the\\nsimulation software", "axqua.install"),
    "workflow": ("Workflow, studies\\nand calibration", "axqua (top level)"),
    "postproc": ("Figures and export", "axqua.postproc"),
    "telemac": ("TELEMAC backend", "axqua.solvers.telemac"),
    "openfoam": ("OpenFOAM backend", "axqua.solvers.openfoam"),
    "config": ("Case description", "axqua.config"),
    "core": ("Core", "axqua.core"),
}

#: Packages drawn on one level.
LEVELS = (("plugin",), ("cli",), ("jobs", "install"), ("workflow", "postproc"),
          ("telemac", "openfoam"), ("config",), ("core",))


def modules() -> dict[str, Path]:
    """``dotted module name -> file`` of the library."""
    found = {}
    for path in sorted(SOURCE.rglob("*.py")):
        parts = list(path.relative_to(SOURCE).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        found[".".join(["axqua", *parts])] = path
    return found


def alias_target(text: str) -> str | None:
    """The module a compatibility module stands for (``axqua.mesh`` is the mesh module
    of the TELEMAC backend), or ``None`` for an ordinary module."""
    if "sys.modules[__name__]" not in text:
        return None
    found = re.search(r"^from (axqua[\w.]*) import (\w+)", text, re.M)
    if found:
        return f"{found.group(1)}.{found.group(2)}"
    found = re.search(r"^import (axqua[\w.]+)", text, re.M)
    return found.group(1) if found else None


def package_of(module: str) -> str | None:
    """The package a module is drawn in, or ``None`` for one that is left out."""
    parts = module.split(".")[1:]
    if not parts or parts == ["__main__"]:
        return None                              # the package itself: only re-exports
    if parts[0] == "solvers":
        return parts[1] if len(parts) > 1 else None
    if parts[0] in ("core", "jobs", "install", "postproc"):
        return parts[0]
    if parts[0] == "gui":
        return None                              # the stand-alone configurator
    if parts[0] == "config":
        return "config"
    if parts[0] == "cli" or parts[0].endswith("cli"):
        return "cli"
    return "workflow"


def imported(tree: ast.AST, module: str, is_package: bool, known: dict[str, Path]
             ) -> set[str]:
    """The modules of the library that *module* imports, anywhere in its text."""
    here = module.split(".")
    if not is_package:
        here = here[:-1]
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                anchor = here[:len(here) - node.level + 1]
                base = ".".join([*anchor, base] if base else anchor)
            names.add(base)
            names.update(f"{base}.{alias.name}" for alias in node.names)
    return {name for name in names if name in known}


def package_dependencies() -> set[tuple[str, str]]:
    """``(package, package it depends on)`` for every pair with an import between."""
    known = modules()
    texts = {name: path.read_text(encoding="utf-8") for name, path in known.items()}
    aliases = {name: alias_target(text) for name, text in texts.items()}
    edges: set[tuple[str, str]] = set()
    for name, path in known.items():
        if aliases[name]:
            continue                             # another name for a module, no code
        origin = package_of(name)
        if origin is None:
            continue
        tree = ast.parse(texts[name])
        for target in imported(tree, name, path.name == "__init__.py", known):
            target = aliases.get(target) or target
            goal = package_of(target)
            if goal and goal != origin:
                edges.add((origin, goal))
    return edges


def packages_dot() -> str:
    lines = ["digraph packages {",
             f"  graph [rankdir=TB, nodesep=0.5, ranksep=0.7, {FONT}];",
             f"  node [shape=tab, style=filled, fillcolor=\"#f5f5f5\", margin=\"0.2,0.12\", "
             f"{FONT}, fontsize=11];",
             f"  edge [style=dashed, arrowhead=vee, color=\"#555555\", {FONT}, fontsize=9];"]
    for key, (title, where) in PACKAGES.items():
        lines.append(f'  {key} [label="{title}\\n{where}"];')
    for level in LEVELS:
        lines.append("  { rank=same; " + "; ".join(level) + "; }")
    # The plugin does not import the library: it starts the program and reads its answer.
    lines.append('  plugin -> cli [label="  starts the program axqua", style=solid];')
    for origin, goal in sorted(package_dependencies()):
        lines.append(f"  {origin} -> {goal};")
    lines.append("}")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- classes

#: The class diagrams: ``name -> (clusters, realizations)``. A cluster is
#: ``(title, file below src/axqua, classes or None for all)``. A realization is
#: ``(class, protocol)``: a protocol is satisfied by having its methods, without
#: inheriting from it, so the source code does not state it.
CLASS_DIAGRAMS: dict[str, tuple[list[tuple[str, str, list[str] | None]],
                                list[tuple[str, str]]]] = {
    "uml-case": ([("axqua.config", "config.py", None)], []),
    "uml-backends": (
        [("axqua.core.registry", "core/registry.py", None),
         ("axqua.core.capabilities", "core/capabilities.py", None),
         ("axqua.solvers.telemac.backend", "solvers/telemac/backend.py", None),
         ("axqua.solvers.openfoam.backend", "solvers/openfoam/backend.py", None)],
        [("BaseBackend", "SolverBackend")]),
    "uml-jobs": (
        [("axqua.jobs.model", "jobs/model.py", None),
         ("axqua.jobs.launcher", "jobs/launcher.py", None),
         ("axqua.jobs.launchers", "jobs/launchers/posix.py", None),
         ("axqua.jobs.launchers", "jobs/launchers/systemd.py", None),
         ("axqua.jobs.launchers", "jobs/launchers/windows.py", None),
         ("axqua.jobs.launchers", "jobs/launchers/wsl.py", None),
         ("axqua.jobs.results", "jobs/results.py", None)],
        [("PosixDetachedLauncher", "JobLauncher"), ("SystemdUserLauncher", "JobLauncher"),
         ("WindowsJobObjectLauncher", "JobLauncher"), ("WslLauncher", "JobLauncher")]),
    "uml-computer": (
        [("axqua.core.profile", "core/profile.py", None),
         ("axqua.core.environment", "core/environment.py", None),
         ("axqua.jobs.profiles", "jobs/profiles.py", None),
         ("axqua.core.machine", "core/machine.py", None),
         ("axqua.core.diagnostics", "core/diagnostics.py", None),
         ("axqua.core.errors", "core/errors.py", None)],
        []),
}

STEREOTYPES = (("Enum", "enumeration"), ("Protocol", "protocol"),
               ("Exception", "exception"), ("Error", "exception"))


def classes_of(path: Path) -> list[dict]:
    """The classes a file defines at its top level, in the order of the file."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name.startswith("_"):
            continue
        bases = [ast.unparse(base).split(".")[-1] for base in node.bases]
        fields = [ast.unparse(item.annotation) for item in node.body
                  if isinstance(item, ast.AnnAssign)]
        found.append({
            "name": node.name, "bases": bases, "fields": fields,
            "dataclass": any("dataclass" in ast.unparse(d) for d in node.decorator_list)})
    return found


def stereotype(entry: dict, names: set[str]) -> str:
    for base in entry["bases"]:
        if base in names:
            continue
        for ending, kind in STEREOTYPES:
            if base.endswith(ending):
                return kind
    return "dataclass" if entry["dataclass"] else ""


def classes_dot(name: str) -> str:
    clusters, realizations = CLASS_DIAGRAMS[name]
    grouped: dict[str, list[dict]] = {}
    for title, relative, chosen in clusters:
        for entry in classes_of(SOURCE / relative):
            if chosen is None or entry["name"] in chosen:
                grouped.setdefault(title, []).append(entry)
    entries = [entry for group in grouped.values() for entry in group]
    names = {entry["name"] for entry in entries}

    lines = [f"digraph {name.replace('-', '_')} {{",
             f"  graph [rankdir=LR, nodesep=0.18, ranksep=1.0, {FONT}, fontsize=11, "
             "labeljust=l];",
             f"  node [shape=box, style=filled, fillcolor=\"#f5f5f5\", {FONT}, "
             "fontsize=10, margin=\"0.12,0.06\"];",
             "  edge [color=\"#555555\"];"]
    for number, (title, group) in enumerate(grouped.items()):
        lines.append(f"  subgraph cluster_{number} {{")
        lines.append(f'    label="{title}"; color="#aaaaaa";')
        for entry in group:
            kind = stereotype(entry, names)
            label = (f"&#171;{kind}&#187;\\n" if kind else "") + entry["name"]
            lines.append(f'    {entry["name"]} [label="{label}"];')
        lines.append("  }")
    for entry in entries:
        for base in entry["bases"]:
            if base in names:                    # generalization: a hollow triangle
                lines.append(f'  {entry["name"]} -> {base} [arrowhead=empty];')
        parts = []
        for annotation in entry["fields"]:
            for part in re.findall(r"[A-Za-z_]\w*", annotation):
                if part in names and part != entry["name"] and part not in parts:
                    parts.append(part)
        for part in parts:                       # composition: a filled diamond
            lines.append(f'  {entry["name"]} -> {part} [arrowtail=diamond, dir=back];')
    for realizing, protocol in realizations:
        if realizing not in names or protocol not in names:
            raise SystemExit(f"{name}: {realizing} or {protocol} is not in the diagram")
        lines.append(f"  {realizing} -> {protocol} [arrowhead=empty, style=dashed];")
    lines.append("}")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------------------ files


def diagrams() -> dict[str, str]:
    """``file name without suffix -> DOT text`` of every diagram."""
    out = {"uml-packages": packages_dot()}
    out.update({name: classes_dot(name) for name in CLASS_DIAGRAMS})
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--check", action="store_true",
                        help="compare the .dot files with the sources; write nothing")
    args = parser.parse_args(argv)
    drawn = diagrams()
    if args.check:
        stale = [name for name, text in drawn.items()
                 if not (OUT / f"{name}.dot").is_file()
                 or (OUT / f"{name}.dot").read_text(encoding="utf-8") != text]
        stale += [name for name in drawn if not (OUT / f"{name}.svg").is_file()
                  and name not in stale]
        if stale:
            print("not up to date: " + ", ".join(stale)
                  + ". Run 'python scripts/build_uml.py'.", file=sys.stderr)
            return 1
        print(f"{len(drawn)} diagrams are up to date")
        return 0
    dot = shutil.which("dot")
    if dot is None:
        print("Graphviz is needed to draw the figures (the program 'dot').",
              file=sys.stderr)
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    for name, text in drawn.items():
        (OUT / f"{name}.dot").write_text(text, encoding="utf-8")
        done = subprocess.run([dot, "-Tsvg", str(OUT / f"{name}.dot")],
                              capture_output=True, text=True, check=False)
        if done.returncode:
            print(done.stderr, file=sys.stderr)
            return 1
        # without the comment that names the version of Graphviz that drew the figure
        svg = re.sub(r"<!-- Generated by graphviz.*?-->\n", "", done.stdout, flags=re.S)
        (OUT / f"{name}.svg").write_text(svg, encoding="utf-8")
        print(f"{name}: {text.count(' -> ')} relations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
