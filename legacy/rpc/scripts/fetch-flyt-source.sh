#!/usr/bin/env bash

set -Eeuo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

need git
need patch
need sha256sum
need_config FLYT_REPOSITORY
need_config FLYT_COMMIT
source_dir="${FLYT_SOURCE_DIR:-$ROOT_DIR/.cache/flyt-source}"
marker="${source_dir}.patch-series.sha256"
series_sha="$(sha256sum "$ROOT_DIR/patches/series" "$ROOT_DIR"/patches/*.patch | sha256sum | awk '{print $1}')"
mkdir -p "$(dirname "$source_dir")"
if [[ ! -d "$source_dir/.git" ]]; then
  git clone --filter=blob:none --no-checkout "$FLYT_REPOSITORY" "$source_dir"
fi
if [[ -f "$marker" && "$(<"$marker")" == "$series_sha" && \
      "$(git -C "$source_dir" rev-parse HEAD)" == "$FLYT_COMMIT" ]]; then
  printf 'source_dir=%s baseline=%s patches=already-applied\n' \
    "$source_dir" "$FLYT_COMMIT"
  exit 0
fi
[[ -z "$(git -C "$source_dir" status --porcelain)" ]] || \
  die "source cache has untracked changes and no matching marker: $source_dir"
git -C "$source_dir" fetch --depth=1 origin "$FLYT_COMMIT"
git -C "$source_dir" checkout --detach "$FLYT_COMMIT"
while IFS= read -r patch_name; do
  [[ -n "$patch_name" && "$patch_name" != \#* ]] || continue
  patch="$ROOT_DIR/patches/$patch_name"
  [[ -f "$patch" ]] || die "series references missing patch: $patch_name"
  patch -d "$source_dir" -p1 --forward --no-backup-if-mismatch < "$patch"
done < "$ROOT_DIR/patches/series"
git -C "$source_dir" diff --check
printf '%s\n' "$series_sha" > "$marker"
printf 'source_dir=%s baseline=%s\n' "$source_dir" "$FLYT_COMMIT"
