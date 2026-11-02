"""Alerts: a readable incident card on the console, plus optional Telegram."""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

from .models import Diagnosis, Incident, Outcome

ICONS = {"recovered": "[RECOVERED]", "failed": "[FAILED]", "reported": "[NEEDS HUMAN]",
         "skipped": "[SKIPPED]", "dry_run": "[DRY RUN]"}


def render(inc: Incident, dx: Diagnosis, out: Outcome) -> str:
    line = "=" * 64
    last_logs = "\n".join("    " + l for l in (inc.logs or "").strip().splitlines()[-5:])
    took = f" in {out.recovery_seconds:.0f}s" if out.recovery_seconds else ""
    return "\n".join([
        line,
        f"{ICONS.get(out.status, out.status)}  {inc.namespace}/{inc.deployment}  (pod {inc.pod})",
        line,
        f"  Kubernetes says : {inc.signal}  (restarts: {inc.restart_count})",
        f"  Root cause      : {dx.cause}",
        f"  Why             : {dx.explanation}",
        f"  Action          : {out.action} -> {out.status}{took}",
        f"  Detail          : {out.detail}",
        f"  How to fix      : {dx.fix_hint}",
        *(["  Last log lines  :", last_logs] if last_logs else []),
        line,
    ])


class Alerter:
    def __init__(self) -> None:
        self.token = os.environ.get("TELEGRAM_BOT_TOKEN")
        self.chat = os.environ.get("TELEGRAM_CHAT_ID")

    def send(self, inc: Incident, dx: Diagnosis, out: Outcome) -> None:
        text = render(inc, dx, out)
        print(text, flush=True)
        if self.token and self.chat:
            self._telegram(text)

    def info(self, msg: str) -> None:
        print(f"[docksentinel] {msg}", flush=True)

    def _telegram(self, text: str) -> None:
        data = urllib.parse.urlencode({"chat_id": self.chat, "text": text}).encode()
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        try:
            urllib.request.urlopen(url, data=data, timeout=10).read()
        except Exception as exc:  # alerts must never crash the controller
            print(f"[docksentinel] telegram alert failed: {exc}")


def to_json(inc: Incident, dx: Diagnosis, out: Outcome) -> str:
    return json.dumps({"incident": {**vars(inc), "detected_at": inc.detected_at.isoformat()},
                       "diagnosis": vars(dx), "outcome": vars(out)}, indent=2)
