"""Read-only AgentMail credential diagnostics. Never sends mail; never prints secret values."""
from __future__ import annotations

import os
from urllib.parse import quote

from .http import HttpError, request_json

ENDPOINTS = {"US (api.agentmail.to)": "https://api.agentmail.to", "EU (api.agentmail.eu)": "https://api.agentmail.eu"}


def _shape(name: str, value: str) -> str:
    v = value or ""
    return (f"{name}: set={bool(v)} length={len(v)} leading/trailing whitespace={v != v.strip()} "
            f"wrapped in quotes={bool(v.strip()) and v.strip()[0] in (chr(34), chr(39))} contains newline={chr(10) in v} "
            f"starts with 'Bearer '={v.strip().lower().startswith('bearer ')}")


def _status(method: str, url: str, key: str) -> str:
    try:
        resp = request_json(method, url, headers={"Authorization": f"Bearer {key}"}, retries=0, timeout=20)
    except HttpError as exc:
        body = exc.body[:160].replace(key, "***") if key else exc.body[:160]
        return f"HTTP {exc.status} {body}"
    if isinstance(resp, dict) and "inboxes" in resp:
        return f"HTTP 200 (inboxes visible: {len(resp.get('inboxes') or [])})"
    return "HTTP 200"


def diagnose() -> int:
    key = os.environ.get("AGENTMAIL_API_KEY", "")
    inbox = os.environ.get("AGENTMAIL_INBOX_ID", "")
    recipient = os.environ.get("ALERT_RECIPIENT", "")
    print(_shape("AGENTMAIL_API_KEY", key))
    print(_shape("AGENTMAIL_INBOX_ID", inbox) + f" looks like an email address={'@' in inbox} "
          f"domain={inbox.split('@', 1)[1] if '@' in inbox else '(none)'}")
    print(_shape("ALERT_RECIPIENT", recipient) + f" looks like an email address={'@' in recipient}")
    clean = key.strip().strip("'\"")
    if clean.lower().startswith("bearer "):
        clean = clean[7:].strip()
    variants = {"as stored": key} if clean == key else {"as stored": key, "trimmed/unquoted": clean}
    for label, k in variants.items():
        for region, base in ENDPOINTS.items():
            print(f"[key {label}] {region} GET /v0/inboxes -> {_status('GET', f'{base}/v0/inboxes', k)}")
            if inbox:
                print(f"[key {label}] {region} GET /v0/inboxes/<inbox> -> "
                      f"{_status('GET', f'{base}/v0/inboxes/{quote(inbox.strip(), safe=chr(64))}', k)}")
    return 0
