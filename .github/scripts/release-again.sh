#!/usr/bin/env bash
# Start the workflow `release` again for a tag whose release never came out.
#
# Called by auto-release.yml when the tag v$VERSION exists already. The release of a tag
# is complete when its GitHub release carries the plugin archive and, while the
# repository variable PYPI_UPLOAD is true, the wheel (the job `pypi` attaches it after
# the upload). If a file is missing and no run of `release` for that tag is under way,
# the workflow is started on the tag, as auto-release does for a new tag. A run counts
# as under way while it is in progress, or for 15 minutes after it was created: a run
# that has not started by then has been lost (2026-11-16, v1.4.0).
#
# DRY_RUN=1 prints the decision and starts nothing.
set -euo pipefail

tag="v${VERSION:?the version is not set}"
assets=$(gh release view "$tag" --json assets --jq '.assets[].name' 2>/dev/null || true)
missing=""
grep -qx "axqua-qgis-${VERSION}.zip" <<<"$assets" || missing="the plugin archive"
if [ "${PYPI_UPLOAD:-}" = "true" ] && ! grep -qx "axqua-${VERSION}-py3-none-any.whl" <<<"$assets"; then
  missing="${missing:+$missing and }the wheel"
fi
if [ -z "$missing" ]; then
  echo "$tag is released: nothing to do"
  exit 0
fi

under_way=$(gh run list --workflow plugin-release.yml --branch "$tag" --limit 20 \
  --json status,createdAt \
  --jq '[.[] | select(.status == "in_progress" or (.status != "completed" and (now - (.createdAt | fromdateiso8601)) < 900))] | length')
if [ "$under_way" != "0" ]; then
  echo "$tag lacks $missing, and a run of release for it is under way: nothing to do"
  exit 0
fi

echo "::warning::$tag exists, but its release lacks $missing: starting release on the tag again"
if [ "${DRY_RUN:-}" = "1" ]; then
  echo "dry run: gh workflow run plugin-release.yml --ref $tag"
  exit 0
fi
gh workflow run plugin-release.yml --ref "$tag"
