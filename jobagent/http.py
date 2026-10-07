"""Tiny stdlib HTTP helper with timeouts and bounded retries."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Optional

USER_AGENT = "mailagentthing-job-alerts/0.1 (+https://github.com/gath579/mailagentthing)"


class HttpError(RuntimeError):
    def __init__(self, status: int, url: str, body: str):
        super().__init__(f"HTTP {status} for {url}: {body[:300]}")
        self.status = status
        self.url = url
        self.body = body


def request_json(
    method: str,
    url: str,
    *,
    headers: Optional[dict] = None,
    body: Any = None,
    timeout: float = 30,
    retries: int = 2,
) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    hdrs = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if data is not None:
        hdrs["Content-Type"] = "application/json"
    hdrs.update(headers or {})

    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", "replace")
            # Retry only transient server-side failures; 4xx are caller errors.
            if exc.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(2 ** (attempt + 1))
                continue
            raise HttpError(exc.code, url, text) from None
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt < retries:
                time.sleep(2 ** (attempt + 1))
                continue
            raise HttpError(0, url, repr(exc)) from None
    raise AssertionError("unreachable")


def get_json(url: str, **kw) -> Any:
    return request_json("GET", url, **kw)
