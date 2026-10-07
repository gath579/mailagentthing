"""Source deduplication: collapse the same posting seen via several boards/URLs."""
from __future__ import annotations

from .models import Job


def _keys(job: Job) -> list[str]:
    keys = [f"k:{job.source_key}", f"f:{job.fingerprint}"]
    if job.url:
        keys.append(f"u:{job.url.rstrip('/').lower()}")
    return keys


def _richness(job: Job) -> tuple:
    return (len(job.description), bool(job.compensation), bool(job.posted_at))


def dedupe_sources(jobs: list[Job]) -> list[Job]:
    """Merge jobs sharing a source key, URL, or source-independent fingerprint.

    Within each duplicate group the richest record wins; the other URLs go to
    `also_seen_at` and the earliest posting date is kept. Order of first
    appearance is preserved.
    """
    groups: list[list[Job]] = []
    group_of: dict[str, int] = {}
    for job in jobs:
        keys = _keys(job)
        gid = next((group_of[k] for k in keys if k in group_of), None)
        if gid is None:
            gid = len(groups)
            groups.append([])
        groups[gid].append(job)
        for k in keys:
            group_of.setdefault(k, gid)

    result = []
    for group in groups:
        winner = max(group, key=_richness)
        for other in group:
            if other is not winner and other.url and other.url != winner.url \
                    and other.url not in winner.also_seen_at:
                winner.also_seen_at.append(other.url)
        dates = [j.posted_at for j in group if j.posted_at]
        if dates:
            winner.posted_at = min(dates)
        result.append(winner)
    return result
