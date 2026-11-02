"""Thin wrapper around the Kubernetes API.

Everything that talks to the cluster lives here, so the rest of DockSentinel
works on plain dicts and can be unit-tested with a fake of this class.
"""
from __future__ import annotations

import time
from typing import Any, Iterator

from kubernetes import client, config, watch
from kubernetes.client.rest import ApiException

MANAGED_LABEL = "docksentinel.io/enabled"
REVISION_ANNOTATION = "deployment.kubernetes.io/revision"


def _to_dict(obj: Any) -> dict[str, Any]:
    """Convert a kubernetes-client object to kubectl-style camelCase JSON."""
    return client.ApiClient().sanitize_for_serialization(obj)


class Kube:
    def __init__(self, in_cluster: bool = False):
        if in_cluster:
            config.load_incluster_config()
        else:
            config.load_kube_config()
        self.core = client.CoreV1Api()
        self.apps = client.AppsV1Api()

    # -- watching ----------------------------------------------------------
    def watch_pods(self, namespace: str | None, timeout_seconds: int = 30) -> Iterator[dict]:
        """Yield pod dicts as they change. Ends after timeout_seconds (caller loops)."""
        w = watch.Watch()
        if namespace:
            stream = w.stream(self.core.list_namespaced_pod, namespace,
                              timeout_seconds=timeout_seconds)
        else:
            stream = w.stream(self.core.list_pod_for_all_namespaces,
                              timeout_seconds=timeout_seconds)
        for event in stream:
            if event["type"] in ("ADDED", "MODIFIED"):
                yield _to_dict(event["object"])

    # -- reading -----------------------------------------------------------
    def owner_deployment(self, namespace: str, rs_name: str) -> tuple[str | None, int | None]:
        """ReplicaSet name -> (Deployment name, revision)."""
        try:
            rs = _to_dict(self.apps.read_namespaced_replica_set(rs_name, namespace))
        except ApiException:
            return None, None
        meta = rs.get("metadata") or {}
        rev = (meta.get("annotations") or {}).get(REVISION_ANNOTATION)
        for ref in meta.get("ownerReferences") or []:
            if ref.get("kind") == "Deployment":
                return ref.get("name"), int(rev) if rev else None
        return None, None

    def deployment(self, namespace: str, name: str) -> dict[str, Any]:
        return _to_dict(self.apps.read_namespaced_deployment(name, namespace))

    def deployments(self, namespace: str | None) -> list[dict[str, Any]]:
        res = (self.apps.list_namespaced_deployment(namespace) if namespace
               else self.apps.list_deployment_for_all_namespaces())
        return [_to_dict(d) for d in res.items]

    def replicasets(self, namespace: str, deployment: str) -> list[dict[str, Any]]:
        out = []
        for rs in self.apps.list_namespaced_replica_set(namespace).items:
            d = _to_dict(rs)
            owners = (d.get("metadata") or {}).get("ownerReferences") or []
            if any(o.get("kind") == "Deployment" and o.get("name") == deployment for o in owners):
                out.append(d)
        return out

    def pods(self, namespace: str, label_selector: str) -> list[dict[str, Any]]:
        res = self.core.list_namespaced_pod(namespace, label_selector=label_selector)
        return [_to_dict(p) for p in res.items]

    def logs(self, namespace: str, pod: str, container: str, tail: int = 100) -> str:
        """Logs of the crashed (previous) container if any, else the current one."""
        for previous in (True, False):
            try:
                return self.core.read_namespaced_pod_log(
                    pod, namespace, container=container, previous=previous,
                    tail_lines=tail) or ""
            except ApiException:
                continue
        return ""

    # -- acting --------------------------------------------------------------
    def set_memory_limit(self, namespace: str, deployment: str, container: str,
                         limit: str) -> None:
        patch = {"spec": {"template": {"spec": {"containers": [
            {"name": container, "resources": {"limits": {"memory": limit}}}]}}}}
        self._patch(namespace, deployment, patch)

    def set_pod_template(self, namespace: str, deployment: str,
                         template: dict[str, Any]) -> None:
        """Replace the whole pod template, exactly like `kubectl rollout undo`."""
        body = {"spec": {"template": {**template, "$patch": "replace"}}}
        self._patch(namespace, deployment, body)

    def restart(self, namespace: str, deployment: str) -> None:
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        patch = {"spec": {"template": {"metadata": {"annotations": {
            "kubectl.kubernetes.io/restartedAt": stamp}}}}}
        self._patch(namespace, deployment, patch)

    def _patch(self, namespace: str, deployment: str, patch: dict) -> None:
        # Strategic merge = what kubectl uses: containers are merged by name.
        self.apps.patch_namespaced_deployment(
            deployment, namespace, patch,
            _content_type="application/strategic-merge-patch+json")
