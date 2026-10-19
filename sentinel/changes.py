"""
"What changed since it last worked?"

The algorithm, in five steps:
  1. app_key()  - recognise the same app across runs (container IDs change every run)
  2. flatten()  - turn `docker inspect` JSON (+ the image's Dockerfile steps)
                  into simple key -> value pairs
  3. compare()  - diff the last good snapshot against the failing one
  4. rank()     - score each change, so the most likely cause comes first
  5. explain()  - turn the ranked changes into a finding for the ticket

This file is pure logic: it never talks to Docker or the database.
"""

import hashlib
import re


IGNORED_ENV = ["HOSTNAME"]

MAX_CHANGES_SHOWN = 5

UNKNOWN_FILES = "?"

SKIPPED_STEPS = [
    "ENV", "ARG", "CMD", "ENTRYPOINT", "USER", "WORKDIR", "LABEL",
    "HEALTHCHECK", "SHELL", "STOPSIGNAL", "MAINTAINER", "ONBUILD", "VOLUME",
]

COMMON_WORDS = ["install", "update", "apt-get", "echo", "copy", "true", "with", "from"]

DOCKER_RANDOM_NAME = re.compile(r"^[a-z]+_[a-z]+$")


def image_without_tag(image):
    """
    "myapp:latest" -> "myapp",  "localhost:5000/myapp:v2" -> "localhost:5000/myapp"
    A ":" only starts a tag if no "/" comes after it.
    """
    position = image.rfind(":")
    if position == -1:
        return image

    after = image[position + 1:]
    if "/" in after:
        return image

    return image[:position]


def app_key(attrs):
    """
    Step 1: a name for "the same app" that stays the same between runs.

      1. Compose project + service, e.g. "shop/api"
      2. The container name, if the user chose it
      3. The image name without tag, if Docker made up a random name
         (like "fervent_sutherland"), because that name is new every run
    """
    labels = attrs["Config"]["Labels"]
    if labels is None:
        labels = {}

    project = labels.get("com.docker.compose.project", "")
    service = labels.get("com.docker.compose.service", "")
    if project != "" and service != "":
        return "compose:" + project + "/" + service

    name = attrs["Name"].lstrip("/")
    if DOCKER_RANDOM_NAME.match(name) is None:
        return "name:" + name

    return "image:" + image_without_tag(attrs["Config"]["Image"])


def hash_value(value):
    """
    Env values can be passwords, so only a short fingerprint is stored.
    Enough to see that a value changed, without saving the secret.
    """
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return "hash:" + digest[:12]


def join_list(items):
    if items is None:
        return ""
    return " ".join(items)


def format_bytes(number):
    megabytes = number / (1024 * 1024)
    return str(round(megabytes)) + " MB"


def clean_step(created_by):
    """
    Docker's build history writes Dockerfile steps in a raw form:
      'RUN /bin/sh -c pip install flask # buildkit'   -> 'RUN pip install flask'
      'COPY app.py . # buildkit'                      -> 'COPY app.py .'
      '/bin/sh -c #(nop)  EXPOSE 80'  (older builder) -> 'EXPOSE 80'
    """
    text = created_by.strip()

    if text.endswith("# buildkit"):
        text = text[:-len("# buildkit")].strip()

    if text.startswith("/bin/sh -c #(nop)"):
        text = text[len("/bin/sh -c #(nop)"):].strip()
    elif text.startswith("/bin/sh -c "):
        text = "RUN " + text[len("/bin/sh -c "):]

    if text.startswith("RUN /bin/sh -c "):
        text = "RUN " + text[len("RUN /bin/sh -c "):]

    return text


def build_steps(history, layers):
    """
    Turn the image's build history into snapshot keys, one per Dockerfile step:

        "build: COPY app.py ."  ->  fingerprint of the files that step added

    history - from Docker, NEWEST step first. Each entry has CreatedBy and Size.
    layers  - the image's file layers, OLDEST first.

    Every step with Size > 0 created one file layer, in the same order, so
    the n-th such step belongs to the n-th layer. If the counts do not match,
    the fingerprints are left as "?" and only the step text is compared.

    Steps already covered by the container settings (ENV, CMD, USER, ...)
    are skipped, so they are not reported twice.
    """
    oldest_first = list(reversed(history))

    steps_with_files = 0
    for entry in oldest_first:
        if entry["Size"] > 0:
            steps_with_files = steps_with_files + 1

    can_match = steps_with_files == len(layers)

    steps = {}
    layer_number = 0

    for entry in oldest_first:
        fingerprint = "no files"

        if entry["Size"] > 0:
            if can_match:
                fingerprint = layers[layer_number].replace("sha256:", "")[:12]
            else:
                fingerprint = UNKNOWN_FILES
            layer_number = layer_number + 1

        text = clean_step(entry["CreatedBy"])
        if text == "":
            continue

        instruction = text.split(" ")[0].upper()
        if instruction in SKIPPED_STEPS:
            continue

        steps["build: " + text] = fingerprint

    return steps


def flatten(attrs, build=None):
    """
    Step 2: turn the nested `docker inspect` JSON into flat key -> value pairs.

      Config.Env ["DEBUG=false"]                  -> env.DEBUG     = hash:...
      HostConfig.PortBindings {"80/tcp": [8080]}  -> port.80/tcp   = 8080
      Mounts [{Destination: /data, Source: ...}]  -> mount./data   = /home/...

    Things that change on every run (container ID, IP address, hostname,
    creation time) are left out, otherwise every run would look different.
    """
    config = attrs["Config"]
    host = attrs["HostConfig"]
    snapshot = {}

    snapshot["image.name"] = config["Image"]
    snapshot["image.id"] = attrs["Image"]
    snapshot["command"] = join_list(config["Cmd"])
    snapshot["entrypoint"] = join_list(config["Entrypoint"])
    snapshot["user"] = config["User"]
    snapshot["workdir"] = config["WorkingDir"]
    snapshot["restart"] = host["RestartPolicy"]["Name"]

    env_list = config["Env"]
    if env_list is None:
        env_list = []

    for item in env_list:
        name, _, value = item.partition("=")
        if name in IGNORED_ENV:
            continue
        snapshot["env." + name] = hash_value(value)

    bindings = host["PortBindings"]
    if bindings is None:
        bindings = {}

    for container_port in bindings:
        host_ports = []
        for binding in bindings[container_port] or []:
            host_ports.append(binding["HostPort"])
        host_ports.sort()
        snapshot["port." + container_port] = ",".join(host_ports)

    for mount in attrs["Mounts"]:
        if mount["Type"] == "volume":
            source = "volume " + mount["Name"]
        else:
            source = mount["Source"]
        snapshot["mount." + mount["Destination"]] = source

    if host["Memory"] > 0:
        snapshot["limit.memory"] = format_bytes(host["Memory"])

    if host["NanoCpus"] > 0:
        snapshot["limit.cpus"] = str(host["NanoCpus"] / 1000000000)

    networks = attrs["NetworkSettings"]["Networks"]
    if networks is None:
        networks = {}

    for network_name in networks:
        snapshot["network." + network_name] = "connected"

    if build is not None:
        for key in build:
            snapshot[key] = build[key]

    return snapshot


def compare(good, bad):
    """
    Step 3: diff two flat snapshots. There are only three kinds of change:
      removed - key was in the good run, missing now
      changed - key in both, different value
      added   - key is new
    """
    differences = []

    for key in good:
        if key not in bad:
            differences.append({"kind": "removed", "key": key, "old": good[key], "new": None})
        elif good[key] != bad[key]:
            differences.append({"kind": "changed", "key": key, "old": good[key], "new": bad[key]})

    for key in bad:
        if key not in good:
            differences.append({"kind": "added", "key": key, "old": None, "new": bad[key]})

    return differences


def base_score(difference):
    """
    How often this kind of change breaks a container.
    """
    kind = difference["kind"]
    key = difference["key"]

    if key.startswith("env.") and kind == "removed":
        return 5
    if key.startswith("build: ") and kind == "removed":
        return 5
    if key.startswith("build: ") and kind == "changed":
        return 4
    if key.startswith("build: ") and kind == "added":
        return 3
    if key == "image.id":
        return 2
    if key.startswith("mount.") and kind != "added":
        return 4
    if key == "command" or key == "entrypoint":
        return 3
    if key.startswith("limit."):
        return 3
    if kind == "added":
        return 1
    return 2


def evidence_bonus(difference, evidence):
    """
    Extra points when the error matches the change - this links the
    diff to the diagnosis (e.g. logs mention the missing env var).
    """
    key = difference["key"]
    text = evidence["logs"].lower() + " " + evidence["error"].lower()
    bonus = 0

    if key.startswith("env."):
        env_name = key[len("env."):].lower()
        if env_name in text:
            bonus = bonus + 5

    if key == "limit.memory" and (evidence["oom_killed"] or evidence["exit_code"] == 137):
        bonus = bonus + 5

    if key.startswith("mount.") and ("no such file" in text or "permission denied" in text):
        bonus = bonus + 3

    if key == "user" and "permission denied" in text:
        bonus = bonus + 3

    if key.startswith("port.") and ("port is already allocated" in text or "address already in use" in text):
        bonus = bonus + 3

    if evidence["exit_code"] == 127 and (key == "command" or key == "entrypoint" or key == "image.id"):
        bonus = bonus + 3

    if (key == "command" or key == "entrypoint") and "no such file" in text:
        bonus = bonus + 3

    if key.startswith("build: "):
        step = key[len("build: "):]
        if step_mentioned_in(step, text):
            bonus = bonus + 5

    return bonus


def step_mentioned_in(step, text):
    """
    True if a meaningful word of a Dockerfile step appears in the error text.
    Example: step "COPY config.txt ." and log "config.txt not found".
    Very short and very common words are ignored.
    """
    words = step.replace("/", " ").replace("=", " ").split(" ")

    for word in words[1:]:
        word = word.strip(".,'\"").lower()
        if len(word) < 4:
            continue
        if word in COMMON_WORDS:
            continue
        if word in text:
            return True

    return False


def rank(differences, evidence):
    """
    Step 4: score every change and sort, highest score first.
    """
    for difference in differences:
        difference["score"] = base_score(difference) + evidence_bonus(difference, evidence)

    ranked = sorted(differences, key=lambda difference: difference["score"], reverse=True)
    return ranked


def short_id(image_id):
    return image_id.replace("sha256:", "")[:12]


def describe(difference):
    """
    One plain sentence for one change.
    """
    kind = difference["kind"]
    key = difference["key"]
    old = difference["old"]
    new = difference["new"]

    if key.startswith("env."):
        name = key[len("env."):]
        if kind == "removed":
            return "Environment variable " + name + " was REMOVED."
        if kind == "added":
            return "Environment variable " + name + " was added."
        return "Environment variable " + name + " has a different value."

    if key == "image.id":
        return "The image was rebuilt or re-pulled (" + short_id(old) + " -> " + short_id(new) + ")."

    if key.startswith("build: "):
        step = key[len("build: "):]
        if kind == "removed":
            return "Dockerfile step REMOVED:  " + step
        if kind == "added":
            return "Dockerfile step added:  " + step
        if step.upper().startswith("COPY") or step.upper().startswith("ADD"):
            return "Same Dockerfile step, but the copied files are different (your code/files changed):  " + step
        return "Same Dockerfile step, but it produced different files this time:  " + step

    if key.startswith("mount."):
        path = key[len("mount."):]
        if kind == "removed":
            return "Nothing is mounted at " + path + " anymore (was " + old + ")."
        if kind == "added":
            return "New mount at " + path + " from " + new + "."
        return "Mount at " + path + " now comes from " + new + " (was " + old + ")."

    if key.startswith("port."):
        port = key[len("port."):]
        if kind == "removed":
            return "Port " + port + " is no longer published (was " + old + ")."
        if kind == "added":
            return "Port " + port + " is now published on " + new + "."
        return "Port " + port + " is now published on " + new + " (was " + old + ")."

    if key == "limit.memory":
        if kind == "removed":
            return "The memory limit was removed (was " + old + ")."
        if kind == "added":
            return "A memory limit of " + new + " was added."
        return "The memory limit changed from " + old + " to " + new + "."

    if key == "command" or key == "entrypoint":
        if kind == "changed":
            return "The start " + key + " changed from '" + old + "' to '" + new + "'."

    if key.startswith("network."):
        network = key[len("network."):]
        if kind == "removed":
            return "No longer connected to network " + network + "."
        return "Now connected to network " + network + "."

    if kind == "removed":
        return key + " was removed (was '" + str(old) + "')."
    if kind == "added":
        return key + " was set to '" + str(new) + "'."
    return key + " changed: '" + str(old) + "' -> '" + str(new) + "'."


def suggest_fix(difference):
    """
    What to do about the top-ranked change.
    """
    kind = difference["kind"]
    key = difference["key"]

    if key.startswith("env.") and kind == "removed":
        name = key[len("env."):]
        return "Add " + name + " back - check your .env file, the -e flags, or the environment: section in docker-compose.yml."
    if key.startswith("env."):
        return "Check the new value of " + key[len("env."):] + " in your .env file or docker-compose.yml."
    if key.startswith("build: "):
        step = key[len("build: "):]
        if kind == "removed":
            return "Your Dockerfile no longer has '" + step + "'. Add it back and rebuild the image."
        if kind == "added":
            return "The new Dockerfile step '" + step + "' probably broke the image. Remove it and rebuild to confirm."
        return "The files from '" + step + "' changed since the last working build. The bug is most likely in that change."
    if key == "image.id":
        return "The new image build is the most likely cause. Check what changed in your code or Dockerfile since the last build."
    if key.startswith("mount."):
        return "Check the -v / volumes: path - the folder may have moved or been renamed."
    if key.startswith("port."):
        return "Check the -p / ports: setting."
    if key.startswith("limit."):
        return "Restore the previous limit, or raise it."
    if key == "command" or key == "entrypoint":
        return ("The container now starts with '" + str(difference["new"]) + "' (it was '" + str(difference["old"]) + "'). "
                "Check the CMD / ENTRYPOINT line in your Dockerfile - is the file name right, and is the file copied into the image?")
    return "Undo this change and run again to confirm it is the cause."


def remove_rebuild_noise(differences):
    """
    When one Dockerfile step changes, Docker rebuilds every step after it,
    so those later steps produce new files too. Only the FIRST change is
    the real cause, so the later ones are dropped:

      - if steps were added/removed, all "same step, different files"
        entries are dropped (the added/removed step explains them)
      - otherwise only the first "different files" step is kept
      - "image rebuilt" is dropped when a Dockerfile step explains it
    """
    step_added_or_removed = False

    for difference in differences:
        if difference["key"].startswith("build: ") and difference["kind"] != "changed":
            step_added_or_removed = True

    kept = []
    first_changed_step_seen = False
    step_kept = False

    for difference in differences:
        key = difference["key"]
        kind = difference["kind"]

        if key.startswith("build: ") and kind == "changed":
            if difference["new"] == UNKNOWN_FILES or difference["old"] == UNKNOWN_FILES:
                continue
            if step_added_or_removed:
                continue
            if first_changed_step_seen:
                continue
            first_changed_step_seen = True

        if key.startswith("build: "):
            step_kept = True

        kept.append(difference)

    if not step_kept:
        return kept

    without_image_id = []
    for difference in kept:
        if difference["key"] != "image.id":
            without_image_id.append(difference)

    return without_image_id


def explain(differences, evidence, good_time):
    """
    Step 5: turn the ranked changes into one finding for the ticket.
    """
    worked_at = good_time.replace("T", " ")[:16]

    differences = remove_rebuild_noise(differences)

    if len(differences) == 0:
        return {
            "cause": "It worked at " + worked_at + " with exactly the same container settings - nothing changed.",
            "fix": "The cause is probably inside the app (code, data) or an outside service it depends on.",
            "command": "",
            "details": [],
        }

    ranked = rank(differences, evidence)

    details = []
    for difference in ranked[:MAX_CHANGES_SHOWN]:
        details.append(describe(difference))

    hidden = len(ranked) - MAX_CHANGES_SHOWN
    if hidden > 0:
        details.append("... and " + str(hidden) + " more change(s).")

    count = str(len(ranked))
    return {
        "cause": "It worked at " + worked_at + ". Since then " + count + " setting(s) changed, most likely cause first:",
        "fix": suggest_fix(ranked[0]),
        "command": "",
        "details": details,
    }
