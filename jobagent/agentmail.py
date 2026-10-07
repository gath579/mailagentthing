"""Minimal AgentMail REST client (endpoints per the official `agentmail` SDK v2).

POST /v0/inboxes/{inbox_id}/messages/send  -> {"message_id", "thread_id"}
GET  /v0/inboxes/{inbox_id}/messages/{id}  -> Message (labels, to, subject, ...)
"""
from __future__ import annotations

import hashlib
import time
from urllib.parse import quote

from .config import Secrets
from .emailgen import Email
from .http import HttpError, request_json


class DeliveryError(RuntimeError):
    pass


class AgentMailClient:
    def __init__(self, secrets: Secrets, request=request_json):
        self.secrets = secrets
        self._request = request

    def _url(self, path: str) -> str:
        return f"{self.secrets.base_url.rstrip('/')}/v0/{path}"

    def _headers(self, extra: dict | None = None) -> dict:
        return {"Authorization": f"Bearer {self.secrets.agentmail_api_key}", **(extra or {})}

    def send(self, email: Email, idempotency_key: str) -> dict:
        inbox = quote(self.secrets.inbox_id, safe="")
        try:
            resp = self._request(
                "POST",
                self._url(f"inboxes/{inbox}/messages/send"),
                headers=self._headers({"Idempotency-Key": idempotency_key}),
                body={
                    "to": [self.secrets.recipient],
                    "subject": email.subject,
                    "text": email.text,
                    "html": email.html,
                    "labels": ["job-alert"],
                },
                retries=2,  # safe: the idempotency key makes retries return the original send
            )
        except HttpError as exc:
            raise DeliveryError(f"AgentMail send failed: {exc}") from exc
        if not isinstance(resp, dict) or not resp.get("message_id") or not resp.get("thread_id"):
            raise DeliveryError(f"AgentMail send returned an unexpected response: {resp!r}")
        return resp

    def get_message(self, message_id: str) -> dict:
        inbox = quote(self.secrets.inbox_id, safe="")
        return self._request(
            "GET",
            self._url(f"inboxes/{inbox}/messages/{quote(message_id, safe='')}"),
            headers=self._headers(),
        )

    def verify(self, sent: dict, email: Email, attempts: int = 4, delay: float = 2.0) -> dict:
        """Read the message back from AgentMail and check it is the one we sent."""
        last_error = None
        for attempt in range(attempts):
            try:
                msg = self.get_message(sent["message_id"])
            except HttpError as exc:   # may 404 briefly right after sending
                last_error = exc
                time.sleep(delay * (attempt + 1))
                continue
            problems = []
            if msg.get("message_id") != sent["message_id"]:
                problems.append(f"message_id {msg.get('message_id')!r} != {sent['message_id']!r}")
            if msg.get("thread_id") != sent["thread_id"]:
                problems.append("thread_id mismatch")
            if (msg.get("subject") or "") != email.subject:
                problems.append(f"subject mismatch: {msg.get('subject')!r}")
            to = [t.lower() for t in msg.get("to") or []]
            if not any(self.secrets.recipient.lower() in t for t in to):
                problems.append(f"recipient {self.secrets.recipient!r} not in to={msg.get('to')!r}")
            if problems:
                raise DeliveryError("AgentMail verification failed: " + "; ".join(problems))
            return {
                "message_id": msg["message_id"],
                "thread_id": msg["thread_id"],
                "labels": msg.get("labels", []),
                "timestamp": msg.get("timestamp"),
                "to": msg.get("to"),
            }
        raise DeliveryError(f"AgentMail verification could not read message back: {last_error}")


def idempotency_key(recipient: str, jobs) -> str:
    material = recipient.lower() + "\n" + "\n".join(sorted(j.fingerprint for j in jobs))
    return "jobalert-" + hashlib.sha256(material.encode()).hexdigest()[:40]
