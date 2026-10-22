"""Notification deduplication: remember which jobs were already emailed.

State lives in a JSON file committed to the repo by the workflow, so it
survives between ephemeral GitHub Actions runners and is auditable in git.
A job is recorded as soon as AgentMail returns a message ID for *its own*
email; a failed delivery is never recorded and stays eligible for retry.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import Job

RETENTION_DAYS = 180
SAME_ROLE_DAYS = 60


def canonical_url(url: str | None) -> str:
    """Posting URL compared case-insensitively, ignoring scheme, fragment and a trailing slash."""
    u = (url or "").strip().lower().split("#", 1)[0]
    for prefix in ("https://", "http://"):
        if u.startswith(prefix):
            u = u[len(prefix):]
    if u.startswith("www."):
        u = u[4:]
    return u.rstrip("/")


def _company_title(company: str | None, title: str | None) -> str:
    from .normalize import canonical_text
    return f"{canonical_text(company or '')}|{canonical_text(title or '')}"


def _url_key(url: str | None, title: str | None) -> tuple[str, str]:
    # Same link *and* same title: some boards point several roles at one generic careers page.
    return canonical_url(url), " ".join((title or "").lower().split())


class SentStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.data = {"version": 2, "sent": {}, "runs": []}
        if self.path.exists():
            self.data = json.loads(self.path.read_text())
        self._source_keys = {v.get("source_key") for v in self.data["sent"].values()}
        self._urls = {_url_key(v.get("url"), v.get("title")) for v in self.data["sent"].values() if v.get("url")}
        self._company_titles = {_company_title(v.get("company"), v.get("title")): v.get("sent_at", "")
                                for v in self.data["sent"].values()}

    def already_sent(self, job: Job, now: datetime | None = None) -> bool:
        # The URL check catches the same posting re-listed under a renamed company (aggregators do this).
        if (job.fingerprint in self.data["sent"] or job.source_key in self._source_keys
                or (bool(job.url) and _url_key(job.url, job.title) in self._urls)):
            return True
        # Same company + same title emailed recently: aggregators format the location differently
        # ("India, Bengaluru" vs "Bengaluru Karnataka India"), so the fingerprint alone misses it.
        sent_at = self._company_titles.get(_company_title(job.company, job.title))
        if not sent_at:
            return False
        now = now or datetime.now(timezone.utc)
        return now - datetime.fromisoformat(sent_at) <= timedelta(days=SAME_ROLE_DAYS)

    def unsent(self, jobs: list[Job], now: datetime | None = None) -> list[Job]:
        return [j for j in jobs if not self.already_sent(j, now)]

    def record_sent(self, job: Job, delivery: dict, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        self.data["sent"][job.fingerprint] = {
            "source_key": job.source_key,
            "company": job.company,
            "title": job.title,
            "url": job.url,
            "tier": job.tier,
            "score": job.score,
            "sent_at": now.isoformat(),
            "message_id": delivery["message_id"],
            "thread_id": delivery["thread_id"],
            "verified": None,
        }
        self._source_keys.add(job.source_key)
        if job.url:
            self._urls.add(_url_key(job.url, job.title))
        self._company_titles[_company_title(job.company, job.title)] = now.isoformat()

    def mark_verified(self, job: Job, ok: bool, detail: str = "") -> None:
        entry = self.data["sent"][job.fingerprint]
        entry["verified"] = ok
        if detail:
            entry["verify_detail"] = detail[:300]

    def feed_fetch_times(self) -> dict[str, str]:
        return dict(self.data.get("feeds_fetched", {}))

    def record_feed_fetches(self, labels: list[str], now: datetime) -> None:
        fetched = self.data.setdefault("feeds_fetched", {})
        for label in labels:
            fetched[label] = now.isoformat()

    def record_run(self, summary: dict) -> None:
        self.data["runs"] = (self.data["runs"] + [summary])[-50:]

    def prune(self, now: datetime | None = None) -> None:
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=RETENTION_DAYS)
        self.data["sent"] = {
            k: v for k, v in self.data["sent"].items() if datetime.fromisoformat(v["sent_at"]) >= cutoff
        }

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        with os.fdopen(fd, "w") as fh:
            json.dump(self.data, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, self.path)
