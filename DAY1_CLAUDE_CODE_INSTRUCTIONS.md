# DockSentinel – Day 1: Kubernetes Failure Lab

> Paste this whole file into Claude Code, opened in the root of the DockSentinel repo
> (github.com/amithm18/DockerSentinel) on a Ubuntu machine.

---

## Your role and rules (for Claude Code)

You are helping a student team that knows Docker basics but is **new to Kubernetes**.
Today's goal is NOT to build DockSentinel. It is to build a **local Kubernetes lab
with apps that fail on purpose**, so that the DockSentinel controller (Days 2–5)
has real failures to detect, diagnose and recover from.

Follow these rules:

1. **Teach as you go.** After each step, explain in 2–4 plain sentences what you did
   and what the Kubernetes concept is. Assume the reader has never used `kubectl`.
2. **Check before installing.** Run `which`/`--version` first. Never run `sudo`
   without showing me the exact command and asking me first.
3. **Do not touch the existing Python code** in the repo. Put everything for today
   under a new top-level folder `k8s-lab/`.
4. Work on a new git branch `day1-k8s-lab`. Commit at the end of each step with a
   clear message. **Do not push** until I say so.
5. If a step fails, show the error, explain the cause, fix it, and continue.
   If you are stuck after 2 attempts, stop and ask me.
6. Use **Node.js 22 (LTS)** images (`node:22-alpine`). Node 20 is end-of-life.
7. Every container must have **resource requests/limits** and a **readiness probe**.

---

## Step 0 – Inspect the repo and machine

1. Show the repo's top-level structure and current branch. Create branch `day1-k8s-lab`.
2. Check and report versions of: `docker`, `kind`, `kubectl`, `node`, `git`.
3. Check Docker works without sudo: `docker info` and `docker run --rm hello-world`.
   - If Fedora is using Podman instead of Docker, tell me. Prefer real Docker Engine
     (`docker-ce`). Only if I agree, use kind with `KIND_EXPERIMENTAL_PROVIDER=podman`.
   - If permission denied: suggest adding me to the `docker` group (ask first).
4. Report free RAM (`free -h`) and disk (`df -h ~`). Need about 4 GB RAM and 10 GB disk.

---

## Step 1 – Install kind and kubectl (if missing)

Install into `~/.local/bin` (no sudo needed); make sure it is on `PATH`.

- **kubectl:** download the latest stable binary from `https://dl.k8s.io`
  (use `https://dl.k8s.io/release/stable.txt` to get the version), verify the
  checksum, `chmod +x`.
- **kind:** download the latest stable `kind-linux-amd64` release from the official
  kind GitHub releases page, `chmod +x`.

Verify: `kubectl version --client` and `kind version`.

**Explain:** what kind is (Kubernetes nodes running as Docker containers) and what
kubectl is (the CLI that talks to the Kubernetes API server).

---

## Step 2 – Create the cluster

Create `k8s-lab/cluster/kind-config.yaml`:
- cluster name `docksentinel`
- one control-plane node and one worker node

Run `kind create cluster --config k8s-lab/cluster/kind-config.yaml`.

Verify and show output of:
```
kubectl cluster-info --context kind-docksentinel
kubectl get nodes -o wide
kubectl get pods -A
```

Create namespace `lab` (as a YAML file `k8s-lab/manifests/00-namespace.yaml`, then apply).

**Explain:** cluster, node, control plane, namespace, and what the pods in
`kube-system` are.

---

## Step 3 – Write three small Node.js lab apps

Create under `k8s-lab/apps/`. Keep each app short (under ~80 lines), well commented,
listening on port **8080**, with `GET /healthz` and `GET /` endpoints, and logging
clearly to stdout. Use the built-in `http` module (no Express) except where noted.

### App 1 – `memory-hog`
Controls memory use through environment variables:
- `MODE=steady` (default): on start, allocate and **hold** `WORKING_SET_MB` MB
  (default `30`) using `Buffer.alloc` (zero-filled, so memory is really used).
- `MODE=leak`: after `LEAK_DELAY_SEC` (default `20`) seconds, allocate an extra
  `LEAK_MB_PER_SEC` (default `10`) MB every second, forever.
- Log the current RSS (`process.memoryUsage().rss`) every 5 seconds.

Purpose: `steady` + a too-large working set = **"memory limit too low"** (fixable by
raising the limit). `leak` = **"memory leak"** (raising the limit does not fix it;
DockSentinel must give up after retries).

### App 2 – `bad-release`
- `server.js` does `require('./routes')` and serves `GET /` from it, returning
  `{"version": APP_VERSION}`.
- The Dockerfile takes build args `APP_VERSION` and `BREAK` (default `0`).
  If `BREAK=1`, the Dockerfile **deletes `routes.js`** after copying the source.
  This produces a *real* `Error: Cannot find module './routes'` at startup.
- Build two images: `lab/bad-release:v1` (working) and `lab/bad-release:v2`
  (`BREAK=1`, crashes on start).

Purpose: simulate a **bad release** that should be rolled back.

### App 3 – `db-app`
- Uses the `pg` npm package (include `package.json` and a `package-lock.json`;
  generate the lockfile with a `node:22-alpine` container if Node is not installed
  locally).
- Reads `DATABASE_URL` from the environment.
- Every 5 seconds runs `SELECT 1`. `/healthz` returns 200 only if the last check
  succeeded.
- After **3 consecutive failures**, log the full error (it will contain
  `ECONNREFUSED` or similar) and `process.exit(1)`.

Purpose: simulate **"database down"** and **"missing Secret"** failures.

### Dockerfiles (all three apps)
- `FROM node:22-alpine`, `WORKDIR /app`, copy package files first, then `npm ci --omit=dev`
  (only where there are dependencies), copy source, `USER node`, `EXPOSE 8080`,
  `CMD ["node", "server.js"]`.
- Add a `.dockerignore` (`node_modules`, `.git`, `*.md`).

**Explain:** why we test failures on purpose (chaos/failure-injection testing),
and what each app will teach us.

---

## Step 4 – Build images and load them into kind

Create `k8s-lab/scripts/build-and-load.sh` that:
1. Builds `lab/memory-hog:v1`, `lab/bad-release:v1`, `lab/bad-release:v2`, `lab/db-app:v1`.
2. Pulls `postgres:16-alpine`.
3. Loads all of them into the cluster: `kind load docker-image <image> --name docksentinel`.

Run it. Verify images are inside the node:
`docker exec docksentinel-worker crictl images | grep -E 'lab/|postgres'`

**Explain:** why kind cannot see local Docker images by default, and what
`imagePullPolicy: IfNotPresent` means. Never use the `:latest` tag in this lab.

---

## Step 5 – Kubernetes manifests (healthy baseline)

Create in `k8s-lab/manifests/` (one file per app, numbered for apply order).
All in namespace `lab`, all with labels `app: <name>` and `part-of: docksentinel-lab`.

| File | Contents |
|---|---|
| `10-postgres.yaml` | Secret `postgres-secret` (user `lab`, password `labpass`, db `labdb`); Deployment `postgres` (1 replica, `postgres:16-alpine`, env from that secret, readiness probe `pg_isready -U lab -d labdb`, requests 100Mi/limit 256Mi); Service `postgres` port 5432 |
| `20-db-app.yaml` | Secret **`db-creds`** with `DATABASE_URL=postgres://lab:labpass@postgres:5432/labdb`; Deployment `db-app` (1 replica, env `DATABASE_URL` from `secretKeyRef` on `db-creds`); Service |
| `30-memory-hog.yaml` | Deployment `memory-hog` (1 replica, `MODE=steady`, `WORKING_SET_MB=30`, memory request 64Mi / **limit 128Mi**); Service |
| `40-bad-release.yaml` | Deployment `bad-release` (**2 replicas**, image `lab/bad-release:v1`, rolling update strategy with `maxUnavailable: 0`, `maxSurge: 1`, annotation `kubernetes.io/change-cause: "v1 initial"`); Service |

Every Deployment: readiness probe on `/healthz` (postgres excepted), liveness probe
with a generous `initialDelaySeconds`, CPU request 50m / limit 250m.

Apply all and wait:
```
kubectl apply -f k8s-lab/manifests/
kubectl -n lab rollout status deploy --timeout=180s
kubectl -n lab get pods,svc
```
All pods must be `Running` and `READY 1/1`. Test one app through a port-forward:
`kubectl -n lab port-forward svc/bad-release 8080:8080` then `curl localhost:8080/`.

**Explain:** Pod vs Deployment vs ReplicaSet vs Service; requests vs limits;
readiness vs liveness probe; what a Secret is; what a rolling update is.

---

## Step 6 – Failure scripts

Create `k8s-lab/scripts/break.sh <scenario>` and `k8s-lab/scripts/reset.sh <scenario|all>`.
Each scenario prints what it is doing and what failure to expect.

| Scenario | break.sh does | Expected Kubernetes signal | reset.sh does |
|---|---|---|---|
| `oom-limit` | `kubectl set env deploy/memory-hog WORKING_SET_MB=160` | `lastState.terminated.reason=OOMKilled`, exit code 137, then `CrashLoopBackOff` | set `WORKING_SET_MB=30` |
| `oom-leak` | set `MODE=leak` | Running, then OOMKilled every ~30 s | set `MODE=steady` |
| `bad-release` | `kubectl set image deploy/bad-release app=lab/bad-release:v2` + change-cause annotation `"v2 release"` | new pod `CrashLoopBackOff`; old v1 pods **keep running** (because `maxUnavailable: 0`); logs show `Cannot find module './routes'` | `kubectl rollout undo deploy/bad-release` |
| `bad-image` | set image to `lab/bad-release:v3` (does not exist) | `ErrImagePull` → `ImagePullBackOff` | rollout undo |
| `missing-secret` | delete Secret `db-creds`, then `rollout restart deploy/db-app` | `CreateContainerConfigError`, event says `secret "db-creds" not found` | re-apply `20-db-app.yaml` |
| `db-down` | `kubectl scale deploy/postgres --replicas=0` | db-app logs connection errors, exits, `CrashLoopBackOff` | scale postgres to 1, wait Ready, `rollout restart deploy/db-app` |

Also create `k8s-lab/scripts/status.sh` that prints, for every pod in `lab`:
name, phase, ready, restart count, current waiting reason, last terminated reason
and exit code. Use `kubectl get pods -o jsonpath` or `-o custom-columns`.

**Explain:** what `CrashLoopBackOff` means (Kubernetes restarts the container with
growing delays up to 5 minutes, but never explains *why*). That gap is exactly what
DockSentinel fills.

---

## Step 7 – Run every scenario and capture evidence

Create `k8s-lab/scripts/capture.sh <scenario>` that saves into
`k8s-lab/evidence/<scenario>/`:
- `pods.json` – `kubectl -n lab get pods -o json`
- `describe.txt` – `kubectl -n lab describe pod <failing pod>`
- `logs-previous.txt` – `kubectl -n lab logs <pod> --previous` (ignore error if none)
- `events.txt` – `kubectl -n lab get events --sort-by=.lastTimestamp`
- `deployment.json` – the affected Deployment as JSON
- `rollout-history.txt` – `kubectl -n lab rollout history deploy/<name>`

Then for **each of the 6 scenarios**, in order:
1. `reset.sh all`, confirm every pod is Ready.
2. `break.sh <scenario>`.
3. Wait until the expected signal appears (poll `status.sh` every 10 s, max 3 minutes).
4. `capture.sh <scenario>`.
5. Show me the key lines that prove the failure (reason, exit code, log error, event).
6. `reset.sh <scenario>` and confirm recovery.

These evidence files are **test fixtures** for Day 2 (watcher/collector) and Day 3
(diagnosis rules), so the controller can be developed against real Kubernetes output.

---

## Step 8 – Documentation and wrap-up

1. Write `k8s-lab/README.md`:
   - prerequisites and setup commands (`kind create cluster …`, `build-and-load.sh`, `kubectl apply …`)
   - the scenario table from Step 6, with the **actual** signals observed in Step 7
   - how to tear down: `kind delete cluster --name docksentinel`
2. Write `k8s-lab/LEARNING_NOTES.md`: a one-page beginner glossary of every concept
   used today (cluster, node, pod, deployment, replicaset, service, namespace, secret,
   requests/limits, probes, rolling update, rollout undo, OOMKilled, CrashLoopBackOff,
   ImagePullBackOff, CreateContainerConfigError, events) – each in 1–2 lines with the
   `kubectl` command to see it.
3. Commit everything on `day1-k8s-lab`.
4. Give me a final summary:
   - what works, what didn't, anything that needed a workaround
   - a table of the 6 scenarios with observed signals
   - 5 `kubectl` commands I should practise before Day 2

---

## Day 1 is done when

- [ ] `kubectl get nodes` shows 2 Ready nodes in cluster `docksentinel`
- [ ] All 4 lab Deployments are healthy in namespace `lab`
- [ ] All 6 failure scenarios can be triggered and reset with one command each
- [ ] Evidence for all 6 scenarios is saved under `k8s-lab/evidence/`
- [ ] README and learning notes are written and committed

---

## Troubleshooting hints (for Claude Code)

- **`kind create cluster` hangs or nodes NotReady on Ubuntu:** check
  `fs.inotify.max_user_watches` / `max_user_instances`; propose raising them with
  `sysctl` (ask before sudo).
- **`ErrImageNeverPull` / pull errors for `lab/*` images:** the image was not loaded
  into kind or the tag differs; re-run `kind load docker-image`.
- **OOM not triggered:** confirm the limit is really 128Mi
  (`kubectl get deploy memory-hog -o yaml`) and that memory is touched (zero-filled
  `Buffer.alloc`, references kept in an array).
- **db-app crashes even in baseline:** postgres may not be Ready yet; db-app should
  retry 3 times (15 s) before exiting, and Kubernetes will restart it; wait for
  postgres readiness in the setup script.
- **SELinux denials on Ubuntu:** report the `ausearch`/`journalctl` message rather than
  disabling SELinux.
