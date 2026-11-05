#!/usr/bin/env bash
# Publish a new version: bumps manifest.json + const.py, commits, tags, pushes and creates a GitHub release.
#
#   scripts/release.sh            -> next patch version (0.2.1 -> 0.2.2)
#   scripts/release.sh minor      -> 0.2.1 -> 0.3.0
#   scripts/release.sh major      -> 0.2.1 -> 1.0.0
#   scripts/release.sh 0.4.7      -> exactly that version
#   scripts/release.sh patch "What changed"   -> custom release title
set -euo pipefail
cd "$(dirname "$0")/.."

MANIFEST=custom_components/ovcirrus/manifest.json
CONST=custom_components/ovcirrus/const.py
BUMP="${1:-patch}"
TITLE="${2:-}"

if [[ -n "$(git status --porcelain)" ]]; then
  echo "Commit or stash your changes first." >&2
  exit 1
fi

CURRENT=$(python3 -c "import json;print(json.load(open('$MANIFEST'))['version'])")
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

if git rev-parse "v$NEW" >/dev/null 2>&1; then
  echo "Tag v$NEW already exists." >&2
  exit 1
fi

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
git push -q origin HEAD "v$NEW"
gh release create "v$NEW" --title "${TITLE:-v$NEW}" --generate-notes
echo "Released v$NEW ($CURRENT -> $NEW)"
