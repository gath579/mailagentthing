"""End-to-end pipeline:

discover -> normalize -> source dedup -> filter/score -> notification dedup
-> email generation -> AgentMail send -> verify -> record as sent
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from .agentmail import AgentMailClient, idempotency_key
from .config import Config, Secrets
from .dedupe import dedupe_sources
from .discovery import discover_jobs
from .emailgen import build_email
from .normalize import normalize_all
from .scoring import filter_and_score
from .sources import Fetcher
from .state import SentStore

log = logging.getLogger(__name__)


class PipelineError(RuntimeError):
    pass


def run(config: Config, *, send: bool, out_dir: Path, secrets: Secrets | None = None,
        fetchers: dict[str, Fetcher] | None = None, client: AgentMailClient | None = None,
        require_sent: bool = False, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    out_dir.mkdir(parents=True, exist_ok=True)
    summary: dict = {"started_at": now.isoformat(), "mode": "send" if send else "dry-run"}

    # 1. Discovery
    found = discover_jobs(config.companies, fetchers)
    summary["discovered"] = len(found.postings)
    summary["boards_ok"] = found.counts
    summary["boards_failed"] = found.errors

    # 2. Normalization
    jobs, norm_errors = normalize_all(found.postings, config.company_names)
    summary["normalized"] = len(jobs)
    summary["normalize_errors"] = norm_errors[:20]

    # 3. Source deduplication
    jobs = dedupe_sources(jobs)
    summary["after_source_dedup"] = len(jobs)

    # 4. Filtering / scoring
    matched, rejected = filter_and_score(jobs, config.profile, now)
    summary["matched"] = len(matched)
    reject_counts: dict[str, int] = {}
    for r in rejected:
        bucket = r.reason.split(" ")[0]  # title / location / posted / score
        reject_counts[bucket] = reject_counts.get(bucket, 0) + 1
    summary["rejected_by_reason"] = reject_counts

    # 5. Notification deduplication
    store = SentStore(config.state_path)
    new = store.unsent(matched)
    batch = new[: config.email.max_jobs_per_email]
    summary["new_unsent"] = len(new)
    summary["in_this_email"] = [
        {"company": j.company, "title": j.title, "location": j.location, "score": j.score,
         "posted_at": j.posted_at.isoformat() if j.posted_at else None, "url": j.url}
        for j in batch
    ]
    (out_dir / "matches.json").write_text(json.dumps([j.to_dict() for j in matched], indent=2))

    if not batch:
        log.info("no new matching jobs; nothing to send")
        summary["sent"] = None
        if require_sent:
            raise PipelineError(f"--require-sent: no new matching jobs to send. Summary: {summary}")
        return _finish(store, summary, out_dir, persist=False)

    # 6. Analysis / email generation
    email = build_email(batch, config.email.subject_prefix, now)
    (out_dir / "email.html").write_text(email.html)
    (out_dir / "email.txt").write_text(f"Subject: {email.subject}\n\n{email.text}")
    summary["subject"] = email.subject

    if not send:
        log.info("dry run: email written to %s, not sent", out_dir)
        summary["sent"] = None
        return _finish(store, summary, out_dir, persist=False)

    # 7. Delivery + verification
    secrets = secrets or Secrets.from_env()
    client = client or AgentMailClient(secrets)
    key = idempotency_key(secrets.recipient, batch)
    response = client.send(email, key)
    log.info("AgentMail accepted message %s", response["message_id"])
    verified = client.verify(response, email)
    log.info("verified message %s (labels=%s)", verified["message_id"], verified["labels"])

    # 8. Record as sent (only after verification succeeded)
    store.record_sent(batch, verified, now)
    summary["sent"] = {**verified, "jobs": len(batch), "idempotency_key": key}
    return _finish(store, summary, out_dir, persist=True)


def _finish(store: SentStore, summary: dict, out_dir: Path, persist: bool) -> dict:
    summary["finished_at"] = datetime.now(timezone.utc).isoformat()
    (out_dir / "run_summary.json").write_text(json.dumps(summary, indent=2))
    if persist:
        store.prune()
        store.record_run({k: summary.get(k) for k in
                          ("started_at", "mode", "discovered", "matched", "new_unsent", "sent")})
        store.save()
    return summary
