"""Diagnosis engine: matches an Incident against YAML rules (first match wins)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .models import Diagnosis, Incident

DEFAULT_RULES = Path(__file__).with_name("rules.yaml")

UNKNOWN = {
    "name": "unknown",
    "cause": "Unrecognised failure ({signal})",
    "explanation": "No rule matched this failure.",
    "action": "report",
    "fix_hint": "Read the pod logs and events; consider adding a new rule.",
}


class _SafeDict(dict):
    """str.format_map helper: leaves unknown {placeholders} visible instead of crashing."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def load_rules(path: str | Path | None = None) -> list[dict[str, Any]]:
    with open(path or DEFAULT_RULES, encoding="utf-8") as fh:
        return (yaml.safe_load(fh) or {}).get("rules", [])


def _matches(cond: dict[str, Any], inc: Incident, new_revision: bool) -> bool:
    if "waiting_reason_in" in cond and inc.waiting_reason not in cond["waiting_reason_in"]:
        return False
    if "last_reason_in" in cond and inc.last_reason not in cond["last_reason_in"]:
        return False
    if "exit_code_in" in cond and inc.last_exit_code not in cond["exit_code_in"]:
        return False
    if "log_contains_any" in cond:
        text = inc.logs or ""
        if not any(needle in text for needle in cond["log_contains_any"]):
            return False
    if "new_revision" in cond and bool(cond["new_revision"]) != new_revision:
        return False
    return True


def diagnose(
    inc: Incident,
    rules: list[dict[str, Any]],
    new_revision: bool = False,
) -> Diagnosis:
    """Return the Diagnosis of the first rule that matches, or an 'unknown' one."""
    chosen = next((r for r in rules if _matches(r.get("when") or {}, inc, new_revision)), UNKNOWN)
    values = _SafeDict(vars(inc), signal=inc.signal)
    fmt = lambda key: str(chosen.get(key, "")).format_map(values)  # noqa: E731
    return Diagnosis(
        rule=chosen["name"],
        cause=fmt("cause"),
        explanation=fmt("explanation"),
        action=chosen.get("action", "report"),
        fix_hint=fmt("fix_hint"),
    )
