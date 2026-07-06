#!/usr/bin/env bash
set -euo pipefail

VERSION_FILE="VERSION"

usage() {
	echo "Usage: $0 \"commit message\""
}

fail() {
	echo "Error: $*" >&2
	exit 1
}

if [[ $# -eq 0 ]]; then
	usage >&2
	exit 2
fi

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
	fail "not inside a git repository"
fi

if [[ ! -f "$VERSION_FILE" ]]; then
	fail "$VERSION_FILE does not exist"
fi

current_version="$(<"$VERSION_FILE")"

if [[ ! "$current_version" =~ ^([0-9]+)\.([0-9]+)\.([0-9]+)$ ]]; then
	fail "$VERSION_FILE must contain a semantic version like 0.1.0"
fi

major="${BASH_REMATCH[1]}"
minor="${BASH_REMATCH[2]}"
patch="${BASH_REMATCH[3]}"
next_version="${major}.${minor}.$((patch + 1))"

printf "%s\n" "$next_version" > "$VERSION_FILE"

echo "Version: $current_version -> $next_version"

echo "Running quality checks..."
make quality

echo "Running frontend type check & build..."
make test-frontend

echo "Checking backend..."
if ! curl -sf http://127.0.0.1:8020/api/version >/dev/null 2>&1; then
	fail "Backend is not running on port 8020. Start it with 'make backend' before committing."
fi

git add -A

if git diff --cached --quiet; then
	fail "no changes to commit"
fi

git commit -m "$*"
echo "Committed version $next_version"

branch="$(git rev-parse --abbrev-ref HEAD)"
echo "Pushing to origin/${branch}..."
git push -u origin "$branch"
echo "Pushed version $next_version to origin/${branch}"
