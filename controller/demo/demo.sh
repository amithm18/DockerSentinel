#!/usr/bin/env bash
# DockSentinel demo helper.
#   ./demo/demo.sh setup                 create cluster (if needed) + demo apps
#   ./demo/demo.sh status                show demo pods
#   ./demo/demo.sh break <scenario>      oom | leak | bad-image | crash-release | missing-secret
#   ./demo/demo.sh reset                 put every demo app back to healthy
#   ./demo/demo.sh teardown              delete the demo namespace
set -euo pipefail
NS=demo
CLUSTER=${CLUSTER:-docksentinel}
HERE="$(cd "$(dirname "$0")" && pwd)"

say() { printf '\n\033[1m>> %s\033[0m\n' "$*"; }

setup() {
  if ! kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
    say "Creating kind cluster '$CLUSTER' (takes ~1 minute)"
    kind create cluster --name "$CLUSTER"
  fi
  kubectl config use-context "kind-$CLUSTER" >/dev/null
  say "Deploying demo apps"
  kubectl apply -f "$HERE/apps.yaml"
  kubectl -n $NS create secret generic api-secret --from-literal=password=demo123 \
    --dry-run=client -o yaml | kubectl apply -f -
  say "Waiting for apps to become ready (first run downloads images)"
  kubectl -n $NS rollout status deploy/web --timeout=180s
  kubectl -n $NS rollout status deploy/api --timeout=180s
  kubectl -n $NS rollout status deploy/memory-app --timeout=180s
  status
}

status() { kubectl -n $NS get deploy,pods -o wide; }

break_it() {
  case "${1:-}" in
    oom)
      say "memory-app: lowering memory limit to 64Mi (app needs ~90 MB) -> expect OOMKilled"
      kubectl -n $NS set resources deploy/memory-app -c app --limits=memory=64Mi ;;
    leak)
      say "memory-app: app now uses 600 MB (simulated leak) -> raising memory will NOT help"
      kubectl -n $NS set resources deploy/memory-app -c app --limits=memory=64Mi
      kubectl -n $NS set env deploy/memory-app -c app MB=600 ;;
    bad-image)
      say "web: deploying image tag that does not exist -> expect ImagePullBackOff"
      kubectl -n $NS set image deploy/web web=nginx:9.99-does-not-exist
      kubectl -n $NS annotate deploy/web kubernetes.io/change-cause="v2 bad image tag" --overwrite ;;
    crash-release)
      say "web: deploying a release whose container exits immediately -> expect CrashLoopBackOff"
      kubectl -n $NS set image deploy/web web=busybox:1.36
      kubectl -n $NS annotate deploy/web kubernetes.io/change-cause="v2 crashing release" --overwrite ;;
    missing-secret)
      say "api: deleting Secret api-secret and restarting -> expect CreateContainerConfigError"
      kubectl -n $NS delete secret api-secret --ignore-not-found
      kubectl -n $NS rollout restart deploy/api ;;
    *) echo "scenarios: oom | leak | bad-image | crash-release | missing-secret"; exit 1 ;;
  esac
  echo "Now watch the DockSentinel terminal."
}

reset() {
  say "Resetting demo apps to healthy"
  kubectl apply -f "$HERE/apps.yaml"
  kubectl -n $NS create secret generic api-secret --from-literal=password=demo123 \
    --dry-run=client -o yaml | kubectl apply -f -
  kubectl -n $NS rollout status deploy/web --timeout=180s || true
  kubectl -n $NS rollout status deploy/memory-app --timeout=180s || true
  kubectl -n $NS rollout restart deploy/api
  kubectl -n $NS rollout status deploy/api --timeout=180s || true
}

case "${1:-}" in
  setup) setup ;;
  status) status ;;
  break) break_it "${2:-}" ;;
  reset) reset ;;
  teardown) kubectl delete namespace $NS --ignore-not-found ;;
  *) sed -n '2,7p' "$0"; exit 1 ;;
esac
