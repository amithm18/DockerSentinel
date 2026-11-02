# DockSentinel – Kubernetes Failure Lab (Day 1)

A local Kubernetes cluster with small apps that **fail on purpose**. The DockSentinel
controller (Days 2–5) is developed and evaluated against these failures, and the
files in `evidence/` are real Kubernetes output used as test fixtures.

## Layout

```
k8s-lab/
├── cluster/kind-config.yaml     # 2-node kind cluster "docksentinel"
├── apps/                        # three Node.js 22 lab apps (Dockerfile each)
│   ├── memory-hog/              # MODE=steady|leak – memory failures
│   ├── bad-release/             # v1 works, v2 is missing routes.js
│   └── db-app/                  # needs PostgreSQL, exits after 3 failed checks
├── manifests/                   # healthy baseline (apply in file order)
│   ├── 00-namespace.yaml
│   ├── 10-postgres.yaml
│   ├── 20-db-app.yaml
│   ├── 30-memory-hog.yaml
│   └── 40-bad-release.yaml
├── scripts/
│   ├── build-and-load.sh        # build images, load them into kind
│   ├── break.sh <scenario>      # inject one failure
│   ├── reset.sh <scenario|all>  # undo it (safe to run any time)
│   ├── status.sh                # one line per pod: reason, exit code, restarts
│   ├── wait-for.sh <scenario>   # poll until the expected signal appears (max 3 min)
│   ├── capture.sh <scenario>    # save evidence to evidence/<scenario>/
│   └── run-scenario.sh <s>      # all of the above, end to end
└── evidence/<scenario>/         # captured fixtures (see below)
```

## Prerequisites

Docker Engine (works without `sudo`), `kind` ≥ 0.30, `kubectl`, about 4 GB free RAM
and 10 GB disk. Node.js is **not** needed locally (everything builds in `node:22-alpine`).

## Setup

```bash
kind create cluster --config k8s-lab/cluster/kind-config.yaml
kubectl get nodes                          # 2 nodes, both Ready

k8s-lab/scripts/build-and-load.sh          # build lab/* images, load into kind

kubectl apply -f k8s-lab/manifests/
kubectl -n lab rollout status deploy --timeout=180s
k8s-lab/scripts/status.sh                  # every pod Running, READY true
```

On the very first apply, db-app may restart once because it starts before postgres
is Ready. It connects on the next try; `reset.sh all` gives a clean state.

Try an app: `kubectl -n lab port-forward svc/bad-release 8080:8080`, then
`curl localhost:8080/` → `{"version":"v1"}`.

## Failure scenarios

```bash
k8s-lab/scripts/break.sh oom-limit         # inject
k8s-lab/scripts/status.sh                  # watch
k8s-lab/scripts/reset.sh oom-limit         # undo   (or: reset.sh all)
k8s-lab/scripts/run-scenario.sh oom-limit  # whole cycle + capture evidence
```

Signals below were **observed** on 2 Nov 2026 (kind v0.33.0, Kubernetes v1.37.0).
"Time" is from `break.sh` until `wait-for.sh` saw the signal (it polls every 10 s).

| Scenario | break.sh does | Observed signal | Time | reset.sh does |
|---|---|---|---|---|
| `oom-limit` | `set env deploy/memory-hog WORKING_SET_MB=160` (limit 128Mi) | new pod: `lastState.terminated.reason=OOMKilled`, exit **137**, then `CrashLoopBackOff`. Dies within 1 s of starting; log stops after `starting ... WORKING_SET_MB=160`. **No event mentions OOM**, only `BackOff`. Old pod keeps running. | 11 s | `WORKING_SET_MB=30` |
| `oom-leak` | `set env deploy/memory-hog MODE=leak` | runs and becomes Ready, then `OOMKilled` (exit 137) **exactly 30 s after each start**; logs show rss rising (`held=80 MB rss=134 MB`); restart count climbs; events: probe failures + `BackOff` | 71 s (2 restarts) | `MODE=steady` |
| `bad-release` | `set image ... app=lab/bad-release:v2` + change-cause `"v2 release"` | v2 pod: `Error`, exit **1**, `CrashLoopBackOff`; logs `Error: Cannot find module './routes'` / `code: 'MODULE_NOT_FOUND'`; **both v1 pods keep serving** (`maxUnavailable: 0`); history: 1 `v1 initial`, 2 `v2 release` | 10 s | `rollout undo` (only if not on v1) |
| `bad-image` | `set image ... app=lab/bad-release:v3` (tag does not exist) | `ErrImagePull` → `ImagePullBackOff`, phase `Pending`, no container logs; event: `Failed to pull image "lab/bad-release:v3": ... pull access denied, repository does not exist`; v1 pods keep serving | 20 s | `rollout undo` |
| `missing-secret` | delete Secret `db-creds`, `rollout restart deploy/db-app` | new pod `Pending`, `CreateContainerConfigError`, no logs; event `Error: secret "db-creds" not found`; old pod keeps running | 10 s | re-apply `20-db-app.yaml`. The stuck pod then starts **by itself** (kubelet retries) |
| `db-down` | `scale deploy/postgres --replicas=0` | db-app logs `database check failed (n/3): ECONNREFUSED ... 10.96.170.22:5432` ×3 then `giving up`, exit **1** after 10 s; readiness `503`; `CrashLoopBackOff` | 61 s | scale postgres to 1, wait Ready, `rollout restart deploy/db-app` |

### Evidence files (`evidence/<scenario>/`)

| File | Contents |
|---|---|
| `pods.json` | `kubectl -n lab get pods -o json` (all lab pods) |
| `describe.txt` | `kubectl describe pod` of the failing pod |
| `logs-previous.txt` | logs of the last crashed container (`--previous`); an error message when none exists (image/config errors) |
| `logs-current.txt` | logs of the current container |
| `events.txt` | all `lab` events, sorted by time |
| `deployment.json` / `replicasets.json` | the affected Deployment and its ReplicaSets (revision annotations) |
| `rollout-history.txt` | `kubectl rollout history` |
| `deployment-postgres.json` | db-down only: the database Deployment (0 replicas) |
| `status.txt`, `meta.txt` | `status.sh` output; scenario, pod name, capture time |

### Notes for Day 2/3 (collector and rules)

- **OOM is only in pod status.** No Kubernetes event says "OOMKilled"; read
  `status.containerStatuses[].lastState.terminated` (reason + exit code).
- **oom-limit vs oom-leak:** both are `OOMKilled`/137. They differ in how long the
  container ran (≈1 s vs ≈30 s), the log (RSS climbing in a leak), and whether it
  repeats after the limit is raised.
- **`events.txt` contains events from earlier scenarios** (events live about an hour).
  Filter by the failing pod's name (`involvedObject.name`).
- **Rolling updates keep the old pod alive** for every template change here
  (`maxUnavailable: 0`, or 0 by rounding for 1 replica). A failing new pod sits next
  to a healthy old one, so "Deployment available" alone does not mean "release OK".
- **`rollout undo` creates a new revision number**: undoing from 2 to 1 gives
  revision 3 with the v1 template. Track "last good" by ReplicaSet/template, not by
  the number alone.
- Pods that are shutting down briefly show phase `Succeeded` (the apps exit 0 on
  SIGTERM). Ignore pods with `metadata.deletionTimestamp` set.

## Workarounds needed on this machine

1. **`kind load docker-image postgres:16-alpine` fails** with
   `ctr: content digest sha256:... not found`. Docker 29 uses the containerd image
   store; postgres is a multi-platform image and only the local platform's layers
   are downloaded, but kind imports all platforms. `build-and-load.sh` instead runs
   `docker save --platform linux/<arch>` and `kind load image-archive`.
2. **OOM was not triggered at first.** `Buffer.alloc(1 MB)` returned fresh kernel
   pages that are already zero, so they were never written and never counted
   (rss 52 MB while "holding" 160 MB). memory-hog now fills buffers with `1`s
   (`Buffer.alloc(MB, 1)`).
3. **Slow pod shutdown (30 s).** Node.js as PID 1 ignores SIGTERM, so Kubernetes
   waited for the full grace period. The apps now exit on SIGTERM.

## Tear down

```bash
kind delete cluster --name docksentinel
```
