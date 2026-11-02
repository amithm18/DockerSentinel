"""Engine: the decide -> act -> verify loop. The heart of DockSentinel."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from . import actions
from .alerts import Alerter
from .collector import build_incident, failing_container, owner_replicaset
from .diagnosis import diagnose
from .kube import MANAGED_LABEL, REVISION_ANNOTATION
from .models import Diagnosis, Incident, Outcome
from .store import Store


@dataclass
class Settings:
    dry_run: bool = False
    all_deployments: bool = False   # ignore the opt-in label
    verify_seconds: int = 30         # how long a fix must stay healthy
    rollout_timeout: int = 120       # max wait for a rollout to finish
    max_actions_per_hour: int = 3    # retry limit per Deployment
    max_memory_raises: int = 2       # more OOMs after this = likely a leak
    memory_cap: str = "1Gi"
    cooldown_seconds: int = 60       # ignore a Deployment right after acting on it
    report_cooldown_seconds: int = 300


class Engine:
    def __init__(self, kube: Any, store: Store, rules: list[dict], alerter: Alerter,
                 settings: Settings | None = None, clock=time.monotonic, sleep=time.sleep):
        self.kube, self.store, self.rules, self.alerter = kube, store, rules, alerter
        self.s = settings or Settings()
        self.clock, self.sleep = clock, sleep
        self._quiet_until: dict[tuple, float] = {}
        self._healthy_since: dict[tuple, float] = {}

    # ------------------------------------------------------------------ helpers
    def _managed(self, dep: dict) -> bool:
        if self.s.all_deployments:
            return True
        labels = (dep.get("metadata") or {}).get("labels") or {}
        return labels.get(MANAGED_LABEL) == "true"

    def _quiet(self, key: tuple) -> bool:
        return self.clock() < self._quiet_until.get(key, 0)

    def _hush(self, key: tuple, seconds: int) -> None:
        self._quiet_until[key] = self.clock() + seconds

    # --------------------------------------------------- learning good revisions
    def refresh_good_revisions(self, namespace: str | None) -> None:
        """A revision becomes 'last good' once fully healthy for verify_seconds."""
        for dep in self.kube.deployments(namespace):
            if not self._managed(dep):
                continue
            ns, name = dep["metadata"]["namespace"], dep["metadata"]["name"]
            rev = actions.deployment_revision(dep)
            key = (ns, name, rev)
            if rev is None or not actions.is_fully_healthy(dep):
                self._healthy_since = {k: v for k, v in self._healthy_since.items()
                                       if k[:2] != (ns, name)}
                continue
            since = self._healthy_since.setdefault(key, self.clock())
            if self.clock() - since >= self.s.verify_seconds and \
                    self.store.good_revision(ns, name) != rev:
                self.store.mark_good(ns, name, rev)
                self.alerter.info(f"{ns}/{name}: revision {rev} verified healthy (last good)")

    # ------------------------------------------------------------- main entry
    def handle_pod(self, pod: dict) -> Outcome | None:
        rs = owner_replicaset(pod)
        if not rs:
            return None
        ns = pod["metadata"]["namespace"]
        dep_name, rev = self.kube.owner_deployment(ns, rs)
        if not dep_name or self._quiet((ns, dep_name)):
            return None
        dep = self.kube.deployment(ns, dep_name)
        if not self._managed(dep):
            return None
        inc = build_incident(pod, dep_name, rev)
        if inc is None:
            return None
        inc.logs = self.kube.logs(ns, inc.pod, inc.container)

        good = self.store.good_revision(ns, dep_name)
        new_revision = good is not None and rev is not None and rev > good
        dx = diagnose(inc, self.rules, new_revision=new_revision)

        report_key = (ns, dep_name, dx.rule)
        if dx.action == "report" and self._quiet(report_key):
            return None

        out = self._act(inc, dx, dep, good)
        self.store.record(inc, dx, out)
        self.alerter.send(inc, dx, out)
        if out.status == "reported":
            self._hush(report_key, self.s.report_cooldown_seconds)
        else:
            self._hush((ns, dep_name), self.s.cooldown_seconds)
        return out

    # ------------------------------------------------------------------ act
    def _act(self, inc: Incident, dx: Diagnosis, dep: dict, good: int | None) -> Outcome:
        ns, name = inc.namespace, inc.deployment or ""
        if dx.action == "report":
            return Outcome("report", "reported", "Not safe to auto-fix; a human must act.")

        if self.store.actions_since(ns, name, 60) >= self.s.max_actions_per_hour:
            return Outcome(dx.action, "reported",
                           f"Retry limit reached ({self.s.max_actions_per_hour} actions in the "
                           "last hour); stopping automatic recovery.")

        if dx.action == "raise_memory":
            raised = self.store.actions_since(ns, name, 60, action="raise_memory")
            if raised >= self.s.max_memory_raises:
                return Outcome("raise_memory", "reported",
                               f"Still out of memory after {raised} increases: likely a memory "
                               "leak in the application. Raising further would only hide it.")
            new = actions.next_memory_limit(inc.memory_limit, self.s.memory_cap)
            if new is None:
                return Outcome("raise_memory", "reported",
                               f"Memory limit {inc.memory_limit or '(none)'} cannot be raised "
                               f"further (cap {self.s.memory_cap}).")
            detail = f"Memory limit {inc.memory_limit} -> {new}"
            if self.s.dry_run:
                return Outcome("raise_memory", "dry_run", detail)
            self.kube.set_memory_limit(ns, name, inc.container, new)
            return self._verify(ns, name, "raise_memory", detail)

        if dx.action == "rollback":
            current = actions.deployment_revision(dep)
            if good is None or (current is not None and good >= current):
                return Outcome("rollback", "reported",
                               "No earlier revision verified as healthy, so there is nothing "
                               "safe to roll back to.")
            tpl = actions.rollback_template(self.kube.replicasets(ns, name), good)
            if tpl is None:
                return Outcome("rollback", "reported",
                               f"Revision {good} no longer exists in the cluster.")
            detail = f"Rolled back from revision {current} to last good revision {good}"
            if self.s.dry_run:
                return Outcome("rollback", "dry_run", detail)
            self.kube.set_pod_template(ns, name, tpl)
            return self._verify(ns, name, "rollback", detail)

        if dx.action == "restart":
            if self.s.dry_run:
                return Outcome("restart", "dry_run", "Rolling restart")
            self.kube.restart(ns, name)
            return self._verify(ns, name, "restart", "Rolling restart")

        return Outcome(dx.action, "reported", f"Unknown action '{dx.action}'.")

    def _new_pods_failing(self, ns: str, name: str, dep: dict) -> bool:
        """True if any pod of the Deployment's newest revision is crashing."""
        rev = actions.deployment_revision(dep)
        for rs in self.kube.replicasets(ns, name):
            meta = rs.get("metadata") or {}
            if str((meta.get("annotations") or {}).get(REVISION_ANNOTATION)) != str(rev):
                continue
            h = (meta.get("labels") or {}).get("pod-template-hash")
            if h and any(failing_container(p) for p in
                         self.kube.pods(ns, f"pod-template-hash={h}")):
                return True
        return False

    # --------------------------------------------------------------- verify
    def _verify(self, ns: str, name: str, action: str, detail: str) -> Outcome:
        """Recovery only counts if the Deployment is fully healthy and STAYS healthy."""
        start = self.clock()
        self.alerter.info(f"{ns}/{name}: {detail}; verifying for {self.s.verify_seconds}s ...")
        deadline = start + self.s.rollout_timeout
        healthy_since = None
        while self.clock() < deadline:
            dep = self.kube.deployment(ns, name)
            if actions.is_fully_healthy(dep):
                healthy_since = healthy_since or self.clock()
                if self.clock() - healthy_since >= self.s.verify_seconds:
                    rev = actions.deployment_revision(dep)
                    if rev is not None:
                        self.store.mark_good(ns, name, rev)
                    took = healthy_since - start
                    return Outcome(action, "recovered",
                                   f"{detail}; healthy for {self.s.verify_seconds}s "
                                   f"(revision {rev} marked good)", recovery_seconds=took)
            else:
                healthy_since = None
                if self.clock() - start >= 20 and self._new_pods_failing(ns, name, dep):
                    return Outcome(action, "failed",
                                   f"{detail}; but the new pods are still failing.")
            self.sleep(3)
        return Outcome(action, "failed",
                       f"{detail}; but the Deployment did not stay healthy within "
                       f"{self.s.rollout_timeout}s.", recovery_seconds=None)
