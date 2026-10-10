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


SDK_HEADERS = {   # what the official `agentmail` Python SDK v2 sends
    "User-Agent": "agentmail/2.0.13", "X-Fern-Language": "Python", "X-Fern-SDK-Name": "agentmail",
    "X-Fern-SDK-Version": "2.0.13",
}


def _status(method: str, url: str, key: str | None, extra: dict | None = None) -> str:
    headers = dict(extra or {})
    if key is not None:
        headers["Authorization"] = f"Bearer {key}"
    try:
        resp = request_json(method, url, headers=headers, retries=0, timeout=20)
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
    base = ENDPOINTS["US (api.agentmail.to)"]
    url = f"{base}/v0/inboxes"
    print("--- baselines (tell a gateway/UA block apart from a rejected key) ---")
    print(f"no Authorization header -> {_status('GET', url, None)}")
    print(f"garbage key 'am_invalid_diagnostic' -> {_status('GET', url, 'am_invalid_diagnostic')}")
    print(f"real key, our User-Agent -> {_status('GET', url, clean)}")
    print(f"real key, SDK User-Agent + X-Fern headers -> {_status('GET', url, clean, SDK_HEADERS)}")
    _sdk_check(clean, inbox.strip())
    return 0


def _sdk_check(key: str, inbox: str) -> None:
    """Same read through the official SDK, if installed (pip install agentmail)."""
    try:
        from agentmail import AgentMail  # type: ignore
    except ImportError:
        print("official agentmail SDK: not installed (skipped)")
        return
    client = AgentMail(api_key=key)
    for label, call in (("inboxes.list()", lambda: client.inboxes.list()),
                        ("inboxes.get(<inbox>)", lambda: client.inboxes.get(inbox))):
        try:
            resp = call()
            n = len(getattr(resp, "inboxes", None) or []) if label.startswith("inboxes.list") else 1
            print(f"official SDK {label} -> OK ({n} inbox(es))")
        except Exception as exc:  # noqa: BLE001 - diagnostic only
            code = getattr(exc, "status_code", "?")
            body = str(getattr(exc, "body", "") or exc)[:160].replace(key, "***")
            print(f"official SDK {label} -> {type(exc).__name__} status={code} {body}")
