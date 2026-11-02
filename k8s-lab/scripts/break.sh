#!/usr/bin/env bash
# Inject one failure into the lab. Usage: break.sh <scenario>
set -euo pipefail
source "$(dirname "$0")/lib.sh"
[ $# -eq 1 ] || usage_scenarios

case "$1" in
  oom-limit)
    say "oom-limit: memory-hog will try to hold 160 MB under a 128Mi limit"
    say "expect: lastState.terminated.reason=OOMKilled, exitCode=137, then CrashLoopBackOff"
    "${KCTL[@]}" set env deploy/memory-hog WORKING_SET_MB=160
    ;;
  oom-leak)
    say "oom-leak: memory-hog starts healthy, then leaks 10 MB/s after 20 s"
    say "expect: Running/Ready, then OOMKilled roughly every 30 s, restart count climbing"
    "${KCTL[@]}" set env deploy/memory-hog MODE=leak
    ;;
  bad-release)
    say "bad-release: rolling out lab/bad-release:v2 (routes.js missing from the image)"
    say "expect: new pod CrashLoopBackOff, logs 'Cannot find module ./routes'; old v1 pods keep serving"
    "${KCTL[@]}" set image deploy/bad-release app=lab/bad-release:v2
    # Annotate after the image change so the note lands on the new revision.
    "${KCTL[@]}" annotate deploy/bad-release kubernetes.io/change-cause="v2 release" --overwrite
    ;;
  bad-image)
    say "bad-image: rolling out lab/bad-release:v3, which does not exist anywhere"
    say "expect: new pod ErrImagePull -> ImagePullBackOff; old v1 pods keep serving"
    "${KCTL[@]}" set image deploy/bad-release app=lab/bad-release:v3
    "${KCTL[@]}" annotate deploy/bad-release kubernetes.io/change-cause="v3 release (bad tag)" --overwrite
    ;;
  missing-secret)
    say "missing-secret: deleting Secret db-creds, then restarting db-app"
    say "expect: new pod CreateContainerConfigError, event 'secret \"db-creds\" not found'"
    "${KCTL[@]}" delete secret db-creds
    "${KCTL[@]}" rollout restart deploy/db-app
    ;;
  db-down)
    say "db-down: scaling postgres to 0 replicas"
    say "expect: db-app logs ECONNREFUSED, exits with code 1 after 3 checks, CrashLoopBackOff"
    "${KCTL[@]}" scale deploy/postgres --replicas=0
    ;;
  *) usage_scenarios ;;
esac
