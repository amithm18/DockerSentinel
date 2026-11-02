"""Collector: turns a raw Kubernetes Pod into an Incident.

Works on the same JSON shape that `kubectl get pod -o json` prints (camelCase),
so recorded kubectl output can be used directly as test fixtures.
"""
from __future__ import annotations

from typing import Any

from .models import Incident

# Waiting reasons that mean "this container is broken", not "still starting".
FAILING_WAITING_REASONS = {
    "CrashLoopBackOff",
    "ImagePullBackOff",
    "ErrImagePull",
    "InvalidImageName",
    "CreateContainerConfigError",
    "CreateContainerError",
    "RunContainerError",
}


def _statuses(pod: dict[str, Any]) -> list[dict[str, Any]]:
    return (pod.get("status") or {}).get("containerStatuses") or []


def failing_container(pod: dict[str, Any]) -> dict[str, Any] | None:
    """Return the status of the first failing container, or None if the pod is fine."""
    for cs in _statuses(pod):
        state = cs.get("state") or {}
        waiting = state.get("waiting") or {}
        terminated = state.get("terminated") or {}
        last = (cs.get("lastState") or {}).get("terminated") or {}
        if waiting.get("reason") in FAILING_WAITING_REASONS:
            return cs
        if terminated.get("reason") == "OOMKilled" or (
            last.get("reason") == "OOMKilled" and not cs.get("ready")
        ):
            return cs
    return None


def owner_replicaset(pod: dict[str, Any]) -> str | None:
    for ref in (pod.get("metadata") or {}).get("ownerReferences") or []:
        if ref.get("kind") == "ReplicaSet":
            return ref.get("name")
    return None


def _memory_limit(pod: dict[str, Any], container: str) -> str | None:
    for c in (pod.get("spec") or {}).get("containers") or []:
        if c.get("name") == container:
            return ((c.get("resources") or {}).get("limits") or {}).get("memory")
    return None


def build_incident(
    pod: dict[str, Any],
    deployment: str | None,
    revision: int | None,
    logs: str = "",
) -> Incident | None:
    """Build an Incident for a failing pod (None if the pod is healthy)."""
    cs = failing_container(pod)
    if cs is None:
        return None
    meta = pod.get("metadata") or {}
    state = cs.get("state") or {}
    waiting = state.get("waiting") or {}
    terminated = state.get("terminated") or {}
    last = (cs.get("lastState") or {}).get("terminated") or terminated
    exit_code = last.get("exitCode")
    return Incident(
        namespace=meta.get("namespace", "default"),
        pod=meta.get("name", "?"),
        container=cs.get("name", "?"),
        deployment=deployment,
        revision=revision,
        phase=(pod.get("status") or {}).get("phase", "Unknown"),
        waiting_reason=waiting.get("reason"),
        waiting_message=waiting.get("message"),
        last_reason=last.get("reason"),
        last_exit_code=int(exit_code) if exit_code is not None else None,
        restart_count=int(cs.get("restartCount") or 0),
        image=cs.get("image"),
        memory_limit=_memory_limit(pod, cs.get("name", "")),
        logs=logs,
    )
