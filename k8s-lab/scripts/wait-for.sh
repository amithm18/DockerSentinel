#!/usr/bin/env bash
# Poll every 10 s (max 3 min) until a scenario's expected Kubernetes signal appears.
# Usage: wait-for.sh <scenario>     Exit code 0 = signal seen, 1 = timed out.
set -euo pipefail
source "$(dirname "$0")/lib.sh"
[ $# -eq 1 ] || usage_scenarios

SCENARIO="$1"
DEPLOY="$(deployment_for "$SCENARIO")"

# One line per pod: image|waitingReason|lastTerminatedReason|exitCode|restarts
pod_lines() {
  "${KCTL[@]}" get pods -l "app=$DEPLOY" -o jsonpath='{range .items[*]}{.spec.containers[0].image}{"|"}{.status.containerStatuses[0].state.waiting.reason}{"|"}{.status.containerStatuses[0].lastState.terminated.reason}{"|"}{.status.containerStatuses[0].lastState.terminated.exitCode}{"|"}{.status.containerStatuses[0].restartCount}{"\n"}{end}'
}

signal_seen() {
  local lines; lines="$(pod_lines)"
  case "$SCENARIO" in
    oom-limit)      echo "$lines" | awk -F'|' '$2=="CrashLoopBackOff" && $3=="OOMKilled" && $4==137' | grep -q . ;;
    oom-leak)       echo "$lines" | awk -F'|' '$3=="OOMKilled" && $5>=2' | grep -q . ;;
    bad-release)    echo "$lines" | awk -F'|' '$1 ~ /:v2$/ && $2=="CrashLoopBackOff"' | grep -q . ;;
    bad-image)      echo "$lines" | awk -F'|' '$2=="ImagePullBackOff"' | grep -q . ;;
    missing-secret) echo "$lines" | awk -F'|' '$2=="CreateContainerConfigError"' | grep -q . ;;
    db-down)        echo "$lines" | awk -F'|' '$2=="CrashLoopBackOff" && $4==1' | grep -q . ;;
  esac
}

start=$(date +%s)
for _ in $(seq 1 18); do
  "$(dirname "$0")/status.sh" | grep -E "^(POD|$DEPLOY)"
  if signal_seen; then
    say "expected signal for '$SCENARIO' seen after $(( $(date +%s) - start )) s"
    exit 0
  fi
  echo "   ...not yet, checking again in 10 s"
  sleep 10
done
say "timed out after 3 minutes waiting for '$SCENARIO'"
exit 1
