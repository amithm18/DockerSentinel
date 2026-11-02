# DockSentinel controller (MVP)

A Kubernetes controller that **detects** failing pods, **diagnoses** the root cause
with explainable YAML rules, **recovers** safely with a cause-specific action,
**verifies** that the fix holds, and **records** every incident.

```
watch pods ─▶ collect ─▶ diagnose (rules.yaml) ─▶ decide ─▶ act ─▶ verify ─▶ store + alert
```

| Failure | Kubernetes says | DockSentinel does |
|---|---|---|
| Memory limit too low | `OOMKilled` (exit 137) | Doubles the memory limit (cap 1Gi), verifies |
| Memory leak | `OOMKilled` again after 2 raises | Stops and reports "likely memory leak" |
| Bad image tag | `ImagePullBackOff` | Rolls back to the last revision verified healthy |
| Crashing new release | `CrashLoopBackOff` on a new revision | Rolls back to the last revision verified healthy |
| Missing Secret | `CreateContainerConfigError` | Reports the missing object (never guesses) |
| Database down | logs show `ECONNREFUSED` | Reports the unreachable dependency |

Safety: opt-in label, 3 actions per Deployment per hour, rollback only to a revision
that stayed healthy, 30 s verification after every action, `--dry-run` mode.

## Run the demo (about 10 minutes, Ubuntu + Docker + kind + kubectl)

```bash
cd controller
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q                          # 21 tests, no cluster needed

./demo/demo.sh setup               # kind cluster + 3 demo apps
docksentinel watch -n demo         # terminal 1: leave running
```
Wait for `revision 1 verified healthy` lines (about 30 s), then in **terminal 2**:

```bash
./demo/demo.sh break oom             # watch it raise memory and recover
./demo/demo.sh break bad-image       # watch it roll back
./demo/demo.sh break crash-release   # watch it roll back
./demo/demo.sh break missing-secret  # watch it explain, not "fix"
./demo/demo.sh break leak            # watch it give up safely after 2 raises
docksentinel incidents               # history with recovery times
./demo/demo.sh reset                 # back to healthy
```

Offline diagnosis of any saved pod: `kubectl get pod X -o json > p.json && docksentinel diagnose p.json`

## Run inside the cluster
See `deploy/install.yaml` (least-privilege RBAC + Deployment).

## Layout
```
docksentinel/
  kube.py       all Kubernetes API calls (watch, read, patch)
  collector.py  pod JSON -> Incident
  rules.yaml    diagnosis rules (edit to add new failure types)
  diagnosis.py  first-match rule engine
  engine.py     decide -> act -> verify, retry limits, learning good revisions
  actions.py    memory maths, rollback template, health checks
  store.py      SQLite: incidents, good revisions
  alerts.py     console incident card + optional Telegram
  cli.py        watch | incidents | diagnose
demo/           demo apps + break/reset script
deploy/         in-cluster install (RBAC)
tests/          unit + engine tests with a fake cluster
```

Telegram alerts (optional): `export TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=...`
