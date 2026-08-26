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
    ast.parse(path.read_text(), filename=str(path))
PY

if find "$ROOT_DIR" -type f -size +20M -not -path '*/.git/*' | grep -q .; then
  find "$ROOT_DIR" -type f -size +20M -not -path '*/.git/*' -print >&2
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
