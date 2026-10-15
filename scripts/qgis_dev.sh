#!/usr/bin/env bash
# Start QGIS with the aXqua plugin of this checkout, in a QGIS user profile of its own.
#
#   scripts/qgis_dev.sh [--setup-only] [arguments passed on to qgis]
#
# The plugin needs QGIS 3.44 or newer, which many distributions do not ship yet. This
# script starts the QGIS of a conda environment instead and leaves the system QGIS and
# its profiles alone. Create the environment once with:
#
#   conda create -n qgis-dev -c conda-forge "qgis>=3.44"
#
# On the first start the script links the plugin folder of this checkout into the user
# profile and enables it there, so edits to the plugin are live after a QGIS restart.
#
# Settings, all optional:
#
#   QGIS_ENV      conda environment that holds QGIS, as a name or a folder (qgis-dev)
#   QGIS_PROFILE  QGIS user profile to use (axqua-dev)
#   AXQUA_ENV     conda environment that holds aXqua (axqua-env)
#   AXQUA_EXE     the axqua program the plugin calls; overrides AXQUA_ENV
#
# The plugin never imports aXqua. It calls the program named by AXQUA_EXE, so QGIS and
# aXqua can live in two environments with two different Python versions.

set -euo pipefail

setup_only=false
if [ "${1:-}" = "--setup-only" ]; then
    setup_only=true
    shift
fi

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
plugin="$repo/qgis_plugin/axqua"
qgis_env="${QGIS_ENV:-qgis-dev}"
axqua_env="${AXQUA_ENV:-axqua-env}"
profile="${QGIS_PROFILE:-axqua-dev}"

fail() {
    echo "qgis_dev.sh: $*" >&2
    exit 1
}

# ---------------------------------------------------------------- the conda installation
conda_base=""
if [ -n "${CONDA_EXE:-}" ]; then
    conda_base="$(dirname "$(dirname "$CONDA_EXE")")"
elif command -v conda > /dev/null 2>&1; then
    conda_base="$(conda info --base)"
else
    for candidate in "${MAMBA_ROOT_PREFIX:-}" "$HOME/miniforge3" "$HOME/mambaforge" "$HOME/miniconda3" "$HOME/anaconda3"; do
        if [ -n "$candidate" ] && [ -d "$candidate/envs" ]; then
            conda_base="$candidate"
            break
        fi
    done
fi

env_prefix() {
    # A folder is taken as it is; a name is looked up in the conda installation.
    if [ -d "$1" ]; then
        (cd "$1" && pwd)
    elif [ -n "$conda_base" ] && [ -d "$conda_base/envs/$1" ]; then
        echo "$conda_base/envs/$1"
    fi
}

qgis_prefix="$(env_prefix "$qgis_env")"
[ -n "$qgis_prefix" ] || fail "no conda environment '$qgis_env'. Create it with: conda create -n qgis-dev -c conda-forge \"qgis>=3.44\""
[ -x "$qgis_prefix/bin/qgis" ] || fail "the environment $qgis_prefix holds no QGIS (bin/qgis is missing)"

# ---------------------------------------------------------------- the axqua program
# Looked up before the QGIS environment is activated, because that environment does not
# hold aXqua and would hide the one on the PATH of the calling shell.
if [ -z "${AXQUA_EXE:-}" ]; then
    axqua_prefix="$(env_prefix "$axqua_env")"
    if [ -n "$axqua_prefix" ] && [ -x "$axqua_prefix/bin/axqua" ]; then
        AXQUA_EXE="$axqua_prefix/bin/axqua"
    else
        AXQUA_EXE="$(command -v axqua || true)"
    fi
fi
if [ -n "$AXQUA_EXE" ]; then
    export AXQUA_EXE
else
    echo "qgis_dev.sh: no axqua program found. Name it in the plugin under aXqua > Settings." >&2
fi

# ---------------------------------------------------------------- the QGIS user profile
profile_dir="${QGIS_CUSTOM_CONFIG_PATH:-$HOME/.local/share/QGIS/QGIS3}/profiles/$profile"
mkdir -p "$profile_dir/python/plugins" "$profile_dir/QGIS"
link="$profile_dir/python/plugins/axqua"
if [ -L "$link" ] || [ ! -e "$link" ]; then
    ln -sfn "$plugin" "$link"
else
    fail "$link exists and is not a link. Remove the installed plugin from the profile '$profile' first."
fi

# Enable the plugin in the profile. The file is only touched where the entry is missing.
ini="$profile_dir/QGIS/QGIS3.ini"
if [ ! -f "$ini" ]; then
    printf '[PythonPlugins]\naxqua=true\n' > "$ini"
elif ! grep -q '^axqua=' "$ini"; then
    if grep -q '^\[PythonPlugins\]' "$ini"; then
        sed -i '/^\[PythonPlugins\]/a axqua=true' "$ini"
    else
        printf '\n[PythonPlugins]\naxqua=true\n' >> "$ini"
    fi
fi

echo "QGIS:    $qgis_prefix/bin/qgis"
echo "profile: $profile_dir"
echo "plugin:  $plugin"
echo "axqua:   ${AXQUA_EXE:-not found}"
if $setup_only; then
    exit 0
fi

# ---------------------------------------------------------------- start QGIS
# Activation sets the variables QGIS, GDAL and PROJ of the environment need.
if [ -n "$conda_base" ] && [ -f "$conda_base/etc/profile.d/conda.sh" ]; then
    set +u
    # shellcheck disable=SC1091
    source "$conda_base/etc/profile.d/conda.sh"
    conda activate "$qgis_prefix"
    set -u
else
    export PATH="$qgis_prefix/bin:$PATH"
    export QGIS_PREFIX_PATH="$qgis_prefix"
fi
exec qgis --profile "$profile" "$@"
