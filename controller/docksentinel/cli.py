"""Command line interface.

  docksentinel watch -n demo            watch a namespace and self-heal
  docksentinel watch -n demo --dry-run  diagnose only, never change anything
  docksentinel incidents                show incident history
  docksentinel diagnose pod.json        diagnose a saved `kubectl get pod -o json`
"""
from __future__ import annotations

import argparse
import json
import sys

from .alerts import Alerter, render
from .collector import build_incident
from .diagnosis import diagnose, load_rules
from .engine import Engine, Settings
from .models import Outcome
from .store import Store


def cmd_watch(args: argparse.Namespace) -> int:
    from .kube import Kube  # imported here so offline commands work without a cluster

    kube = Kube(in_cluster=args.in_cluster)
    engine = Engine(kube, Store(args.db), load_rules(args.rules), Alerter(), Settings(
        dry_run=args.dry_run, all_deployments=args.all_deployments,
        verify_seconds=args.verify_seconds))
    scope = args.namespace or "all namespaces"
    engine.alerter.info(f"watching {scope} (dry-run={args.dry_run}); press Ctrl+C to stop")
    try:
        while True:
            engine.refresh_good_revisions(args.namespace)
            for pod in kube.watch_pods(args.namespace, timeout_seconds=10):
                engine.handle_pod(pod)
                engine.refresh_good_revisions(args.namespace)
    except KeyboardInterrupt:
        engine.alerter.info("stopped")
        return 0


def cmd_incidents(args: argparse.Namespace) -> int:
    rows = Store(args.db).recent(args.limit)
    if not rows:
        print("No incidents recorded yet.")
        return 0
    print(f"{'#':>3}  {'time (UTC)':19}  {'deployment':22}  {'cause':14}  {'action':12}  "
          f"{'status':10}  {'recovery':>8}")
    for r in rows:
        rec = f"{r['recovery_seconds']:.0f}s" if r["recovery_seconds"] else "-"
        print(f"{r['id']:>3}  {r['detected_at'][:19]:19}  {r['namespace'] + '/' + r['deployment']:22.22}  "
              f"{r['rule']:14.14}  {r['action']:12}  {r['status']:10}  {rec:>8}")
    return 0


def cmd_diagnose(args: argparse.Namespace) -> int:
    with open(args.pod_json, encoding="utf-8") as fh:
        pod = json.load(fh)
    pods = pod.get("items", [pod])
    logs = open(args.logs, encoding="utf-8").read() if args.logs else ""
    rules, found = load_rules(args.rules), False
    for p in pods:
        inc = build_incident(p, deployment=p["metadata"].get("labels", {}).get("app"),
                             revision=None, logs=logs)
        if inc:
            found = True
            dx = diagnose(inc, rules, new_revision=args.new_revision)
            print(render(inc, dx, Outcome(dx.action, "dry_run", "offline diagnosis")))
    if not found:
        print("No failing containers found in that file.")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="docksentinel", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default="docksentinel.db", help="SQLite file (default: %(default)s)")
    p.add_argument("--rules", default=None, help="custom rules.yaml")
    sub = p.add_subparsers(dest="cmd", required=True)

    w = sub.add_parser("watch", help="watch pods, diagnose and self-heal")
    w.add_argument("-n", "--namespace", default=None, help="namespace (default: all)")
    w.add_argument("--dry-run", action="store_true", help="diagnose only, change nothing")
    w.add_argument("--all-deployments", action="store_true",
                   help="manage every Deployment, not only those labelled "
                        "docksentinel.io/enabled=true")
    w.add_argument("--verify-seconds", type=int, default=30,
                   help="how long a fix must stay healthy (default: %(default)s)")
    w.add_argument("--in-cluster", action="store_true", help="use in-cluster credentials")
    w.set_defaults(func=cmd_watch)

    i = sub.add_parser("incidents", help="show recorded incidents")
    i.add_argument("--limit", type=int, default=20)
    i.set_defaults(func=cmd_incidents)

    d = sub.add_parser("diagnose", help="diagnose a saved `kubectl get pod -o json` file")
    d.add_argument("pod_json")
    d.add_argument("--logs", help="text file with the pod's logs")
    d.add_argument("--new-revision", action="store_true",
                   help="treat the pod as part of a new release")
    d.set_defaults(func=cmd_diagnose)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
