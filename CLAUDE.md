# DockSentinel – Project Context for Claude Code

## What this project is
DockSentinel is an MCA461P minor project (RV College of Engineering, Dept. of MCA).
Team: Amith M, Jairam Hegde (JK). Guide: Dr. Andhe Dharani.
Repo: github.com/amithm18/DockerSentinel. Development OS: Ubuntu Linux.

**One-line pitch:** a standalone, plug-in reliability tool that takes any supported
repository from code to a running Kubernetes deployment, then detects, diagnoses and
safely recovers its runtime failures, explaining every failure in plain English.

**Positioning (the research gap):**
- Kubernetes self-heals but restarts blindly (`CrashLoopBackOff`) and never explains *why*.
- Datadog-style platforms explain partly but only alert, and they are paid.
- k8sgpt explains but does not fix; Robusta automates but needs custom playbooks.
- Dockerfile-generation research (Repo2Run, Shipwright) covers build time only.
- DockSentinel = generation + diagnosis + cause-specific, **verified** recovery with safe
  rollback, aimed at small teams and beginners.

## The team's level
The team knows Docker basics (images, containers, push/pull, containerizing simple
Node.js apps) and is **new to Kubernetes**. When working with them:
- Explain each step in plain words; introduce Kubernetes concepts as they appear.
- Prefer simple, readable code over clever code. Comment the why, not the what.
- Ask before running `sudo`, pushing to GitHub, or deleting anything.
- Work on feature branches; commit in small steps; never push without permission.

## Architecture

### Part 1 – Repo side (one-time setup, works on any supported repo)
- **Analyzer:** reads the repo (`package.json`, lockfiles, `requirements.txt`/`pyproject.toml`,
  regexes for `listen(PORT)` and `process.env.X`, `.env.example`) → a profile:
  stack, version, install/build/start commands, port, env vars, DB dependency.
- **Generator:** fills **Jinja2 templates** (not an LLM) → `Dockerfile`, `.dockerignore`,
  `k8s/deployment.yaml`, `k8s/service.yaml`, `.github/workflows/build.yml`.
  Runs **once** (`docksentinel init`), refuses to overwrite existing files; a separate
  `update` command shows a diff / opens a PR instead of overwriting.
  Templates bake in best practices: non-root user, healthcheck/readiness probe,
  resource requests/limits, pinned base image, no `:latest`.
- **CI/CD:** GitHub Actions builds on every push, tags the image with the commit SHA,
  pushes to Docker Hub/GHCR, then deploys (`kubectl apply` or Ansible).
- Files land in the repo root (Dockerfile, workflow) and `k8s/`.

### Part 2 – Cluster side (installed once per cluster, independent of any repo)
A **Kubernetes controller** written in Python, running as a Deployment in its own namespace,
with a ServiceAccount + RBAC (get/list/watch pods, pods/log, events; get/patch deployments;
list replicasets). It talks only to the Kubernetes API (no Docker socket).
Apps opt in with label `docksentinel.io/enabled: "true"`.

Runtime loop:
1. **Watch** pods/events (`kubernetes` Python client `watch`, or `kopf`).
2. **Collect** for a failing pod: waiting/terminated reason, exit code, restart count,
   owning Deployment and revision, last 100 log lines (`previous=True` for crashed containers).
3. **Diagnose** with YAML rules (top-down match) → a cause + plain-English message.
4. **Decide**: auto-fixable and retries left? (max 3 attempts per Deployment per hour,
   backoff 10 s / 30 s / 90 s; otherwise report only).
5. **Recover** with a cause-specific action.
6. **Verify**: all replicas Ready for 60 s. On success, record the revision as "last good".
7. **Log and alert**: SQLite incidents table, FastAPI dashboard, Telegram/e-mail.

### Diagnosis → action table (MVP)
| Kubernetes signal | Cause | Action |
|---|---|---|
| `OOMKilled`, exit 137 | Memory limit too low | Patch Deployment memory limit ×2 (cap 1 GiB) |
| Repeated `OOMKilled` after raises | Memory leak | Stop, report "needs code fix" |
| `CrashLoopBackOff` soon after a new revision | Bad release | Roll back to last good revision |
| `ImagePullBackOff` / `ErrImagePull` | Bad image tag | Roll back, report the tag |
| `CreateContainerConfigError` | Missing Secret/ConfigMap | Report only (name the missing object) |
| Logs contain `ECONNREFUSED` to DB | Dependency down | Restart DB Deployment, wait Ready, restart app |
| Readiness failing | Unhealthy app | Restart pod, check logs for a cause |
| `Pending`, insufficient resources | Cluster too small | Report only |

Rollback = patch the Deployment's pod template with the template of the ReplicaSet
whose `deployment.kubernetes.io/revision` is the last good one (this is what
`kubectl rollout undo` does; the API has no single undo call). Only roll back to a
revision that previously passed verification.

**Never auto-fix:** code bugs, missing env/Secrets, bad config. Report the cause and fix steps.

### LLM use
Not in the core. Rules and templates are deterministic, offline, explainable and easy
to evaluate. An LLM may be an **optional, advisory** extra later (explaining unmatched
errors, a fallback Dockerfile that must be validated by building it). It never takes actions.

### Packaging (standalone / plug-in)
- `docksentinel` CLI (pip package or Docker image): `init`, `update`, `deploy`, `status`.
- Reusable GitHub Action (`docksentinel-action`) that any repo calls in one workflow step.
- Controller shipped as `install.yaml` (MVP) and later a Helm chart.
- Suggested monorepo layout: `controller/`, `cli/`, `action/`, `deploy/`, `k8s-lab/`, `docs/`.

## Scope
**In:** single-service Node.js and Python apps (optional PostgreSQL/Redis), Kubernetes
(kind/k3d locally, k3s on a cloud VM online), the failure types above, dashboard, alerts,
evaluation.
**Out:** multi-service/microservice repos, production-grade multi-cluster setups,
fixing application code, Docker Swarm.
**Known limitations to state honestly:** rules cover known failure types only; the
controller needs cluster-wide read access; it is not a replacement for full observability
platforms.

## Tech stack
Python 3.11+, `kubernetes` Python client (or `kopf`), PyYAML, Jinja2, SQLite, FastAPI +
Chart.js, Telegram Bot API / SMTP. kind or k3d locally; k3s online; Ansible to set up k3s
on VMs; GitHub Actions; Docker Hub/GHCR. Lab apps: Node.js 22 LTS (`node:22-alpine`;
Node 20 is end-of-life), `postgres:16-alpine`.

## Local vs online
- **Local:** kind cluster on the Fedora laptop; controller first run from the laptop
  (`load_kube_config`), then in-cluster (`load_incluster_config`).
- **Online:** k3s on 1–2 cloud VMs installed by Ansible; dashboard behind a reverse proxy
  with basic login; alerts to a Telegram group.

## Evaluation (for the report)
Inject 6–8 failure types; compare DockSentinel against plain Kubernetes behaviour
(and Docker restart policy). Metrics: mean time to recovery, recovery success rate,
diagnosis accuracy (target > 85%), controller memory (< 100 MB), idle CPU.

## 5-day MVP plan
1. **Day 1 – Failure lab:** install kind/kubectl, 2-node cluster `docksentinel`, namespace
   `lab`; apps `memory-hog` (MODE steady/leak), `bad-release` (v1 works, v2 missing
   `./routes`), `db-app` (pg, exits after 3 failed checks) + Postgres; `break.sh` /
   `reset.sh` / `status.sh` / `capture.sh`; evidence saved to `k8s-lab/evidence/<scenario>/`
   as test fixtures. Scenarios: oom-limit, oom-leak, bad-release, bad-image,
   missing-secret, db-down. (Detailed instructions: `DAY1_CLAUDE_CODE_INSTRUCTIONS.md`.)
2. **Day 2 – Watcher + collector:** detect failing pods; build incident records; unit-test
   against Day 1 evidence fixtures.
3. **Day 3 – Diagnosis + storage:** `rules.yaml` + matcher; SQLite `incidents` and
   `good_revisions`; plain-English messages.
4. **Day 4 – Recovery + verification:** raise memory, rollback, DB restart; 60 s
   verification; retry limits.
5. **Day 5 – In-cluster + demo:** controller image, RBAC manifests, Telegram alerts,
   demo script, measure recovery times.

**After MVP:** analyzer/generator CLI → GitHub Action → FastAPI dashboard → Ansible + k3s
online → evaluation chapter.

## Status (as of 27 Oct 2026)
- Phase 1 review deck updated for this direction (lit survey of 10 papers).
- An earlier plain-Docker prototype exists (event listener, collector, rule-based
  diagnosis, SQLite, CLI). Keep it; do not delete or rewrite it without asking.
- Next step: Day 1 failure lab on branch `day1-k8s-lab`.
- Open item: confirm with the guide that the Kubernetes-first direction fits the
  approved synopsis (originally plain-Docker focused).
