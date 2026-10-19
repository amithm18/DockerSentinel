"""
Demo.

`sentinel demo` shows what Sentinel does in about 10 seconds, with
nothing to set up:

  1. runs a tiny fake app that needs an API_KEY setting - it works
  2. runs it again WITHOUT API_KEY - it crashes
  3. prints Sentinel's ticket, which points to the missing API_KEY

The fake app is just a one-line shell script inside the small "alpine"
image. The demo container is removed at the end.
"""

import time
from datetime import datetime

import docker

from sentinel import collector
from sentinel import store
from sentinel import tracker
from sentinel.ticket import print_ticket


DEMO_NAME = "sentinel-demo"
DEMO_IMAGE = "alpine"

FAKE_APP = (
    'if [ -z "$API_KEY" ]; then echo "Error: API_KEY is missing"; exit 1; fi; '
    'echo "demo app is running"; sleep 3600'
)


def remove_demo_container(client):
    try:
        container = client.containers.get(DEMO_NAME)
        container.remove(force=True)
    except docker.errors.NotFound:
        pass


def start_demo_app(client, environment):
    container = client.containers.run(
        DEMO_IMAGE,
        ["sh", "-c", FAKE_APP],
        name=DEMO_NAME,
        environment=environment,
        detach=True,
    )
    tracker.record_start(client, container.id)
    return container


def wait_until_stopped(container, seconds):
    """
    Check twice a second until the container is no longer running.
    """
    checks = seconds * 2
    for _ in range(checks):
        container.reload()
        if container.status != "running":
            return
        time.sleep(0.5)


def run_demo(client):
    print("Sentinel demo - nothing to set up. It runs a tiny fake app in Docker.")
    print()

    remove_demo_container(client)

    try:
        print("Step 1/3  Starting the demo app WITH its API_KEY setting...")
        container = start_demo_app(client, {"API_KEY": "abc123"})
        time.sleep(2)
        container.reload()

        if container.status != "running":
            print("          The demo app did not start, so the demo cannot continue.")
            return

        store.mark_snapshot(container.id, "good")
        print("          It works. Sentinel saved this as the last good run.")
        print()

        print("Step 2/3  Starting it again, but 'forgetting' API_KEY...")
        remove_demo_container(client)
        container = start_demo_app(client, {})
        wait_until_stopped(container, 10)
        container.reload()
        tracker.record_exit(container.id, container.attrs["State"]["ExitCode"])
        print("          It crashed.")
        print()

        print("Step 3/3  Sentinel's diagnosis:")
        print()

        evidence = collector.get_evidence(client, container.id)
        findings = tracker.diagnose_with_history(evidence)
        when = datetime.now().strftime("%H:%M:%S")

        print_ticket(
            "DEMO",
            when,
            "crashed",
            evidence["name"],
            evidence["image"],
            evidence["exit_code"],
            evidence["error"],
            findings,
            evidence["logs"],
        )

    finally:
        remove_demo_container(client)

    print("Demo container removed.")
    print("Run 'sentinel watch' to get tickets like this for your own containers.")
