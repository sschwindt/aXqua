#!/usr/bin/env bash
# build_i18n.sh - build the documentation in English, German and French, as one site.
#
#   docs/_build/site/        English   https://axqua.eu/
#   docs/_build/site/de/     German    https://axqua.eu/de/   (axqua.de forwards here)
#   docs/_build/site/fr/     French    https://axqua.eu/fr/   (axqua.fr forwards here)
#
# The German and French pages come from the catalogs in docs/locale/<lang>/LC_MESSAGES, which this
# script brings up to date first (docs/i18n/translate.py, see docs/i18n/README.md). Afterwards every
# page gets the language links, hreflang links and, in German and French, the note that the page is
# a machine translation (docs/i18n/inject_i18n.py). deploy.sh calls this script and publishes the
# result on the gh-pages branch. Read the Docs does not run it: it builds the English pages from
# docs/conf.py alone.
#
# Settings, all optional:
#   ENGINE=none     build with the translations that are there, translate nothing (the default:
#                   a quick look at the site; new text then stays English in German and French)
#   ENGINE=argos    translate what is new or changed, offline (deploy.sh uses this)
#   ENGINE=deepl    the same with DeepL (pip install deepl; DEEPL_API_KEY)
#   RETRANSLATE=1   translate every machine translation again (reviewed entries are kept)
#   I18N_JOBS=N     translation workers (default: a quarter of the cores, at most 4)
#   I18N_FAST=1     greedy decoding: about a quarter faster, slightly worse wording
#   DOCSTRINGS=1    also translate the reference of the Python code (left in English otherwise)
#   DOCS_PYTHON=... the Python of the environment from docs/i18n/environment.yml
#   SITE_URL=...    the address of the site (default https://axqua.eu)
#
# Usage:  bash docs/i18n/build_i18n.sh
set -euo pipefail

I18N_DIR="$(cd "$(dirname "$0")" && pwd)"
DOCS_DIR="$(dirname "$I18N_DIR")"
BUILD="$DOCS_DIR/_build"
SITE="$BUILD/site"
POT="$BUILD/i18n/gettext"
LOGS="$BUILD/i18n"
ENGINE="${ENGINE:-none}"
SITE_URL="${SITE_URL:-https://axqua.eu}"
SITE_URL="${SITE_URL%/}"
LANGS=(de fr)

# The Python of the docs environment: DOCS_PYTHON, else an environment called axqua-docs under one of
# the usual conda roots. `mamba run` is avoided on purpose: in a shell that did not run `mamba
# shell init` it looks for environments in another root.
find_docs_python() {
  if [ -n "${DOCS_PYTHON:-}" ]; then
    echo "$DOCS_PYTHON"
    return 0
  fi
  local roots=("${MAMBA_ROOT_PREFIX:-}" "$HOME/miniforge3" "$HOME/mambaforge" "$HOME/miniconda3"
               "$HOME/anaconda3" "$HOME/.local/share/mamba")
  if command -v conda > /dev/null 2>&1; then
    roots+=("$(conda info --base 2> /dev/null || true)")
  fi
  local root
  for root in "${roots[@]}"; do
    if [ -n "$root" ] && [ -x "$root/envs/axqua-docs/bin/python" ]; then
      echo "$root/envs/axqua-docs/bin/python"
      return 0
    fi
  done
  return 1
}
if ! PY="$(find_docs_python)"; then
  echo "ERROR: no Python for the documentation. Create the environment once with"
  echo "         mamba env create -f docs/i18n/environment.yml"
  echo "       or name a Python that has Sphinx and Argos Translate in DOCS_PYTHON."
  exit 1
fi
echo "documentation Python: $PY"

TR_ARGS=(--pot "$POT" --engine "$ENGINE")
if [ "${RETRANSLATE:-0}" = "1" ]; then TR_ARGS+=(--retranslate); fi
if [ "${DOCSTRINGS:-0}" = "1" ]; then TR_ARGS+=(--docstrings); fi
if [ -n "${I18N_JOBS:-}" ]; then TR_ARGS+=(--jobs "$I18N_JOBS"); fi
if [ "${I18N_FAST:-0}" = "1" ]; then export ARGOS_BEAM_SIZE=1; fi

rm -rf "$SITE" "$POT"
mkdir -p "$LOGS"

echo "=== 1. English -> $SITE ==="
# Warnings are errors here, as in the CI build of the documentation.
"$PY" -m sphinx -b html -E -q -W --keep-going -d "$BUILD/doctrees/en" \
  -D html_baseurl="$SITE_URL/" "$DOCS_DIR" "$SITE"

echo "=== 2. Message templates -> $POT ==="
"$PY" -m sphinx -b gettext -E -q -d "$BUILD/doctrees/gettext" "$DOCS_DIR" "$POT"

echo "=== 3. Catalogs in docs/locale (engine: $ENGINE) ==="
"$PY" "$I18N_DIR/translate.py" "${TR_ARGS[@]}"

for lang in "${LANGS[@]}"; do
  echo "=== 4.$lang Pages in $lang -> $SITE/$lang ==="
  # Not -W: a warning about a translated message must not stop the publication of the English
  # pages. They are collected in a log, and the count is reported below.
  "$PY" -m sphinx -b html -E -q --keep-going -d "$BUILD/doctrees/$lang" -D language="$lang" \
    -D html_baseurl="$SITE_URL/$lang/" -w "$LOGS/warnings-$lang.log" "$DOCS_DIR" "$SITE/$lang"
  count=$(grep -c "WARNING\|ERROR" "$LOGS/warnings-$lang.log" || true)
  echo "   warnings: $count (see $LOGS/warnings-$lang.log)"
done

echo "=== 5. Language links, hreflang, note on machine translation, 404 page ==="
"$PY" "$I18N_DIR/inject_i18n.py" "$SITE" --site-url "$SITE_URL"

echo "=== Done: $SITE (/, /de/, /fr/) ==="
