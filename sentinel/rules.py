"""
Diagnosis rules.

Turns the evidence collected about a container into a list of findings.
Each finding is a dictionary with:
  - "cause"    what went wrong, in plain words
  - "fix"      what the user should do about it
  - "command"  an optional command to copy and paste ("" if none)

This file only does logic - it never talks to Docker and never prints,
so any front end (terminal, TUI, notifications) can reuse it.
"""


def make_finding(cause, fix, command=""):
    finding = {
        "cause": cause,
        "fix": fix,
        "command": command,
    }
    return finding


def short_error(error):
    """
    Docker errors are long chains like:
      "failed to create task: ... error during container init: exec: "x": executable file not found"
    The useful part is at the end, so keep only the last two pieces.
    """
    if error == "":
        return ""

    parts = error.split(": ")
    if len(parts) <= 2:
        return error

    last_two = parts[-2:]
    return ": ".join(last_two)


def find_port(error):
    """
    Pull the port number out of "Bind for 0.0.0.0:4000 failed: port is already allocated".
    Returns "" if there is no port in the message.
    """
    start = error.find("Bind for ")
    if start == -1:
        return ""

    end = error.find(" failed", start)
    if end == -1:
        return ""

    address = error[start + len("Bind for "):end]
    pieces = address.split(":")
    return pieces[-1]


def check_exit_code(evidence):
    """
    The exit code tells us HOW the container died.
    """
    findings = []
    exit_code = evidence["exit_code"]
    image = evidence["image"]

    if evidence["oom_killed"] and evidence["status"] == "running":
        findings.append(make_finding(
            "A process INSIDE the container ran out of memory and was killed. "
            "The container is still running, so 'docker ps' looks fine - but part of the app is gone.",
            "Raise the memory limit (--memory), or reduce the number of workers/threads. Watch live memory use:",
            "docker stats " + evidence["name"],
        ))
    elif evidence["oom_killed"]:
        findings.append(make_finding(
            "The container used more memory than it is allowed, so the kernel killed it.",
            "Give it more memory with --memory (for example --memory 512m), or find what in the app uses so much memory.",
        ))
    elif exit_code == 137:
        findings.append(make_finding(
            "The container was force-killed, not because of memory.",
            "Either someone ran 'docker kill', or 'docker stop' waited 10 seconds and gave up because the app ignores the stop signal. "
            "Adding --init to 'docker run' usually fixes the second case.",
        ))

    if exit_code == 127:
        findings.append(make_finding(
            "The command the container tries to run does not exist inside the image.",
            "Check the spelling of CMD / ENTRYPOINT. Docker options like -d must come BEFORE the image name. "
            "Open a shell in the image to see what is installed:",
            "docker run -it --rm --entrypoint sh " + image,
        ))

    if exit_code == 126:
        findings.append(make_finding(
            "The start command exists but is not allowed to run.",
            "Make the script executable in your Dockerfile (RUN chmod +x script.sh) and check its first line (#!/bin/sh).",
        ))

    if exit_code == 139:
        findings.append(make_finding(
            "The app crashed hard (segmentation fault).",
            "Usually a native library problem, or an image built for a different CPU. Check the image platform:",
            "docker image inspect " + image + " --format '{{.Architecture}}'",
        ))

    return findings


def check_messages(evidence):
    """
    Docker's error message and the app's logs tell us WHY it died.
    """
    findings = []
    text = evidence["logs"].lower() + " " + evidence["error"].lower()

    port = find_port(evidence["error"])

    if "port is already allocated" in text and port != "":
        findings.append(make_finding(
            "Port " + port + " is already used by another container.",
            "Stop that container, or publish a different port, for example -p " + str(int(port) + 1) + ":<app port>. "
            "Find who is using it:",
            "docker ps --filter publish=" + port,
        ))
    elif "address already in use" in text or "port is already allocated" in text:
        findings.append(make_finding(
            "The app tried to listen on a port that is already taken.",
            "Stop the other process using that port, or change the port the app listens on.",
        ))

    if "permission denied" in text:
        findings.append(make_finding(
            "The app was not allowed to read or write a file or folder.",
            "Check the permissions of mounted volumes and which user the container runs as (USER in the Dockerfile).",
        ))

    if "no such file or directory" in text:
        findings.append(make_finding(
            "The app looked for a file or folder that does not exist.",
            "Check the file path, the COPY steps in your Dockerfile, and your volume mounts (-v).",
        ))

    if "connection refused" in text:
        findings.append(make_finding(
            "The app could not reach another service (database, API, ...).",
            "Make sure that service is running. Inside Docker, connect using the service/container name, not 'localhost'.",
        ))

    if "modulenotfounderror" in text or "cannot find module" in text:
        findings.append(make_finding(
            "A library the app needs is not installed in the image.",
            "Add it to requirements.txt / package.json, make sure the Dockerfile installs it, then rebuild the image.",
        ))

    if "exec format error" in text:
        findings.append(make_finding(
            "The image was built for a different CPU type than this machine.",
            "Rebuild it for your machine:",
            "docker build --platform linux/amd64 -t " + evidence["image"] + " .",
        ))

    return findings


def check_health(evidence):
    """
    Restart loops and failing health checks.
    """
    findings = []
    name = evidence["name"]

    if evidence["restart_count"] > 3:
        findings.append(make_finding(
            "The container has restarted " + str(evidence["restart_count"]) + " times - it is stuck in a crash loop.",
            "Restarting will not fix it. Stop the loop while you fix the cause:",
            "docker update --restart=no " + name,
        ))

    if evidence["health"] == "unhealthy":
        findings.append(make_finding(
            "The container is running, but its health check keeps failing.",
            "See the output of the last health checks:",
            "docker inspect " + name + " --format '{{json .State.Health.Log}}'",
        ))

    return findings


def diagnose(evidence):
    """
    Run every check and return all findings.
    If nothing matched, return one general finding instead, marked
    "generic" so it can be dropped when a better explanation exists.
    """
    findings = []
    findings.extend(check_exit_code(evidence))
    findings.extend(check_messages(evidence))
    findings.extend(check_health(evidence))

    if len(findings) > 0:
        return findings

    if evidence["error"] != "":
        fallback = make_finding(
            "Docker could not start the container.",
            "Read the 'Docker says' line above - it is the reason Docker gave.",
        )
    else:
        fallback = make_finding(
            "The app stopped with exit code " + str(evidence["exit_code"]) + ". This is not a known pattern yet.",
            "The app's own error message is usually in its logs:",
            "docker logs --tail 50 " + evidence["name"],
        )

    fallback["generic"] = True
    findings.append(fallback)

    return findings
