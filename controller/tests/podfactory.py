"""Builds pods in the exact JSON shape `kubectl get pod -o json` returns."""
from __future__ import annotations

import copy


def make_pod(name="web-abc12", ns="demo", rs="web-7d9f", container="web",
             image="nginx:1.27-alpine", memory="128Mi", ready=True,
             waiting=None, waiting_msg=None, last=None, last_exit=None,
             terminated=None, restarts=0, labels=None):
    state = {"running": {"startedAt": "2026-11-02T10:00:00Z"}}
    if waiting:
        state = {"waiting": {"reason": waiting, **({"message": waiting_msg} if waiting_msg else {})}}
    if terminated:
        state = {"terminated": {"reason": terminated, "exitCode": last_exit}}
    cs = {
        "name": container, "image": image, "imageID": "", "ready": ready, "restartCount": restarts,
        "state": state, "lastState": {},
    }
    if last:
        cs["lastState"] = {"terminated": {"reason": last, "exitCode": last_exit}}
    return {
        "apiVersion": "v1", "kind": "Pod",
        "metadata": {
            "name": name, "namespace": ns, "labels": labels or {"app": name.split("-")[0]},
            "ownerReferences": [{"apiVersion": "apps/v1", "kind": "ReplicaSet", "name": rs, "uid": "uid-" + rs}],
        },
        "spec": {"containers": [{"name": container, "image": image,
                                 "resources": {"limits": {"memory": memory}}}]},
        "status": {"phase": "Running", "containerStatuses": [cs]},
    }


def make_deployment(name="web", ns="demo", revision=1, replicas=2, healthy=True,
                    managed=True, template=None):
    st = {"observedGeneration": 1, "replicas": replicas}
    if healthy:
        st.update(updatedReplicas=replicas, availableReplicas=replicas, readyReplicas=replicas)
    else:
        st.update(updatedReplicas=1, availableReplicas=replicas, readyReplicas=replicas,
                  replicas=replicas + 1)
    return {
        "metadata": {"name": name, "namespace": ns, "generation": 1,
                     "labels": {"docksentinel.io/enabled": "true"} if managed else {},
                     "annotations": {"deployment.kubernetes.io/revision": str(revision)}},
        "spec": {"replicas": replicas, "selector": {"matchLabels": {"app": name}},
                 "template": copy.deepcopy(template or {"metadata": {"labels": {"app": name}},
                                                        "spec": {"containers": []}})},
        "status": st,
    }


def make_rs(name, revision, image, dep="web", h="hash"):
    return {
        "metadata": {"name": name,
                     "labels": {"app": dep, "pod-template-hash": h},
                     "annotations": {"deployment.kubernetes.io/revision": str(revision)},
                     "ownerReferences": [{"apiVersion": "apps/v1", "kind": "Deployment", "name": dep, "uid": "uid-" + dep}]},
        "spec": {"selector": {"matchLabels": {"app": dep}},
                 "template": {"metadata": {"labels": {"app": dep, "pod-template-hash": h}},
                              "spec": {"containers": [{"name": "web", "image": image}]}}},
    }
