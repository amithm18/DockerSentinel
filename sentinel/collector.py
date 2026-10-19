"""
Collector.

Everything that talks to Docker lives here:
  - get_evidence()          collects state + logs of one container
  - find_current_problems() checks all containers right now (one-off)
  - watch_events()          waits for containers that crash while running
  - scan_failed_starts()    finds containers that never managed to start

Problems are handed to a callback function `on_problem(trigger, evidence)`,
so this file does not decide what happens next (print, save, notify...).
"""

import time

import docker

from sentinel import changes


LOG_LINES = 30
SCAN_INTERVAL = 5


def get_build_steps(client, image_id):
    """
    Read the image's build history (one entry per Dockerfile step) and its
    file layers, and turn them into snapshot keys. Returns {} if the image
    cannot be read, so a missing image never stops the diagnosis.
    """
    try:
        history = client.api.history(image_id)
        layers = client.api.inspect_image(image_id)["RootFS"]["Layers"]
    except docker.errors.APIError:
        return {}

    return changes.build_steps(history, layers)


def get_evidence(client, container_id):
    """
    Collect everything we know about one container.

    Returns None if the container no longer exists (for example it was
    started with --rm). Logs can fail to load while the container is
    being removed, in that case we continue with empty logs.
    """
    try:
        container = client.containers.get(container_id)
    except docker.errors.NotFound:
        return None

    state = container.attrs["State"]

    try:
        raw_logs = container.logs(tail=LOG_LINES)
        logs = raw_logs.decode("utf-8", errors="replace")
    except docker.errors.APIError:
        logs = ""

    health = ""
    if "Health" in state:
        health = state["Health"]["Status"]

    evidence = {
        "id": container.id,
        "name": container.name,
        "image": container.attrs["Config"]["Image"],
        "status": state["Status"],
        "exit_code": state["ExitCode"],
        "oom_killed": state["OOMKilled"],
        "error": state["Error"],
        "health": health,
        "restart_count": container.attrs["RestartCount"],
        "finished_at": state["FinishedAt"],
        "logs": logs,
        "app_key": changes.app_key(container.attrs),
        "snapshot": changes.flatten(container.attrs, get_build_steps(client, container.attrs["Image"])),
    }

    return evidence


def evidence_from_event(event):
    """
    Fallback when the container is already gone (--rm).
    The event itself still tells us the name, image and exit code.
    """
    attributes = event["Actor"]["Attributes"]

    evidence = {
        "id": event["Actor"]["ID"],
        "name": attributes.get("name", ""),
        "image": attributes.get("image", ""),
        "status": "removed",
        "exit_code": int(attributes.get("exitCode", "0")),
        "oom_killed": False,
        "error": "",
        "health": "",
        "restart_count": 0,
        "finished_at": str(event["timeNano"]),
        "logs": "",
        "app_key": None,
        "snapshot": None,
    }

    return evidence


def get_trigger(evidence):
    """
    Decide if a container is in a problem state, and name the problem.
    Returns None when the container is fine.

    Exit code 143 means it was stopped normally with 'docker stop',
    so it is not treated as a problem.
    """
    status = evidence["status"]
    exit_code = evidence["exit_code"]

    if status == "created" and evidence["error"] != "":
        return "failed to start"

    if status == "exited" and exit_code != 0 and exit_code != 143:
        return "crashed"

    if status == "restarting":
        return "restart loop"

    if evidence["health"] == "unhealthy":
        return "unhealthy"

    return None


def find_current_problems(client):
    """
    Look at every container once and return a list of (trigger, evidence)
    for the ones that have a problem.
    """
    problems = []
    containers = client.containers.list(all=True)

    for container in containers:
        evidence = get_evidence(client, container.id)
        if evidence is None:
            continue

        trigger = get_trigger(evidence)
        if trigger is None:
            continue

        problems.append((trigger, evidence))

    return problems


def event_trigger(event):
    """
    Decide if a Docker event means something went wrong.

    The "oom" event is handled separately in watch_events(), because it
    can mean two different things (see handle_oom_event).
    """
    action = event["Action"]
    attributes = event["Actor"]["Attributes"]

    if action == "die":
        exit_code = attributes.get("exitCode", "0")
        if exit_code == "0" or exit_code == "143":
            return None
        return "crashed"

    if action == "health_status: unhealthy":
        return "unhealthy"

    return None


def handle_oom_event(client, event, on_problem):
    """
    An "oom" event means the kernel killed a process for using too much memory.

      - If it was the main process, the container dies and the "die" event
        that follows reports it (with OOMKilled = True). Nothing to do here.
      - If it was a child process (e.g. one web server worker), the container
        KEEPS RUNNING and looks fine in `docker ps`. Only this event tells us.

    We wait one second so Docker has time to stop the container if the
    main process was killed, then check if it is still running.
    """
    time.sleep(1)

    container_id = event["Actor"]["ID"]
    evidence = get_evidence(client, container_id)

    if evidence is None:
        return

    if evidence["status"] != "running":
        return

    evidence["oom_killed"] = True
    evidence["finished_at"] = str(event["timeNano"])
    on_problem("process killed (OOM)", evidence)


def watch_events(client, on_problem, on_start, on_exit):
    """
    Wait for Docker events forever.

      start -> on_start(container_id)            (to save a snapshot)
      die   -> on_exit(container_id, exit_code)  (to mark the snapshot good/bad)
      crash or unhealthy -> on_problem(trigger, evidence)

    Docker sends a "kill" event when a USER stops or kills a container
    (docker stop, docker kill, docker rm -f). The "die" that follows is
    then not a crash, so it is skipped.
    """
    filters = {"type": "container"}
    killed_by_user = set()

    for event in client.events(decode=True, filters=filters):
        container_id = event["Actor"]["ID"]

        if event["Action"] == "kill":
            killed_by_user.add(container_id)
            continue

        if event["Action"] == "oom":
            handle_oom_event(client, event, on_problem)
            continue

        if event["Action"] == "start":
            killed_by_user.discard(container_id)
            on_start(container_id)

        if event["Action"] == "die":
            if container_id in killed_by_user:
                killed_by_user.discard(container_id)
                continue

            exit_code = int(event["Actor"]["Attributes"].get("exitCode", "0"))
            on_exit(container_id, exit_code)

        trigger = event_trigger(event)
        if trigger is None:
            continue

        evidence = get_evidence(client, container_id)
        if evidence is None:
            evidence = evidence_from_event(event)

        on_problem(trigger, evidence)


def scan_failed_starts(on_problem, on_tick):
    """
    Every few seconds, look for containers that never managed to start.

    Docker sends no event for these: they stay in the "created" state and
    the reason is stored in State.Error. This runs in its own thread, so
    it opens its own Docker connection.

    on_tick(client) is called on every round, for other periodic work.
    """
    client = docker.from_env()

    while True:
        on_tick(client)

        filters = {"status": "created"}
        containers = client.containers.list(all=True, filters=filters)

        for container in containers:
            evidence = get_evidence(client, container.id)
            if evidence is None:
                continue

            if evidence["error"] == "":
                continue

            on_problem("failed to start", evidence)

        time.sleep(SCAN_INTERVAL)
