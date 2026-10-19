"""
Tracker.

Keeps the snapshot history up to date, so "what changed since it last
worked?" can be answered when something fails.

Life of a snapshot:

    container starts      -> snapshot saved as "candidate"
    runs 15+ seconds OK   -> "good"   (healthy, if it has a health check)
    exits with 0 / 143    -> "good"   (finished normally, or stopped by the user)
    crashes before that   -> "bad"

When a container fails, its settings are compared with the last "good"
snapshot of the same app.
"""

from datetime import datetime

import docker

from sentinel import changes
from sentinel import collector
from sentinel import rules
from sentinel import store


GOOD_AFTER_SECONDS = 15


def record_start(client, container_id):
    """
    Called on every "start" event: save the container's settings.
    """
    try:
        container = client.containers.get(container_id)
    except docker.errors.NotFound:
        return

    key = changes.app_key(container.attrs)
    build = collector.get_build_steps(client, container.attrs["Image"])
    data = changes.flatten(container.attrs, build)
    store.save_snapshot(key, container_id, data)


def record_exit(container_id, exit_code):
    """
    Called on every "die" event.
    """
    if exit_code == 0 or exit_code == 143:
        store.mark_snapshot(container_id, "good")
    else:
        store.mark_snapshot(container_id, "bad")


def record_running_containers(client):
    """
    When Sentinel starts, containers that are already running have no
    snapshot yet. Save them as candidates so they can become "good".
    """
    containers = client.containers.list()
    for container in containers:
        record_start(client, container.id)


def is_running_well(state):
    """
    Running, and not failing (or still waiting for) its health check.
    """
    if not state["Running"]:
        return False

    if "Health" in state:
        if state["Health"]["Status"] != "healthy":
            return False

    return True


def promote_candidates(client):
    """
    Called every few seconds: candidates that have been running well for
    GOOD_AFTER_SECONDS become "good".
    """
    now = datetime.now()

    for candidate in store.list_candidates():
        started = datetime.fromisoformat(candidate["taken_at"])
        age = (now - started).total_seconds()
        if age < GOOD_AFTER_SECONDS:
            continue

        container_id = candidate["container_id"]

        try:
            container = client.containers.get(container_id)
        except docker.errors.NotFound:
            store.mark_snapshot(container_id, "unknown")
            continue

        state = container.attrs["State"]

        if is_running_well(state):
            store.mark_snapshot(container_id, "good")
        elif not state["Running"]:
            record_exit(container_id, state["ExitCode"])


def find_changes(evidence):
    """
    Compare a failing container with the last good run of the same app.

    Returns a finding for the ticket, or None if there is no earlier
    good run to compare with (for example the very first run).
    """
    app_key = evidence["app_key"]
    data = evidence["snapshot"]

    if data is None:
        saved = store.get_snapshot(evidence["id"])
        if saved is None:
            return None
        app_key = saved["app_key"]
        data = saved["data"]

    good = store.last_good_snapshot(app_key)
    if good is None:
        return None

    differences = changes.compare(good["data"], data)
    return changes.explain(differences, evidence, good["taken_at"])


def diagnose_with_history(evidence):
    """
    The normal rules, plus "what changed since it last worked?" on top
    when there is an earlier good run to compare with.

    If real changes were found, the generic "not a known pattern"
    finding is dropped - the changes are the better explanation.
    """
    findings = rules.diagnose(evidence)

    change_finding = find_changes(evidence)
    if change_finding is None:
        return findings

    result = [change_finding]
    for finding in findings:
        is_generic = finding.get("generic", False)
        if is_generic and len(change_finding["details"]) > 0:
            continue
        result.append(finding)

    return result
