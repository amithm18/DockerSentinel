"""
Command line interface.

    sentinel check     check all containers right now and print problems
    sentinel watch     keep running: detect, save and print new problems
    sentinel history   show problems saved earlier
    sentinel demo      see what Sentinel does, with a fake app (about 10 seconds)

This file connects the collector (Docker), the rules (diagnosis), the
store (SQLite) and the ticket printer together.
"""

import argparse
import sys
import threading
from datetime import datetime

import docker

from sentinel import collector
from sentinel import demo
from sentinel import store
from sentinel import tracker
from sentinel.ticket import print_ticket


print_lock = threading.Lock()


def connect_to_docker():
    """
    Connect to Docker, or exit with a clear message instead of a traceback.
    """
    try:
        client = docker.from_env()
        client.ping()
    except docker.errors.DockerException as error:
        print("Could not connect to Docker.")
        print("Make sure Docker is running and your user is allowed to use it")
        print("(on Linux: add yourself to the 'docker' group).")
        print()
        print("Details: " + str(error))
        sys.exit(1)

    return client


def show(ticket_id, when, trigger, evidence, findings):
    """
    Print one ticket. The lock stops two threads from printing on top
    of each other.
    """
    print_lock.acquire()

    print_ticket(
        ticket_id,
        when,
        trigger,
        evidence["name"],
        evidence["image"],
        evidence["exit_code"],
        evidence["error"],
        findings,
        evidence["logs"],
    )

    print_lock.release()


def handle_problem(trigger, evidence):
    """
    Called by the collector for every problem it finds.
    Diagnoses it, saves it, and prints it only if it is new.
    """
    findings = tracker.diagnose_with_history(evidence)
    ticket_id, is_new = store.save_diagnosis(trigger, evidence, findings)

    if is_new:
        when = datetime.now().strftime("%H:%M:%S")
        show(ticket_id, when, trigger, evidence, findings)


def run_check():
    client = connect_to_docker()
    problems = collector.find_current_problems(client)

    if len(problems) == 0:
        print("No problems found. All containers look fine.")
        return

    print("Found " + str(len(problems)) + " container(s) with problems.")
    print()

    for trigger, evidence in problems:
        findings = tracker.diagnose_with_history(evidence)
        ticket_id, is_new = store.save_diagnosis(trigger, evidence, findings)
        when = datetime.now().strftime("%H:%M:%S")
        show(ticket_id, when, trigger, evidence, findings)


def run_watch():
    client = connect_to_docker()

    print("DockerSentinel is watching your containers... (Ctrl+C to stop)")
    print("Tickets are saved to " + store.get_db_path())
    print()

    tracker.record_running_containers(client)

    def on_start(container_id):
        tracker.record_start(client, container_id)

    scanner = threading.Thread(
        target=collector.scan_failed_starts,
        args=(handle_problem, tracker.promote_candidates),
        daemon=True,
    )
    scanner.start()

    collector.watch_events(client, handle_problem, on_start, tracker.record_exit)


def run_history(limit):
    history = store.load_history(limit)

    if len(history) == 0:
        print("No problems saved yet.")
        return

    for item in history:
        evidence = {
            "name": item["container_name"],
            "image": item["image"],
            "exit_code": item["exit_code"],
            "error": item["error"],
            "logs": item["logs"],
        }
        when = item["detected_at"].replace("T", " ")
        show(item["id"], when, item["trigger"], evidence, item["findings"])


def main():
    parser = argparse.ArgumentParser(
        prog="sentinel",
        description="Detects and diagnoses failing Docker containers.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("check", help="check all containers right now")
    commands.add_parser("watch", help="keep watching and report new problems")

    history_parser = commands.add_parser("history", help="show saved problems")
    history_parser.add_argument("-n", type=int, default=10, help="how many to show (default 10)")

    commands.add_parser("demo", help="see what Sentinel does, with a fake app (about 10 seconds)")

    arguments = parser.parse_args()

    try:
        if arguments.command == "check":
            run_check()
        elif arguments.command == "watch":
            run_watch()
        elif arguments.command == "history":
            run_history(arguments.n)
        elif arguments.command == "demo":
            demo.run_demo(connect_to_docker())
    except KeyboardInterrupt:
        print()
        print("Stopped.")


if __name__ == "__main__":
    main()
