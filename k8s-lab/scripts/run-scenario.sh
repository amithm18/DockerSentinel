#!/usr/bin/env bash
# Run one scenario end to end and save its evidence:
#   reset all -> break -> wait for the signal -> capture -> show key lines -> reset.
# Usage: run-scenario.sh <scenario>
set -uo pipefail
source "$(dirname "$0")/lib.sh"
[ $# -eq 1 ] || usage_scenarios
SC="$1"; DEPLOY="$(deployment_for "$SC")"; E="$LAB_DIR/evidence/$SC"
cd "$(dirname "$0")"

./reset.sh all >/dev/null
./break.sh "$SC"
./wait-for.sh "$SC" | tail -5 || say "signal not seen; capturing anyway"
./capture.sh "$SC" >/dev/null

say "container state (describe.txt)"
sed -n '/^    State:/,/^    Ready:/p' "$E/describe.txt"
say "last log lines of the crashed container (logs-previous.txt)"
tail -6 "$E/logs-previous.txt"
say "warning events for $DEPLOY (events.txt)"
grep Warning "$E/events.txt" | grep "pod/$DEPLOY" | tail -3 | cut -c1-220

./reset.sh "$SC" | tail -1
