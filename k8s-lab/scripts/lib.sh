# Shared helpers for the lab scripts (sourced, not run directly).
# Always target the lab cluster and namespace, whatever kubectl's current context is.
KCTL=(kubectl --context kind-docksentinel -n lab)
LAB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCENARIOS="oom-limit oom-leak bad-release bad-image missing-secret db-down"

say() { echo -e "\033[1;34m==>\033[0m $*"; }

# Map a scenario to the Deployment it breaks.
deployment_for() {
  case "$1" in
    oom-limit|oom-leak) echo memory-hog ;;
    bad-release|bad-image) echo bad-release ;;
    missing-secret|db-down) echo db-app ;;
    *) echo "unknown scenario: $1" >&2; return 1 ;;
  esac
}

usage_scenarios() {
  echo "scenarios: $SCENARIOS" >&2
  exit 1
}
