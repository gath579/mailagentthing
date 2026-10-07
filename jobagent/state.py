"""Notification deduplication: remember which jobs were already emailed.

State lives in a JSON file committed to the repo by the workflow, so it
survives between ephemeral GitHub Actions runners and is auditable in git.
A job is recorded only after AgentMail accepted *and* we verified the send.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import Job

RETENTION_DAYS = 180


class SentStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.data = {"version": 1, "sent": {}, "runs": []}
        if self.path.exists():
            self.data = json.loads(self.path.read_text())
        self._source_keys = {v.get("source_key") for v in self.data["sent"].values()}

    def already_sent(self, job: Job) -> bool:
        return job.fingerprint in self.data["sent"] or job.source_key in self._source_keys

    def unsent(self, jobs: list[Job]) -> list[Job]:
        return [j for j in jobs if not self.already_sent(j)]

    def record_sent(self, jobs: list[Job], delivery: dict, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        for job in jobs:
            self.data["sent"][job.fingerprint] = {
                "source_key": job.source_key,
                "company": job.company,
                "title": job.title,
                "url": job.url,
                "score": job.score,
                "sent_at": now.isoformat(),
                "message_id": delivery["message_id"],
                "thread_id": delivery["thread_id"],
            }
            self._source_keys.add(job.source_key)

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
