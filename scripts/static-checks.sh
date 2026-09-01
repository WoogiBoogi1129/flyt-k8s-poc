#!/usr/bin/env bash

set -Eeuo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

need git
need rg
need python3

find "$ROOT_DIR/scripts" "$ROOT_DIR/pytorch" -type f -name '*.sh' \
  -print0 | xargs -0 -n 1 bash -n
python3 - "$ROOT_DIR" <<'PY'
import ast
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
for path in root.rglob("*.py"):
    if any(part in {".git", ".cache", ".local", "artifacts", "evidence", "results"}
           for part in path.parts):
        continue
    ast.parse(path.read_text(), filename=str(path))
PY

large_files() {
  find "$ROOT_DIR" \
    \( -path '*/.git' -o -path '*/.cache' -o -path '*/.local' \
       -o -path '*/artifacts' -o -path '*/evidence' -o -path '*/results' \
       -o -path '*/deploy/rendered' \) -prune -o \
    -type f -size +20M -print
}
if large_files | grep -q .; then
  large_files >&2
  die "files larger than 20 MiB must not be committed"
fi

prohibited='(/home/ubuntu/|GPU-c654d43e|taeuk-vm|10\.0\.0\.(27|185|222))'
if rg -n "$prohibited" "$ROOT_DIR" \
  --glob '!scripts/static-checks.sh' --glob '!.git/**' \
  --glob '!deploy/rendered/**' --glob '!evidence/**' --glob '!results/**'; then
  die "environment-specific identifiers remain outside the archived report"
fi

# Patch payloads intentionally preserve whitespace from the exact experiment
# source. Check repository-owned files while validating patch semantics via the
# reconstruction checksum in verify-patch-series.sh.
git -C "$ROOT_DIR" diff --check -- . ':(exclude)patches/*.patch'
git -C "$ROOT_DIR" diff --cached --check -- . ':(exclude)patches/*.patch'
printf '%s\n' 'shell=PASS python_ast=PASS size_gate=PASS public_identifiers=PASS diff=PASS'
