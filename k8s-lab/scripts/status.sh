#!/usr/bin/env bash
# One line per lab pod: the fields DockSentinel will look at to diagnose failures.
# DELETING is set for pods that are shutting down (they can still show Running).
set -euo pipefail
source "$(dirname "$0")/lib.sh"

"${KCTL[@]}" get pods -o custom-columns='POD:.metadata.name,PHASE:.status.phase,READY:.status.containerStatuses[0].ready,RESTARTS:.status.containerStatuses[0].restartCount,WAITING:.status.containerStatuses[0].state.waiting.reason,LAST_TERMINATED:.status.containerStatuses[0].lastState.terminated.reason,EXIT:.status.containerStatuses[0].lastState.terminated.exitCode,IMAGE:.spec.containers[0].image,DELETING:.metadata.deletionTimestamp'
