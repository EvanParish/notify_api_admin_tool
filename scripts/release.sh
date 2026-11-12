#!/usr/bin/env bash
# Prepare a release: bump the version, generate the CHANGELOG.md section, and run the checks.
#
#   scripts/release.sh            # next version from commits since the last tag
#   scripts/release.sh 1.4.0      # explicit version
#
# It never commits, tags, or pushes. It prints those commands at the end, after you review
# CHANGELOG.md (edit the wording freely; it is the team-facing release notes). Pushing the
# tag runs .github/workflows/release.yml, which tests, publishes the image, and creates the
# GitHub Release.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

die() { echo "error: $*" >&2; exit 1; }
CONVENTIONAL='^(feat|fix|perf|refactor|revert|build|docs|style|test|ci|chore)(\([^)]+\))?!?: .+'
RELEASABLE='^(feat|fix|perf|revert|build|docs)(\([^)]+\))?!?: |^[a-z]+(\([^)]+\))?!: '

[ "$(git branch --show-current)" = "main" ] || die "check out main first"
[ -z "$(git status --porcelain)" ] || die "working tree is not clean; commit or stash first"
git fetch --quiet --tags origin main
[ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ] || die "main is not in sync with origin/main"

current="$(uv version --short)"
last_tag="$(git describe --tags --abbrev=0 --match 'v[0-9]*.[0-9]*.[0-9]*' 2>/dev/null || true)"

# First release: the version and its changelog section were written by hand.
if [ -z "$last_tag" ]; then
    grep -q "^## \[${current}\]" CHANGELOG.md || die "CHANGELOG.md has no section for ${current}"
    next="$current"
    echo "No release tag yet: releasing the hand-written ${next} as the first version."
else
    subjects="$(git log --format=%s "${last_tag}..HEAD")"
    [ -n "$subjects" ] || die "no commits since ${last_tag}"

    skipped="$(grep -Ev "$CONVENTIONAL" <<<"$subjects" || true)"
    if [ -n "$skipped" ]; then
        echo "warning: these commits are not Conventional Commits and are left out of the changelog:" >&2
        while IFS= read -r line; do echo "  - ${line}" >&2; done <<<"$skipped"
        echo "  Add anything the team needs to know to CHANGELOG.md by hand." >&2
    fi
    grep -Eq "$RELEASABLE" <<<"$subjects" \
        || die "nothing team-facing since ${last_tag} (only refactor/style/test/ci/chore commits)"

    next="${1:-$(uv run git cliff --bumped-version 2>/dev/null)}"
    next="${next#v}"
    [[ "$next" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "invalid version: ${next}"
    [ "$next" != "$current" ] || die "version ${next} is already in pyproject.toml"
    git rev-parse -q --verify "refs/tags/v${next}" >/dev/null && die "tag v${next} already exists"

    echo "Releasing ${current} -> ${next}"
    uv version --quiet "$next"

    # Insert the new section above the newest existing one, keeping the file's header.
    section="$(uv run git cliff --unreleased --tag "v${next}" --strip header 2>/dev/null)"
    SECTION="$section" uv run python - <<'PY'
import os
from pathlib import Path

path = Path("CHANGELOG.md")
lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
at = next((i for i, line in enumerate(lines) if line.startswith("## ")), len(lines))
lines.insert(at, os.environ["SECTION"].strip() + "\n\n")
path.write_text("".join(lines), encoding="utf-8")
PY
fi

echo "Running checks..."
uv run ruff check .
uv run ruff format --check .
# Same dummy key as CI: the suite needs one set, and should never see the real one from .env.
MASTER_KEY=release-check-not-a-secret uv run python -m pytest tests/ -q --cov=app --cov-report=term-missing:skip-covered --cov-fail-under=100

echo
if [ -z "$last_tag" ]; then
    echo "Ready to release v${next}. Tag the current commit and push the tag:"
    echo
else
    echo "Ready to release v${next}. Review CHANGELOG.md (and edit it if needed), then:"
    echo
    echo "  git add pyproject.toml uv.lock CHANGELOG.md"
    echo "  git commit -m \"chore(release): v${next}\""
fi
echo "  git tag -a v${next} -m \"v${next}\""
echo "  git push --atomic origin main v${next}"
echo
echo "Follow the release at https://github.com/EvanParish/notify_api_admin_tool/actions"
