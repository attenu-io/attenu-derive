#!/usr/bin/env bash
# Mirror the local corpus/mirror/runs to the Tigris bucket (ADR-04). Requires `tigris login`.
#   scripts/mirror.sh            # push local -> t3://attenu-corpus/
#   scripts/mirror.sh pull       # t3://attenu-corpus/ -> local
set -euo pipefail
BUCKET="${ATTENU_BUCKET:-rafa-attenu-corpus}"
cd "$(dirname "$0")/.."
tigris whoami >/dev/null 2>&1 || { echo "not logged in: run  tigris login"; exit 2; }
tigris buckets list --json 2>/dev/null | grep -q "\"$BUCKET\"" || tigris buckets create "$BUCKET" -y
if [ "${1:-push}" = "pull" ]; then
  tigris cp -r "t3://$BUCKET/data" ./
else
  # data/corpus = shippable rows (task text hashed); data/mirror = local-only rows (task text kept)
  # — BOTH stay private in this bucket; the bucket is single-tenant, private by default.
  # data/raw (public dataset downloads, re-fetchable via the normalizers' --download) is NOT mirrored.
  for d in corpus mirror runs reports; do [ -d "./data/$d" ] && tigris cp -r "./data/$d" "t3://$BUCKET/data/"; done
fi
tigris ls "t3://$BUCKET/data" | head -20
