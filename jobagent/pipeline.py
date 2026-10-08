"""End-to-end pipeline:

discover -> normalize -> source dedup -> filter/score -> notification dedup
-> per-job analysis/email -> AgentMail send -> record (on message ID) -> verify
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from .agentmail import AgentMailClient, DeliveryError, idempotency_key
from .config import Config, Secrets
from .dedupe import dedupe_sources
from .discovery import discover_jobs
from .emailgen import build_job_email
from .linkcheck import check as default_link_check, check_all
from .models import Job
from .normalize import normalize_all
from .scoring import filter_and_score
from .sources import Fetcher
from .state import SentStore

log = logging.getLogger(__name__)
MAX_CONSECUTIVE_FAILURES = 3


class PipelineError(RuntimeError):
    pass


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


def send_status(job: Job, config: Config) -> str:
    """Why a matched job will or won't be emailed automatically."""
    if job.tier not in config.email.send_tiers:
        return f"report only ({job.tier} tier is not auto-sent)"
    if job.conditional:
        return "CONDITIONAL: " + "; ".join(job.conditional)
    if config.email.require_live_link and job.link_status != "live":
        return f"NOT SENT: link {job.link_status or 'not checked'} ({job.link_detail})"
    return "WILL SEND"


def _brief(job: Job) -> dict:
    return {"tier": job.tier, "score": job.score, "title": job.title, "company": job.company,
            "conditional": job.conditional, "link_status": job.link_status, "link_detail": job.link_detail,
            "location": job.location, "work_mode": job.work_mode or "not stated",
            "posted_at": job.posted_at.isoformat() if job.posted_at else None,
            "source": job.source_label, "url": job.url, "reasons": job.reasons, "concerns": job.concerns}


def _top_table(jobs: list[Job], config: Config, n: int = 10) -> str:
    lines = [f"TOP {min(n, len(jobs))} MATCHES"]
    for i, j in enumerate(jobs[:n], 1):
        lines += [
            f"{i:>2}. [{j.tier}] {j.score:.0f}/100  {j.title} — {j.company}",
            f"    status: {send_status(j, config)}",
            f"    link: {j.link_status} — {j.link_detail}",
            f"    {j.location or 'location not stated'} · {j.work_mode or 'mode not stated'} · "
            f"posted {j.posted_at.date() if j.posted_at else 'n/a'} · {j.source_label}",
            f"    {j.url}",
            f"    why: {'; '.join(j.reasons)}",
            f"    concerns: {'; '.join(j.concerns) or 'none detected'}",
        ]
    return "\n".join(lines)


def run(config: Config, *, send: bool, out_dir: Path, secrets: Secrets | None = None,
        fetchers: dict[str, Fetcher] | None = None, client: AgentMailClient | None = None,
        require_sent: bool = False, max_emails: int | None = None, throttle_feeds: bool = False,
        now: datetime | None = None, link_checker=default_link_check) -> dict:
    now = now or datetime.now(timezone.utc)
    out_dir.mkdir(parents=True, exist_ok=True)
    summary: dict = {"started_at": now.isoformat(), "mode": "send" if send else "dry-run"}

    # 1. Discovery
    found = discover_jobs(config.companies, config.feeds, fetchers, now=now, throttle_feeds=throttle_feeds)
    summary.update(discovered=len(found.postings), sources_ok=found.counts, sources_failed=found.errors,
                   feeds_skipped=found.skipped)

    # 2. Normalization
    jobs, norm_errors = normalize_all(found.postings, config.company_names, config.company_sizes)
    summary.update(normalized=len(jobs), normalize_errors=norm_errors[:20])

    # 3. Source deduplication
    jobs = dedupe_sources(jobs)
    summary["after_source_dedup"] = len(jobs)

    # 4. Filtering / scoring
    matched, rejected = filter_and_score(jobs, config, now)
    reject_counts: dict[str, int] = {}
    for r in rejected:
        bucket = r.reason.split(":")[0]
        reject_counts[bucket] = reject_counts.get(bucket, 0) + 1
    summary.update(matched=len(matched), rejected_by_reason=reject_counts,
                   tiers={t: sum(j.tier == t for j in matched) for t in ("Strong", "Good", "Possible")})
    (out_dir / "matches.json").write_text("[]")
    near = [r for r in rejected if not r.reason.startswith("title")]
    print(f"NEAR MISSES ({len(near)} design-titled roles rejected on location/experience/age/score):")
    for r in near[:40]:
        print(f"  - {r.job.title} — {r.job.company} | {r.job.location} | {r.reason}")
    (out_dir / "near_misses.json").write_text(json.dumps(
        [{"title": r.job.title, "company": r.job.company, "location": r.job.location, "reason": r.reason,
          "url": r.job.url} for r in near], indent=2))

    # 5. Notification deduplication
    store = SentStore(config.state_path)
    new = store.unsent(matched)

    # 5b. Direct posting-link verification (only matters for jobs that could be sent,
    #     but checked for all new matches so the dry-run report shows it)
    check_all(new, link_checker)
    eligible = [j for j in new if send_status(j, config) == "WILL SEND"]
    limit = config.email.max_emails_per_run if max_emails is None else max_emails
    batch = eligible[:limit]
    summary.update(new_unsent=len(new), sendable=len(eligible), this_run=len(batch),
                   conditional=sum(bool(j.conditional) for j in new),
                   report_only=sum(j.tier not in config.email.send_tiers for j in new),
                   link_status={s: sum(j.link_status == s for j in new)
                                for s in ("live", "closed", "redirected", "unverified")})

    # 6. Per-job analysis / email generation (previews always written)
    previews = out_dir / "emails"
    previews.mkdir(exist_ok=True)
    emails = []
    batch_ids = {id(j) for j in batch}
    preview_jobs = new[:10] + [j for j in batch if j not in new[:10]]
    for i, job in enumerate(preview_jobs, 1):
        email = build_job_email(job, config, now)
        stem = f"{i:02d}-{_slug(job.company)}-{_slug(job.title)}"
        (previews / f"{stem}.txt").write_text(f"Subject: {email.subject}\n\n{email.text}")
        (previews / f"{stem}.html").write_text(email.html)
        if id(job) in batch_ids:
            emails.append((job, email))
    (out_dir / "matches.json").write_text(json.dumps([_brief(j) | {"send_status": send_status(j, config)}
                                                      for j in new], indent=2))
    table = _top_table(new, config)
    (out_dir / "top_matches.txt").write_text(table)
    print(table)

    if not send or not batch:
        summary["sent"] = []
        if send and require_sent:
            raise PipelineError("--require-sent: no new matching jobs to send")
        return _finish(store, summary, out_dir, persist=False)

    # 7. Delivery: one email per job; record each as soon as AgentMail returns its message ID
    secrets = secrets or Secrets.from_env()
    client = client or AgentMailClient(secrets)
    sent, failed, unverified, consecutive = [], [], [], 0
    for job, email in emails:
        try:
            resp = client.send(email, idempotency_key(secrets.recipient, job),
                               labels=["job-alert", f"{job.tier.lower()}-match"])
        except DeliveryError as exc:
            log.error("send failed for %s / %s: %s", job.company, job.title, exc)
            failed.append({"company": job.company, "title": job.title, "error": str(exc)[:300]})
            consecutive += 1
            if consecutive >= MAX_CONSECUTIVE_FAILURES:
                log.error("stopping after %d consecutive send failures", consecutive)
                break
            continue
        consecutive = 0
        store.record_sent(job, resp, now)
        store.save()                     # persist immediately: a later crash must not cause a resend
        try:
            verified = client.verify(resp, email)
            store.mark_verified(job, True)
            log.info("sent + verified %s for %s / %s (labels=%s)", resp["message_id"], job.company,
                     job.title, verified["labels"])
        except DeliveryError as exc:
            store.mark_verified(job, False, str(exc))
            unverified.append({"company": job.company, "title": job.title, "message_id": resp["message_id"],
                               "error": str(exc)[:300]})
        store.save()
        sent.append({"company": job.company, "title": job.title, "tier": job.tier,
                     "message_id": resp["message_id"], "thread_id": resp["thread_id"],
                     "subject": email.subject})

    summary.update(sent=sent, send_failures=failed, verify_failures=unverified)
    result = _finish(store, summary, out_dir, persist=True)
    if failed or unverified:
        raise DeliveryError(f"{len(failed)} send failure(s), {len(unverified)} verification failure(s); "
                            f"see run_summary.json")
    if require_sent and not sent:
        raise PipelineError("--require-sent: nothing was sent")
    return result


def _finish(store: SentStore, summary: dict, out_dir: Path, persist: bool) -> dict:
    summary["finished_at"] = datetime.now(timezone.utc).isoformat()
    (out_dir / "run_summary.json").write_text(json.dumps(summary, indent=2))
    if persist:
        store.prune()
        store.record_run({k: summary.get(k) for k in ("started_at", "mode", "discovered", "matched", "new_unsent")}
                         | {"sent": len(summary.get("sent") or []),
                            "failed": len(summary.get("send_failures") or [])})
        store.save()
    return summary
