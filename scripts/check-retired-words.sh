#!/usr/bin/env bash
# The retired-words guard for this checkout (Data Doctrine R16; ONE-HOME DD-063/DD-064/DD-067).
# The scanner lives in aidream (scripts/check_retired_words.py) and the word list in common-docs
# (systems/platform/vocabulary/retired-words.json); both are expected beside this checkout.
# Usage: scripts/check-retired-words.sh [--list | --write-baseline | --self-test]
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AIDREAM="${AIDREAM_ROOT:-$HERE/../aidream}"
if [ ! -f "$AIDREAM/scripts/check_retired_words.py" ]; then
  echo "UNMEASURED: aidream checkout not found at $AIDREAM (set AIDREAM_ROOT)" >&2
  exit 2
fi
exec python3 "$AIDREAM/scripts/check_retired_words.py" --root "$HERE" --baseline "$HERE/scripts/retired-words-baseline.json" "$@"
