"""Filtering and scoring against the configured search profile."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from .config import Profile
from .models import Job
from .normalize import canonical_text


@dataclass
class Rejection:
    job: Job
    reason: str


def _contains(haystack: str, phrase: str) -> bool:
    phrase = canonical_text(phrase)
    return bool(phrase) and re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", haystack) is not None


def evaluate(job: Job, profile: Profile, now: datetime | None = None) -> str | None:
    """Score `job` in place. Returns a rejection reason, or None if it passes."""
    now = now or datetime.now(timezone.utc)
    title = canonical_text(job.title)
    location = canonical_text(job.location)
    body = canonical_text(f"{job.title}\n{job.department}\n{job.description}")
    reasons: list[str] = []

    title_hits = [p for p in profile.title_include if _contains(title, p)]
    if not title_hits:
        return "title does not match any include phrase"
    excluded = [p for p in profile.title_exclude if _contains(title, p)]
    if excluded:
        return f"title contains excluded term {excluded[0]!r}"

    if any(_contains(location, p) for p in profile.location_exclude):
        return f"location {job.location!r} is excluded"
    location_hits = [p for p in profile.locations if _contains(location, p)]
    remote_match = job.remote and profile.remote_ok
    if profile.locations and not location_hits and not remote_match:
        return f"location {job.location!r} not in preferred locations"

    age_days = None
    if job.posted_at:
        age_days = (now - job.posted_at).total_seconds() / 86400
        if age_days > profile.max_age_days:
            return f"posted {age_days:.0f} days ago (> {profile.max_age_days})"

    score = 40.0
    reasons.append(f"Title matches “{title_hits[0]}”")

    skill_hits = [s for s in profile.skills if _contains(body, s)]
    if skill_hits:
        score += min(35.0, 7.0 * len(skill_hits))
        reasons.append("Mentions " + ", ".join(skill_hits[:8]))
    elif profile.skills:
        reasons.append("None of your listed skills are mentioned")

    if job.remote:
        score += 10.0 if profile.prefer_remote else 5.0
        reasons.append("Remote-friendly")
    elif location_hits:
        score += 5.0
        reasons.append(f"Location matches “{location_hits[0]}”")

    if age_days is not None:
        if age_days <= 3:
            score += 15.0
        elif age_days <= 7:
            score += 10.0
        elif age_days <= 14:
            score += 5.0
        reasons.append(f"Posted {_age_phrase(age_days)}")

    if job.compensation:
        reasons.append(f"Compensation listed: {job.compensation}")

    job.score = min(score, 100.0)
    job.reasons = reasons
    if job.score < profile.min_score:
        return f"score {job.score:.0f} below minimum {profile.min_score:.0f}"
    return None


def _age_phrase(days: float) -> str:
    if days < 1:
        return "today"
    if days < 2:
        return "yesterday"
    return f"{int(days)} days ago"


def filter_and_score(jobs: list[Job], profile: Profile, now: datetime | None = None
                     ) -> tuple[list[Job], list[Rejection]]:
    matched, rejected = [], []
    for job in jobs:
        reason = evaluate(job, profile, now)
        if reason:
            rejected.append(Rejection(job, reason))
        else:
            matched.append(job)
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    matched.sort(key=lambda j: (j.score, j.posted_at or epoch), reverse=True)
    return matched, rejected
