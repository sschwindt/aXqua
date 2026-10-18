#!/usr/bin/env bash
# deploy.sh - build the aXqua documentation in English, German and French and publish it on GitHub
# Pages (branch gh-pages, https://axqua.eu; axqua.de and axqua.fr forward to /de/ and /fr/).
#
# Usage:
#   bash deploy.sh                       build, update the translations and copy the site into the
#                                        gh-pages worktree; nothing is committed or pushed
#   bash deploy.sh --push                ... then commit the updated translations on main, push
#                                        main, and commit and push gh-pages
#   bash deploy.sh --push --retranslate  ... translating every machine translation again
#
# Pushing main also updates Read the Docs (axqua.readthedocs.io, axqua.ch), which builds the English
# pages from main by itself, and it is how the translations reach a Read the Docs translation
# project. The translation is incremental: only what is new or changed since the last deploy goes
# to the engine (docs/i18n/README.md). The settings of docs/i18n/build_i18n.sh apply here too
# (ENGINE, I18N_JOBS, I18N_FAST, DOCSTRINGS, DOCS_PYTHON); a deploy translates with Argos unless
# ENGINE says otherwise.
#
#   DEPLOY_DIR   the worktree of the gh-pages branch (default: aXqua-gh-pages beside this checkout;
#                created with `git worktree add` if it does not exist)
set -euo pipefail

PUSH=0
for arg in "$@"; do
  case "$arg" in
    --push) PUSH=1 ;;
    --retranslate) export RETRANSLATE=1 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown argument: $arg (expected --push and/or --retranslate)"; exit 2 ;;
  esac
done

REPO="$(cd "$(dirname "$0")" && pwd)"
DEPLOY_DIR="${DEPLOY_DIR:-$(dirname "$REPO")/aXqua-gh-pages}"
SITE="$REPO/docs/_build/site"
DOMAIN="axqua.eu"
export SITE_URL="https://$DOMAIN"
export ENGINE="${ENGINE:-argos}"
LOG="$REPO/docs/_build/deploy.log"

echo "=== 0. Checks ==="
branch=$(git -C "$REPO" branch --show-current)
if [ "$PUSH" = "1" ] && [ "$branch" != "main" ]; then
  echo "ERROR: $REPO is on branch '$branch'. The documentation is published from main."
  exit 1
fi
if [ "$PUSH" = "1" ]; then
  # What is published has to be what main holds: the only change a deploy may commit is the update
  # of the translations it makes itself.
  dirty=$(git -C "$REPO" status --porcelain -- . ':(exclude)docs/locale' | awk 'NR<=20')
  if [ -n "$dirty" ]; then
    echo "ERROR: uncommitted changes in $REPO. Commit or stash them first:"
    echo "$dirty"
    exit 1
  fi
  git -C "$REPO" fetch -q origin main
  if ! git -C "$REPO" merge-base --is-ancestor origin/main main; then
    echo "ERROR: origin/main has commits that main does not have. Pull first: git pull --ff-only"
    exit 1
  fi
  ahead=$(git -C "$REPO" log --oneline origin/main..main)
  if [ -n "$ahead" ]; then
    echo "These commits of main are not on GitHub yet and are pushed with the documentation:"
    echo "$ahead" | awk 'NR<=30'
  fi
fi

# The worktree of gh-pages. rsync --delete empties whatever folder it is given, so the folder has
# to be that worktree and nothing else.
if [ ! -e "$DEPLOY_DIR" ]; then
  git -C "$REPO" fetch -q origin gh-pages || true
  git -C "$REPO" worktree add "$DEPLOY_DIR" gh-pages
fi
if [ ! -e "$DEPLOY_DIR/.git" ]; then
  echo "ERROR: DEPLOY_DIR=$DEPLOY_DIR is not a git worktree."
  exit 1
fi
deploy_branch=$(git -C "$DEPLOY_DIR" rev-parse --abbrev-ref HEAD)
if [ "$deploy_branch" != "gh-pages" ]; then
  echo "ERROR: $DEPLOY_DIR is on branch '$deploy_branch', not gh-pages."
  exit 1
fi
if [ "$PUSH" = "1" ]; then
  git -C "$DEPLOY_DIR" fetch -q origin gh-pages
  if ! git -C "$DEPLOY_DIR" merge --ff-only -q origin/gh-pages; then
    echo "ERROR: gh-pages in $DEPLOY_DIR and on GitHub have diverged. Sort that out first."
    exit 1
  fi
fi
echo "   site: $DEPLOY_DIR (gh-pages) -> $SITE_URL"

echo "=== 1. Build (engine: $ENGINE) ==="
mkdir -p "$(dirname "$LOG")"
set +e
bash "$REPO/docs/i18n/build_i18n.sh" 2>&1 | tee "$LOG"
build=${PIPESTATUS[0]}
set -e
if [ "$build" -ne 0 ]; then
  echo "ERROR: the build failed (status $build), see $LOG"
  exit "$build"
fi

echo "=== 2. Verify the build ==="
for page in index.html de/index.html fr/index.html 404.html _i18n/lang.js; do
  if [ ! -f "$SITE/$page" ]; then
    echo "ERROR: $SITE/$page is missing"
    exit 1
  fi
done

echo "=== 3. Copy into $DEPLOY_DIR ==="
rsync -a --delete --exclude=".git" --exclude="CNAME" --exclude=".nojekyll" --exclude="_help" \
  "$SITE/" "$DEPLOY_DIR/"
# CNAME holds the domain GitHub Pages serves the branch at; .nojekyll keeps GitHub from running
# Jekyll, which would drop every folder that starts with an underscore (_static, _images, _i18n).
# _help is the forwarding page that Help in the plugin writes into a local build.
echo "$DOMAIN" > "$DEPLOY_DIR/CNAME"
touch "$DEPLOY_DIR/.nojekyll"
git -C "$DEPLOY_DIR" add -A
changes=$(git -C "$DEPLOY_DIR" status --short | wc -l)
git -C "$DEPLOY_DIR" status --short | awk 'NR<=20'
if [ "$changes" -gt 20 ]; then echo "   ... and $((changes - 20)) more"; fi
echo "   changed files on gh-pages: $changes"

locale_changes=$(git -C "$REPO" status --porcelain -- docs/locale | wc -l)
echo "   changed translation catalogs on main: $locale_changes"

if [ "$PUSH" != "1" ]; then
  echo ""
  echo "Nothing was committed. Look at the site with:"
  echo "  xdg-open $SITE/index.html"
  echo "and publish it with:  bash deploy.sh --push"
  exit 0
fi

echo "=== 4. Commit the translations and push main ==="
if [ "$locale_changes" -gt 0 ]; then
  git -C "$REPO" add docs/locale
  git -C "$REPO" commit -q -m "Update the German and French translations of the documentation"
  echo "   committed $(git -C "$REPO" rev-parse --short HEAD)"
else
  echo "   the translations did not change"
fi
git -C "$REPO" push origin main
main_sha=$(git -C "$REPO" rev-parse main)
if [ "$(git -C "$REPO" ls-remote origin refs/heads/main | cut -f1)" != "$main_sha" ]; then
  echo "ERROR: origin/main is not at $main_sha: the push of main did not take effect"
  exit 1
fi

echo "=== 5. Commit and push gh-pages ==="
if git -C "$DEPLOY_DIR" diff --cached --quiet; then
  echo "   the site is the same as the one published"
else
  git -C "$DEPLOY_DIR" commit -q -m "Publish the documentation of main ${main_sha:0:7}"
fi
git -C "$DEPLOY_DIR" push origin gh-pages
pages_sha=$(git -C "$DEPLOY_DIR" rev-parse HEAD)
if [ "$(git -C "$DEPLOY_DIR" ls-remote origin refs/heads/gh-pages | cut -f1)" != "$pages_sha" ]; then
  echo "ERROR: origin/gh-pages is not at $pages_sha: the push did not take effect"
  exit 1
fi
echo "Done: main ${main_sha:0:7} and gh-pages ${pages_sha:0:7} are on GitHub."
echo "GitHub Pages shows the site at $SITE_URL in about a minute; Read the Docs builds main itself."
