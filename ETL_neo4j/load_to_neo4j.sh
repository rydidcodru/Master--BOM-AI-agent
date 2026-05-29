#!/usr/bin/env bash
# Loader entrypoint used by docker-compose.deploy.yml.
# Applies constraints/indexes, then loads the pre-generated CSVs into Neo4j.
# Runs once after the neo4j service is healthy; the data load is skipped if
# the graph is already populated (so restarts stay fast and idempotent).
set -euo pipefail

BOLT="bolt://${NEO4J_HOST:-neo4j}:7687"
NUSER="${NEO4J_USER:-neo4j}"
NPASS="${NEO4J_PASSWORD:?NEO4J_PASSWORD must be set}"

run() { cypher-shell -a "$BOLT" -u "$NUSER" -p "$NPASS" "$@"; }

echo "[loader] applying constraints & indexes..."
run -f /cypher/constraints_indexes.cypher

NODES="$(run --format plain 'MATCH (n) RETURN count(n);' | tail -n1 | tr -d '[:space:]')"
if [[ "$NODES" =~ ^[0-9]+$ ]] && [ "$NODES" -gt 0 ]; then
  echo "[loader] graph already populated ($NODES nodes) — skipping data load."
else
  echo "[loader] seeding import dir and loading CSV data..."
  cp /csv/*.csv /import/
  chmod 644 /import/*.csv          # source files may be mode 700; neo4j (uid 7474) must read them
  run -f /load/load_all.cypher
  echo "[loader] data load finished."
fi

echo "[loader] done."
