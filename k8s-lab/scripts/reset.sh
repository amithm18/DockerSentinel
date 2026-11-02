#!/usr/bin/env bash
# Undo one failure, or all of them. Usage: reset.sh <scenario|all>
# Every reset is safe to run even when that scenario is not currently broken.
set -euo pipefail
source "$(dirname "$0")/lib.sh"
[ $# -eq 1 ] || { echo "usage: reset.sh <scenario|all>" >&2; usage_scenarios; }

wait_rollout() {
  "${KCTL[@]}" rollout status "deploy/$1" --timeout=180s
}

reset_memory_hog() {
  say "memory-hog: back to MODE=steady WORKING_SET_MB=30"
  "${KCTL[@]}" set env deploy/memory-hog MODE=steady WORKING_SET_MB=30
  wait_rollout memory-hog
}

reset_bad_release() {
  local image
  image=$("${KCTL[@]}" get deploy/bad-release -o jsonpath='{.spec.template.spec.containers[0].image}')
  if [ "$image" != "lab/bad-release:v1" ]; then
    # rollout undo = go back to the previous revision's pod template.
    say "bad-release: running $image, rolling back with 'rollout undo'"
    "${KCTL[@]}" rollout undo deploy/bad-release
  else
    say "bad-release: already on v1, nothing to undo"
  fi
  wait_rollout bad-release
}

reset_missing_secret() {
  say "db-app: re-applying 20-db-app.yaml (recreates Secret db-creds)"
  "${KCTL[@]}" apply -f "$LAB_DIR/manifests/20-db-app.yaml"
  wait_rollout db-app
}

reset_db_down() {
  say "postgres: scaling back to 1 and waiting for Ready"
  "${KCTL[@]}" scale deploy/postgres --replicas=1
  wait_rollout postgres
  # db-app may be deep in CrashLoopBackOff (long delay); restart for a fresh start.
  say "db-app: rollout restart"
  "${KCTL[@]}" rollout restart deploy/db-app
  wait_rollout db-app
}

case "$1" in
  oom-limit|oom-leak) reset_memory_hog ;;
  bad-release|bad-image) reset_bad_release ;;
  missing-secret) reset_missing_secret ;;
  db-down) reset_db_down ;;
  all)
    reset_memory_hog
    reset_bad_release
    "${KCTL[@]}" apply -f "$LAB_DIR/manifests/20-db-app.yaml" >/dev/null
    reset_db_down
    say "all lab Deployments healthy:"
    "${KCTL[@]}" get deploy
    ;;
  *) usage_scenarios ;;
esac
