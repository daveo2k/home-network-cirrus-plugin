#!/usr/bin/env bash
# Publish a new version: bumps manifest.json + const.py, commits, tags, pushes and creates a GitHub release.
#
#   scripts/release.sh            -> next patch version (0.2.1 -> 0.2.2)
#   scripts/release.sh minor      -> 0.2.1 -> 0.3.0
#   scripts/release.sh major      -> 0.2.1 -> 1.0.0
#   scripts/release.sh 0.4.7      -> exactly that version
#   scripts/release.sh patch "What changed"   -> custom release title
#
# Resumable: if the current version is already tagged but has no GitHub release yet
# (e.g. a previous run failed at the last step), it finishes that release instead of bumping again.
set -euo pipefail
cd "$(dirname "$0")/.."

MANIFEST=custom_components/ovcirrus/manifest.json
CONST=custom_components/ovcirrus/const.py
BUMP="${1:-patch}"
TITLE="${2:-}"

step() { printf '\n==> %s\n' "$*"; }
fail() { printf '\nFAILED: %s\n' "$*" >&2; exit 1; }

command -v gh >/dev/null || fail "GitHub CLI (gh) is not installed: brew install gh"
gh auth status >/dev/null 2>&1 || fail "gh is not logged in: gh auth login"

if [[ -n "$(git status --porcelain)" ]]; then
  git status --short
  fail "There are uncommitted changes. Commit or stash them first."
fi

CURRENT=$(python3 -c "import json;print(json.load(open('$MANIFEST'))['version'])")

publish() {
  local version="$1"
  step "Pushing main and tag v$version to GitHub"
  git push origin HEAD:main "v$version" || fail "git push failed (see the error above). Fix it, then run scripts/release.sh again."
  step "Creating GitHub release v$version"
  if gh release view "v$version" >/dev/null 2>&1; then
    echo "Release v$version already exists."
  else
    gh release create "v$version" --verify-tag --latest --title "${TITLE:-v$version}" --generate-notes \
      || fail "gh release create failed (see the error above). Run scripts/release.sh again to retry without bumping the version."
  fi
  step "Done: v$version is published"
  gh release view "v$version" --json url --jq .url || true
}

# Resume an unfinished release of the current version.
if git rev-parse -q --verify "refs/tags/v$CURRENT" >/dev/null; then
  if ! gh release view "v$CURRENT" >/dev/null 2>&1; then
    echo "v$CURRENT is tagged but has no GitHub release yet: finishing it (no version bump)."
    publish "$CURRENT"
    exit 0
  fi
fi

NEW=$(python3 - "$CURRENT" "$BUMP" <<'PY'
import re, sys
cur, bump = sys.argv[1], sys.argv[2]
if re.fullmatch(r"\d+\.\d+\.\d+", bump):
    print(bump); sys.exit()
major, minor, patch = (int(x) for x in cur.split("."))
if bump == "major":
    major, minor, patch = major + 1, 0, 0
elif bump == "minor":
    minor, patch = minor + 1, 0
elif bump == "patch":
    patch += 1
else:
    sys.exit(f"Unknown bump '{bump}': use patch, minor, major or X.Y.Z")
print(f"{major}.{minor}.{patch}")
PY
)

git rev-parse -q --verify "refs/tags/v$NEW" >/dev/null && fail "Tag v$NEW already exists."

step "Bumping version $CURRENT -> $NEW"
python3 - "$MANIFEST" "$CONST" "$NEW" <<'PY'
import json, re, sys
manifest, const, new = sys.argv[1:]
data = json.load(open(manifest))
data["version"] = new
with open(manifest, "w") as fh:
    json.dump(data, fh, indent=2)
    fh.write("\n")
text = open(const).read()
text, n = re.subn(r'^VERSION = "[^"]*"', f'VERSION = "{new}"', text, flags=re.M)
assert n == 1, "VERSION not found in const.py"
open(const, "w").write(text)
PY
git add "$MANIFEST" "$CONST"
git commit -q -m "Release v$NEW"
git tag -a "v$NEW" -m "v$NEW"

publish "$NEW"
