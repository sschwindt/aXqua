"""Where the solvers live on *this* machine - never in the shared repository.

A case configuration describes a **reach**: its geodata, its discharge, its
roughness, its calibration targets. Those travel. The path to a TELEMAC
``pysource.sh`` or an OpenFOAM ``etc/bashrc`` describes a **machine**, and putting
one in a tracked ``case-config.yml`` makes the case unopenable by anyone else and
turns every checkout into a merge conflict over somebody's home directory.

That is not hypothetical here: fourteen tracked case configs named one of three
different users' absolute paths, and one of them was committed pointing at a
second machine entirely.

Resolution order, first hit wins:

1. the environment - ``AXQUA_TELEMAC_PYSOURCE`` / ``AXQUA_OPENFOAM_BASHRC``. This
   is what a CI job, a container or a module-load script sets, and it beats
   everything so a batch system can override without editing a file.
2. a **case-local override** - ``solvers.local.yml`` beside the case config,
   gitignored. For a case that legitimately needs a different build from the rest
   of the machine. Ahead of the machine setting, because precedence follows
   specificity.
3. the **machine settings file** - ``$AXQUA_HOME/solvers.yml``, else
   ``$XDG_CONFIG_HOME/axqua/solvers.yml``, else ``~/.config/axqua/solvers.yml``.
   Outside the repository by construction, so it can never be committed. This is
   what the QGIS plugin writes when the user picks or installs a solver.
4. whatever the case config itself says - kept so every existing config keeps
   working unchanged, and because a self-contained case on a single-user machine
   is a reasonable thing to have.
5. **discovery** of the usual install layouts, so a fresh checkout on a machine
   that has the solver often needs no configuration at all.

Only step 4 is in the repository, and it is now the fallback rather than the
mechanism.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("axqua")

#: Environment variable per solver key.
ENV_VARS = {
    "telemac": "AXQUA_TELEMAC_PYSOURCE",
    "openfoam": "AXQUA_OPENFOAM_BASHRC",
    "visit": "AXQUA_VISIT",
}

#: Filename of the per-machine settings, wherever it is found.
SETTINGS_NAME = "solvers.yml"

#: Filename of the per-case override, which sits beside the case config.
LOCAL_NAME = "solvers.local.yml"

#: Glob patterns searched during discovery, in order of preference. Deliberately
#: not anyone's home directory: these are the layouts an install *creates*, under
#: roots that are conventional rather than personal.
DISCOVERY = {
    "telemac": (
        "/opt/telemac*/configs/pysource.*.sh",
        "/usr/local/telemac*/configs/pysource.*.sh",
        "telemac*/configs/pysource.*.sh",
        "*/telemac-mascaret/configs/pysource.*.sh",
    ),
    "openfoam": (
        "/opt/openfoam*/etc/bashrc",
        "/usr/lib/openfoam/openfoam*/etc/bashrc",
        "OpenFOAM-*/etc/bashrc",
        "*/OpenFOAM-*/etc/bashrc",
    ),
    "visit": ("/opt/visit*/bin/visit", "visit*/bin/visit", "*/visit*/bin/visit"),
}

#: Roots the relative DISCOVERY patterns are joined to. ``AXQUA_SOLVER_ROOT``
#: lets a site add its own (here: a shared /home/IWS/public) without that root
#: ever appearing in the repository.
def _discovery_roots() -> list[Path]:
    roots = [Path(p) for p in os.environ.get("AXQUA_SOLVER_ROOT", "").split(os.pathsep) if p]
    roots += [Path("/opt"), Path("/usr/local")]
    return roots


def settings_path() -> Path:
    """Where the per-machine settings file lives (whether or not it exists)."""
    if os.environ.get("AXQUA_HOME"):
        return Path(os.environ["AXQUA_HOME"]) / SETTINGS_NAME
    base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    return Path(base) / "axqua" / SETTINGS_NAME


def _read_yaml(path: Path) -> dict:
    try:
        import yaml

        with open(path, "r", encoding="utf-8") as handle:
            return yaml.safe_load(handle) or {}
    except Exception as exc:  # noqa: BLE001 - a bad settings file must not kill a build
        log.warning("could not read solver settings %s (%s: %s); ignoring it",
                    path, type(exc).__name__, exc)
        return {}


def _discover(key: str) -> Path | None:
    patterns = DISCOVERY.get(key, ())
    for pattern in patterns:
        if pattern.startswith("/"):
            matches = sorted(Path("/").glob(pattern.lstrip("/")))
        else:
            matches = []
            for root in _discovery_roots():
                if root.is_dir():
                    matches += sorted(root.glob(pattern))
        readable = [m for m in matches if m.is_file()]
        if readable:
            return readable[-1]         # the highest-sorting, i.e. newest version
    return None


@dataclass(frozen=True)
class Resolved:
    """Where a solver was found, and which rule found it."""

    path: Path | None
    source: str

    def __bool__(self) -> bool:
        return self.path is not None

    def line(self, key: str) -> str:
        if self.path is None:
            return (f"{key}: not configured. Set {ENV_VARS.get(key, '')}, or add it "
                    f"to {settings_path()}, or name it in the case config.")
        return f"{key}: {self.path}  (from {self.source})"


def resolve(key: str, *, configured=None, case_dir=None) -> Resolved:
    """Find *key*'s setup script for this machine. See the module docstring."""
    env = os.environ.get(ENV_VARS.get(key, ""))
    if env:
        return Resolved(Path(env).expanduser(), f"${ENV_VARS[key]}")

    # Case-local BEFORE machine-wide: precedence follows specificity, or an
    # override that exists to give one case a different build from the rest of
    # the machine would be silently outranked by the machine setting it is there
    # to override.
    if case_dir is not None:
        local = Path(case_dir) / LOCAL_NAME
        if local.is_file():
            value = _read_yaml(local).get(key)
            if value:
                path = Path(str(value)).expanduser()
                if not path.is_absolute():
                    path = (Path(case_dir) / path).resolve()
                return Resolved(path, str(local))

    machine = settings_path()
    if machine.is_file():
        value = _read_yaml(machine).get(key)
        if value:
            return Resolved(Path(str(value)).expanduser(), str(machine))

    if configured:
        # Relative to the CASE, exactly as the case-local override above is. A config
        # has always been allowed to say `pysource: pysource.sh` and mean the one
        # beside it, and `load_config` used to resolve that against the config's own
        # directory before handing it over. Returning it unresolved here made the
        # meaning depend on the working directory instead, so a case that had always
        # loaded stopped validating - and only on a machine with no settings file,
        # because one of those outranks the config and hid it.
        path = Path(str(configured)).expanduser()
        if not path.is_absolute() and case_dir is not None:
            path = (Path(case_dir) / path).resolve()
        return Resolved(path, "case config")

    found = _discover(key)
    if found:
        log.info("discovered %s at %s; record it in %s to pin it",
                 key, found, settings_path())
        return Resolved(found, "discovered")
    return Resolved(None, "not found")


def write_settings(values: dict, path: Path | None = None) -> Path:
    """Write the per-machine settings file. The QGIS plugin's entry point.

    Merges into whatever is already there rather than replacing it, so setting
    OpenFOAM does not silently drop a working TELEMAC entry.
    """
    import yaml

    target = Path(path) if path is not None else settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    current = _read_yaml(target) if target.is_file() else {}
    current.update({k: str(v) for k, v in values.items() if v is not None})
    with open(target, "w", encoding="utf-8") as handle:
        yaml.safe_dump(current, handle, sort_keys=True)
    log.info("wrote solver settings to %s", target)
    return target


def report(case_dir=None) -> list[str]:
    """One line per solver: where it resolves and which rule won."""
    return [resolve(key, case_dir=case_dir).line(key) for key in sorted(ENV_VARS)]
