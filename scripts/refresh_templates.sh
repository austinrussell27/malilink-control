#!/usr/bin/env bash
# Re-copy the example configs + schema from a dvmhost checkout into dvm_templates/.
set -euo pipefail
REF=${1:-/workspace/ref/dvmhost}
DST="$(cd "$(dirname "$0")/.." && pwd)/dvm_templates"
cp "$REF"/configs/{config.example.yml,fne-config.example.yml,talkgroup_rules.example.yml,iden_table.example.dat,rid_acl.example.dat,adj_site_map.example.yml} "$DST"/
cp "$REF"/configs/schema/talkgroup_rules.yaml_schema.json "$DST"/
cp "$REF"/linux/dvmhost.service "$DST"/
echo "Copied from https://github.com/DVMProject/dvmhost @ $(git -C "$REF" rev-parse HEAD) ($(git -C "$REF" log -1 --format=%cd))." > "$DST/SOURCE.txt"
echo "templates refreshed"
