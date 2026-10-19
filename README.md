# DockerSentinel

A lightweight tool that runs on your own computer, watches your Docker containers, and when one fails, tells you **what went wrong** and **what changed since it last worked**.

- Runs locally, needs no server and no account, and sends nothing online.
- Uses one external library (`docker`). Everything else is Python's standard library.
- Never changes your containers. It only reads.

---

## 1. The problem

In small teams there is often no DevOps engineer. When a container that worked yesterday fails today, Docker only shows something like:

```
Exited (1) 3 seconds ago
```

The developer then has to dig through `docker logs` and `docker inspect` and guess what changed: the Dockerfile, an environment variable, a port, a file?

**DockerSentinel answers that automatically:**

```
╭─ #5  CRASHED ──────────────────────────────────────────── 02:03:50 ─╮
│ demo-app  ·  demo-app  ·  exit 1                                    │
├─────────────────────────────────────────────────────────────────────┤
│ ✖ PROBLEM                                                           │
│   It worked at 02:03. Since then 1 setting(s) changed,              │
│   most likely cause first:                                          │
│     • Dockerfile step REMOVED:  COPY config.txt .                   │
│ ✔ SOLUTION                                                          │
│   Your Dockerfile no longer has 'COPY config.txt .'.                │
│   Add it back and rebuild the image.                                │
├─────────────────────────────────────────────────────────────────────┤
│ LAST LOGS                                                           │
│   Error: config.txt not found                                       │
╰─────────────────────────────────────────────────────────────────────╯
```

---

## 2. Architecture

```
                         ┌──────────────────────────┐
   you:  docker build    │      Docker daemon        │
         docker run ───► │  (manages all containers) │
                         └────────────┬─────────────┘
                                      │  events  +  inspect / logs / image history
                                      ▼
┌───────────────────────────── DockerSentinel ──────────────────────────────┐
│                                                                           │
│   collector.py ──── finds problems and collects evidence                  │
│        │                                                                  │
│        ├──► tracker.py ── saves a snapshot of every run (good or bad)     │
│        │                                                                  │
│        ├──► rules.py ──── "HOW did it fail?"  (exit code, error text)     │
│        │                                                                  │
│        └──► changes.py ── "WHAT CHANGED since it last worked?"            │
│                     │                                                     │
│                     ▼                                                     │
│              store.py ──── SQLite file: ~/.docker-sentinel/sentinel.db    │
│                     │                                                     │
│                     ▼                                                     │
│              ticket.py ─── prints the ticket in the terminal              │
│                                                                           │
│   cli.py ── the commands: watch / check / history / demo                  │
└───────────────────────────────────────────────────────────────────────────┘
```

### What each file does

| File | Job |
|---|---|
| `collector.py` | Talks to Docker. Listens to events and collects evidence (state, exit code, logs, settings). |
| `tracker.py` | Saves a snapshot of each run and marks it **good** (ran fine) or **bad** (crashed). |
| `rules.py` | Explains **how** a container failed, from its exit code and error messages. |
| `changes.py` | The core algorithm: compares the failing run with the last good run. |
| `store.py` | Saves tickets and snapshots in a local SQLite database. |
| `ticket.py` | Draws the ticket in the terminal (colors and a box, plain Python). |
| `cli.py` | The `sentinel` command and its sub-commands. |
| `demo.py` | `sentinel demo`, a 10-second self-running demonstration. |

---

## 3. How it collects data

DockerSentinel is a **normal Docker client**, just like the `docker` command. It does not sit between you and Docker, so Docker keeps working even if Sentinel stops.

It collects data in three ways:

| How | When | Catches |
|---|---|---|
| **Docker events** (live stream) | Instantly | `start`, `die`, `oom`, `kill`, `health_status` |
| **Scan every 5 seconds** | Background thread | Containers that **failed to start**. Docker sends no event for these. |
| **Reading container details** | When something happens | `docker inspect`, `docker logs`, `docker image history` |

### What it reads about a container

| Data | From | Used for |
|---|---|---|
| Exit code, OOM flag, error message | `docker inspect` → `State` | How it failed |
| Last 30 log lines | `docker logs` | Why it failed |
| Env vars, ports, mounts, command, memory limit | `docker inspect` → `Config`, `HostConfig` | What changed |
| Dockerfile steps + file fingerprints | `docker image history` + image layers | What changed in the build |

Env var **values are never stored**, only a short fingerprint (SHA-256 hash). That's enough to tell whether a value changed without saving passwords.

---

## 4. How it works, step by step

### 4.1 The life of a snapshot

```
 container starts ──► snapshot saved as "candidate"
                             │
          ┌──────────────────┼──────────────────────┐
          ▼                  ▼                      ▼
   runs 15 s fine     exits with code 0       crashes early
          │                  │                      │
          ▼                  ▼                      ▼
       "good"             "good"                 "bad"  ──► compare with last "good"
```

### 4.2 Recognizing "the same app"

Every `docker run` creates a **new container ID**, so Sentinel uses a stable name instead:

1. Docker Compose project + service, e.g. `shop/api`
2. otherwise the container name, e.g. `demo-app`
3. otherwise the image name, if Docker made up a random container name

### 4.3 The "what changed" algorithm

**Step 1: Flatten.** The nested `docker inspect` output becomes simple `key → value` pairs:

```
env.API_KEY               = hash:f7059728b631
port.80/tcp               = 8080
mount./app/config         = /home/me/config
command                   = sh app.sh
build: COPY config.txt .  = 3fa21c09e8b1      ← fingerprint of the files this step copied
build: COPY app.sh .      = 91bd02aa7c55
```

**Step 2: Compare.** Diff the last good snapshot against the failing one:

| Kind | Meaning |
|---|---|
| **removed** | was there before, missing now (e.g. a Dockerfile line was deleted) |
| **changed** | same key, different value (e.g. you edited `app.sh`, so its fingerprint changed) |
| **added** | new key |

**Step 3: Rank.** Each change gets a score, so the most likely cause comes first:

| Change | Base score |
|---|---|
| Env var removed / Dockerfile step removed | 5 |
| Copied files changed / mount changed | 4 |
| Command or memory limit changed | 3 |
| Port or other setting changed | 2 |
| Something added | 1 |

**Bonus points** when the evidence matches the change. For example, if the error log says `config.txt not found` and the step `COPY config.txt .` was removed, that step gets +5.

**Step 4: Remove rebuild noise.** When one Dockerfile step changes, Docker rebuilds every step after it. Only the **first** change is the real cause, so the later ones are hidden.

**Step 5: Explain.** The top-ranked change becomes the headline of the ticket, with a suggested fix.

### 4.4 Other problems it detects (base rules)

| Situation | How it is detected |
|---|---|
| Out of memory | `OOMKilled = true`, or an `oom` event while the container keeps running |
| Command not found | exit code 127 |
| Port already in use | "port is already allocated" in Docker's error |
| Missing file / permission | "no such file or directory" / "permission denied" in the logs |
| Can't reach a database or API | "connection refused" in the logs |
| Missing library | "ModuleNotFoundError" / "Cannot find module" in the logs |
| Crash loop | restart count above 3 |
| Failing health check | `health_status: unhealthy` event |

User actions such as `docker stop`, `docker kill` and `docker rm -f` are **not** reported as crashes.

---

## 5. Installation

### Requirements

- **Python 3.9 or newer**
- **Docker**, installed and running
- **Linux only:** your user must be allowed to use Docker:
  ```bash
  sudo usermod -aG docker $USER
  ```
  Then log out and log in again.

> Tested on Fedora Linux. It should also work on macOS and Windows with Docker Desktop, but that has not been tested yet.

### Install steps

```bash
# 1. Get the project (copy the folder, or clone it from git)
cd DockerSentinel

# 2. Create a virtual environment
python3 -m venv sentinelenv

# 3. Activate it
source sentinelenv/bin/activate          # Linux / macOS
# sentinelenv\Scripts\activate           # Windows

# 4. Install DockerSentinel (also installs the docker library)
pip install -e .

# 5. Check that it works
sentinel demo
```

### Optional: use `sentinel` from any folder

With [pipx](https://pipx.pypa.io), the command works in every terminal without activating anything:

```bash
sudo dnf install pipx            # Fedora   (Ubuntu: sudo apt install pipx)
pipx install -e /path/to/DockerSentinel
```

### Commands

| Command | What it does |
|---|---|
| `sentinel watch` | Keeps running and prints a ticket for every new problem. **This is the main mode.** |
| `sentinel check` | Checks all containers once, right now |
| `sentinel history -n 10` | Shows the last 10 saved tickets |
| `sentinel demo` | 10-second demonstration with a fake app, no setup needed |

Tickets are saved in `~/.docker-sentinel/sentinel.db`.

---

## 6. How to demonstrate it

The `demo-app/` folder contains a tiny app you can break on purpose:

```
demo-app/
├── Dockerfile     FROM alpine, COPY config.txt, COPY app.sh, CMD sh app.sh
├── app.sh         prints "App is running", fails if config.txt is missing
└── config.txt     mode=production
```

### Before the demo

```bash
cd DockerSentinel
source sentinelenv/bin/activate
docker rm -f demo-app            # remove leftovers from earlier runs
sentinel check                   # saves old broken containers, so they don't appear during the demo
cd demo-app
docker build -t demo-app .       # build once in advance
```

Make sure `demo-app/Dockerfile` is in its working state:

```dockerfile
FROM alpine
WORKDIR /app
COPY config.txt .
COPY app.sh .
CMD ["sh", "app.sh"]
```

### Live demo

**Terminal 1:** start Sentinel.
```bash
sentinel watch
```

**Terminal 2:** run the working version.
```bash
docker run -d --name demo-app demo-app
docker logs demo-app             # shows "App is running"
```

**Wait 15 seconds.** Sentinel saves this as the last good run.

**Break it.** Delete the line `COPY config.txt .` from the Dockerfile, then:
```bash
docker rm -f demo-app
docker build -t demo-app .
docker run -d --name demo-app demo-app
```

**Terminal 1** now shows a ticket: *"Dockerfile step REMOVED: COPY config.txt ."*

### More things to break (same three commands each time)

| Edit | Sentinel says |
|---|---|
| Delete `COPY config.txt .` | Dockerfile step REMOVED: `COPY config.txt .` |
| Change `CMD` to `["sh", "app2.sh"]` | Start command changed from `sh app.sh` to `sh app2.sh` |
| Keep the Dockerfile, change the last line of `app.sh` to `exit 1` | Same step, but the copied files changed: `COPY app.sh .`, so the bug is in the code change |

### Show the saved history

```bash
sentinel history -n 3
```

### Backup if anything fails live

```bash
sentinel demo
```

### After the demo

```bash
docker rm -f demo-app
```
Restore `Dockerfile` and `app.sh` to their original versions.

### Rules for a smooth demo

- Start `sentinel watch` **before** the first `docker run`.
- Wait **15 seconds** after the first run, so it's saved as "good".
- Always use the **same** `--name demo-app`.

---

## 7. Troubleshooting

| Problem | Fix |
|---|---|
| `Could not connect to Docker` | Start Docker: `sudo systemctl start docker`. On Linux, check you are in the `docker` group. |
| `sentinel: command not found` | Activate the virtual environment first: `source sentinelenv/bin/activate` |
| `Cannot connect to the Docker daemon at unix:///.../sentinel.sock` | An old setting is left in this terminal: run `unset DOCKER_HOST` |
| Ticket appears, but without "what changed" | The first run did not run for 15 s while `sentinel watch` was running, or the container name was different. |

---

## 8. Limitations (current version)

- The **first run** of an app has nothing to compare with, so only the base rules apply.
- Containers started with `--rm` may be deleted before their logs can be read.
- `sentinel check` cannot tell a crash from a container you stopped yourself long ago. Only `watch` can.
- It sees container settings and build steps, **not your source code**. It can say "`app.sh` changed", but not which line.

## 9. Roadmap

1. **Hardening:** reconnect when Docker restarts, plus a test for every rule
2. **Terminal dashboard (TUI):** a live list of containers and tickets
3. **Desktop notifications** and auto-start in the background
4. **Cross-container root cause:** "`api` failed because `db` crashed first"
5. **Small ML model** for errors the rules do not recognize
