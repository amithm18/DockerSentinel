"""Pure helper logic for recovery actions (no cluster access, easy to test)."""
from __future__ import annotations

import copy
import re
from typing import Any

from .kube import REVISION_ANNOTATION

_UNITS = {
    "": 1, "k": 1000, "M": 1000**2, "G": 1000**3,
    "Ki": 1024, "Mi": 1024**2, "Gi": 1024**3,
}


def parse_memory(value: str) -> int:
    """'128Mi' -> bytes."""
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(Ki|Mi|Gi|k|M|G)?\s*", value or "")
    if not m:
        raise ValueError(f"Unsupported memory value: {value!r}")
    return int(float(m.group(1)) * _UNITS[m.group(2) or ""])


def format_mi(num_bytes: int) -> str:
    return f"{max(1, round(num_bytes / 1024**2))}Mi"


def next_memory_limit(current: str | None, cap: str = "1Gi") -> str | None:
    """Double the memory limit, capped. None means 'already at the cap'."""
    if not current:
        return None
    new, top = parse_memory(current) * 2, parse_memory(cap)
    if parse_memory(current) >= top:
        return None
    return format_mi(min(new, top))


def deployment_revision(dep: dict[str, Any]) -> int | None:
    rev = ((dep.get("metadata") or {}).get("annotations") or {}).get(REVISION_ANNOTATION)
    return int(rev) if rev else None


def is_fully_healthy(dep: dict[str, Any]) -> bool:
    """Rollout finished and every replica of the newest revision is available."""
    spec, st = dep.get("spec") or {}, dep.get("status") or {}
    want = spec.get("replicas", 1)
    return (
        (st.get("observedGeneration") or 0) >= ((dep.get("metadata") or {}).get("generation") or 0)
        and (st.get("updatedReplicas") or 0) == want
        and (st.get("availableReplicas") or 0) == want
        and (st.get("readyReplicas") or 0) == want
        and (st.get("replicas") or 0) == want   # no old pods still around
    )


def rollback_template(replicasets: list[dict[str, Any]], revision: int) -> dict[str, Any] | None:
    """Pod template of the ReplicaSet that belongs to `revision`, ready to re-apply."""
    for rs in replicasets:
        ann = (rs.get("metadata") or {}).get("annotations") or {}
        if str(ann.get(REVISION_ANNOTATION)) == str(revision):
            tpl = copy.deepcopy((rs.get("spec") or {}).get("template") or {})
            labels = (tpl.get("metadata") or {}).get("labels") or {}
            labels.pop("pod-template-hash", None)   # Kubernetes adds this itself
            return tpl
    return None
