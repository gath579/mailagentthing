"""Source deduplication: collapse the same posting seen via several boards/URLs."""
from __future__ import annotations

import re

from .models import Job
from .normalize import canonical_text
from .sources import BOARD_FETCHERS

# Words that vary between copies of the same role on different sites
# ("Junior Product Designer" on the ATS vs "Product Designer" on an aggregator).
_LOOSE_DROP = re.compile(
    r"\b(junior|jr|associate|entry|level|mid|i|ii|iii|iv|1|2|3|remote|hybrid|onsite|on site|"
    r"india|apac|worldwide|global|contract|full time|part time)\b")


def _loose_title(job: Job) -> str:
    title = canonical_text(job.title)
    company = canonical_text(job.company)
    if company:
        title = title.replace(company, " ")
    return re.sub(r"\s+", " ", _LOOSE_DROP.sub(" ", title)).strip()


def _keys(job: Job) -> list[str]:
    keys = [f"k:{job.source_key}", f"f:{job.fingerprint}"]
    if job.url:
        keys.append(f"u:{job.url.rstrip('/').lower()}")
    return keys


def _loose_key(job: Job) -> str:
    return f"l:{canonical_text(job.company)}|{_loose_title(job)}"


def _is_board(job: Job) -> bool:
    return job.source in BOARD_FETCHERS


def _richness(job: Job) -> tuple:
    # The employer's own ATS posting always beats an aggregator copy.
    return (_is_board(job), len(job.description), bool(job.compensation), bool(job.posted_at))


def dedupe_sources(jobs: list[Job]) -> list[Job]:
    """Merge jobs sharing a source key, URL, or source-independent fingerprint.

    Aggregator (feed) copies are additionally merged into a company-board posting
    with the same company and the same title once seniority/location words are
    ignored. Two postings on the same company board are never merged that way
    (they can be genuinely different levels).

    Within each duplicate group the richest record wins (company board first);
    other URLs go to `also_seen_at` and the earliest posting date is kept.
    """
    ordered = sorted(jobs, key=lambda j: not _is_board(j))   # boards first, stable otherwise
    groups: list[list[Job]] = []
    group_of: dict[str, int] = {}
    for job in ordered:
        keys = _keys(job)
        lookup = keys + ([_loose_key(job)] if not _is_board(job) else [])
        gid = next((group_of[k] for k in lookup if k in group_of), None)
        if gid is None:
            gid = len(groups)
            groups.append([])
        groups[gid].append(job)
        for k in keys + [_loose_key(job)]:
            group_of.setdefault(k, gid)

    result = []
    for group in groups:
        winner = max(group, key=_richness)
        for other in group:
            if other is winner:
                continue
            for url in [other.url, *other.also_seen_at]:
                if url and url != winner.url and url not in winner.also_seen_at:
                    winner.also_seen_at.append(url)
        dates = [j.posted_at for j in group if j.posted_at]
        if dates:
            winner.posted_at = min(dates)
        result.append(winner)
    return result
