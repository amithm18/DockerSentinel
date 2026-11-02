"""Plain data objects passed between the pipeline stages.

Pipeline:  watcher -> collector (Incident) -> diagnosis (Diagnosis)
           -> engine decides -> actions -> verifier -> store + alerts
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Incident:
    """Everything we know about one failing pod, gathered by the collector."""

    namespace: str
    pod: str
    container: str
    deployment: str | None
    revision: int | None          # Deployment revision of the failing pod's ReplicaSet
    phase: str                    # Pod phase: Pending / Running / Failed ...
    waiting_reason: str | None    # e.g. CrashLoopBackOff, ImagePullBackOff
    waiting_message: str | None
    last_reason: str | None       # e.g. OOMKilled, Error
    last_exit_code: int | None
    restart_count: int
    image: str | None
    memory_limit: str | None
    logs: str = ""
    detected_at: datetime = field(default_factory=now_utc)

    @property
    def signal(self) -> str:
        """Short human summary of what Kubernetes reported."""
        parts = [p for p in (self.waiting_reason, self.last_reason) if p]
        if self.last_exit_code is not None:
            parts.append(f"exit {self.last_exit_code}")
        return ", ".join(parts) or self.phase


@dataclass
class Diagnosis:
    """Result of matching an Incident against the rules."""

    rule: str          # rule name that matched, e.g. "out_of_memory"
    cause: str         # one-line cause in plain English
    explanation: str   # why we think so
    action: str        # raise_memory | rollback | restart | report
    fix_hint: str      # what a human should do if we cannot fix it


@dataclass
class Outcome:
    """What the engine did and whether it worked."""

    action: str
    status: str        # recovered | failed | reported | skipped | dry_run
    detail: str
    recovery_seconds: float | None = None
