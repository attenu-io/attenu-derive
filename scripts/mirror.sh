#!/usr/bin/env bash
# Mirror the local corpus/mirror/runs to the Tigris bucket (ADR-04). Requires `tigris login`.
#   scripts/mirror.sh            # push local -> t3://attenu-corpus/
#   scripts/mirror.sh pull       # t3://attenu-corpus/ -> local
set -euo pipefail
BUCKET="${ATTENU_BUCKET:-attenu-corpus}"
cd "$(dirname "$0")/.."
tigris whoami >/dev/null 2>&1 || { echo "not logged in: run  tigris login"; exit 2; }
tigris buckets list 2>/dev/null | grep -q "^$BUCKET\b" || tigris buckets create "$BUCKET"
if [ "${1:-push}" = "pull" ]; then
  tigris cp -r "t3://$BUCKET/data" ./data
else
  # data/corpus = shippable rows (task text hashed); data/mirror = local-only rows (task text kept)
  # — BOTH stay private in this bucket; the bucket is single-tenant, private by default.
  tigris cp -r ./data "t3://$BUCKET/data"
fi
tigris ls "t3://$BUCKET/data" | head -20
