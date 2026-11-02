#!/usr/bin/env bash
# Save the Kubernetes evidence for a broken scenario into evidence/<scenario>/.
# These files are test fixtures for the Day 2 collector and Day 3 diagnosis rules.
# Usage: capture.sh <scenario>
set -euo pipefail
source "$(dirname "$0")/lib.sh"
[ $# -eq 1 ] || usage_scenarios

SCENARIO="$1"
DEPLOY="$(deployment_for "$SCENARIO")"
OUT="$LAB_DIR/evidence/$SCENARIO"
mkdir -p "$OUT"

# Pick the failing pod: the newest, not-terminating pod of the Deployment that is
# not Ready; fall back to the newest pod if all are Ready (e.g. between OOM kills).
pick_pod() {
  local lines notready newest
  # Lines are name|deletionTimestamp|ready; drop pods that are being deleted.
  lines=$("${KCTL[@]}" get pods -l "app=$DEPLOY" --sort-by=.metadata.creationTimestamp \
    -o jsonpath='{range .items[*]}{.metadata.name}{"|"}{.metadata.deletionTimestamp}{"|"}{.status.containerStatuses[0].ready}{"\n"}{end}' \
    | awk -F'|' '$2 == ""')
  notready=$(echo "$lines" | awk -F'|' '$3 != "true" {print $1}' | tail -1)
  newest=$(echo "$lines" | awk -F'|' '{print $1}' | tail -1)
  echo "${notready:-$newest}"
}

POD="$(pick_pod)"
say "capturing scenario '$SCENARIO' (deployment $DEPLOY, pod $POD) into $OUT"

"${KCTL[@]}" get pods -o json > "$OUT/pods.json"
"${KCTL[@]}" describe pod "$POD" > "$OUT/describe.txt"
# --previous = logs of the last crashed container; none exists for e.g. image errors.
"${KCTL[@]}" logs "$POD" --previous > "$OUT/logs-previous.txt" 2>&1 || true
"${KCTL[@]}" logs "$POD" > "$OUT/logs-current.txt" 2>&1 || true
"${KCTL[@]}" get events --sort-by=.lastTimestamp > "$OUT/events.txt"
"${KCTL[@]}" get deploy "$DEPLOY" -o json > "$OUT/deployment.json"
"${KCTL[@]}" get replicasets -l "app=$DEPLOY" -o json > "$OUT/replicasets.json"
"${KCTL[@]}" rollout history "deploy/$DEPLOY" > "$OUT/rollout-history.txt"
# For the dependency scenario also keep the database Deployment's state.
if [ "$SCENARIO" = "db-down" ]; then
  "${KCTL[@]}" get deploy postgres -o json > "$OUT/deployment-postgres.json"
fi
{
  echo "scenario: $SCENARIO"
  echo "deployment: $DEPLOY"
  echo "pod: $POD"
  echo "captured_at: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
} > "$OUT/meta.txt"
"$(dirname "$0")/status.sh" > "$OUT/status.txt"

ls -1 "$OUT"
