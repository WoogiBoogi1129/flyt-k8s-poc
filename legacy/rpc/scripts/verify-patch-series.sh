#!/usr/bin/env bash

set -Eeuo pipefail
root_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
repository="${FLYT_REPOSITORY:-https://github.com/WoogiBoogi1129/flyt_custom_for_k8s.git}"
commit="${FLYT_COMMIT:-596a86939bae125d127a9ed6f70d92c332f47644}"
expected="$(<"$root_dir/patches/expected-source-diff.sha256")"
work_dir="$(mktemp -d)"
trap 'rm -rf -- "$work_dir"' EXIT

git clone --quiet --no-checkout "$repository" "$work_dir/flyt"
git -C "$work_dir/flyt" checkout --quiet --detach "$commit"
while IFS= read -r patch_name; do
  [[ -n "$patch_name" && "$patch_name" != \#* ]] || continue
  patch -s -d "$work_dir/flyt" -p1 --forward --no-backup-if-mismatch \
    < "$root_dir/patches/$patch_name"
done < "$root_dir/patches/series"
git -C "$work_dir/flyt" diff --check
# New source files introduced by a patch are untracked until staged and would
# otherwise be omitted from `git diff`.  Hash the complete staged source delta.
git -C "$work_dir/flyt" add -A
git -C "$work_dir/flyt" diff --cached --check
actual="$(git -C "$work_dir/flyt" diff --cached --binary | sha256sum | awk '{print $1}')"
[[ "$actual" == "$expected" ]] || {
  printf 'expected=%s actual=%s\n' "$expected" "$actual" >&2
  exit 1
}
printf 'patch_series=PASS baseline=%s diff_sha256=%s\n' "$commit" "$actual"
