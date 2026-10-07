"""Filtering and fit scoring for the configured candidate.

Score (0–100) = title (25) + experience (15) + location/work mode (25)
              + role signals (20) + company structure (10) + recency (5)

Hard rejections: non-design or excluded titles, explicitly senior experience
requirements, roles that are neither in India nor remote-open-to-India, and
stale postings. Everything else is ranked, not filtered.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache

from .config import Config, Locations, Profile, Signal
from .models import Job
from .normalize import canonical_text


@dataclass
class Rejection:
    job: Job
    reason: str


@lru_cache(maxsize=4096)
def _phrase_re(phrase: str) -> re.Pattern:
    prefix = phrase.endswith("*")
    core = canonical_text(phrase.rstrip("*"))
    tail = "" if prefix else r"(?![a-z0-9])"
    return re.compile(rf"(?<![a-z0-9]){re.escape(core)}{tail}")


def has(text: str, phrase: str) -> bool:
    """`text` must already be canonical_text()."""
    return bool(canonical_text(phrase.rstrip("*"))) and _phrase_re(phrase).search(text) is not None


def first(text: str, phrases: list[str]) -> str | None:
    return next((p for p in phrases if has(text, p)), None)


# ---- experience --------------------------------------------------------------

_EXP_RE = re.compile(
    r"(?P<min>\d{1,2})\s*(?:\+|plus)?\s*(?:(?:-|–|—|to)\s*(?P<max>\d{1,2})\s*\+?)?\s*(?:years?|yrs?)\b"
    r"(?=(?P<after>[^.\n]{0,70}))",
    re.I,
)


def parse_experience(text: str) -> tuple[int, int | None, str] | None:
    """Return (min_years, max_years, snippet) of the most demanding stated requirement."""
    best = None
    for m in _EXP_RE.finditer(text or ""):
        window = text[max(0, m.start() - 70): m.end()].lower()
        if "experience" not in window and "exp" not in m.group("after").lower():
            continue
        lo = int(m.group("min"))
        hi = int(m.group("max")) if m.group("max") else None
        if lo > 20 or (hi is not None and hi < lo):
            continue
        snippet = re.sub(r"\s+", " ", text[m.start(): m.end()]).strip()
        if best is None or lo > best[0]:
            best = (lo, hi, snippet)
    return best


def score_experience(job: Job, profile: Profile) -> tuple[float, str | None, str | None, str | None]:
    """Returns (points, reason, concern, rejection)."""
    exp = parse_experience(f"{job.title}\n{job.description}")
    if exp is None:
        job.experience = "not stated"
        return 8, "No explicit experience requirement", None, None
    lo, hi, snippet = exp
    job.experience = snippet
    target_lo, target_hi = profile.experience_target
    if lo >= profile.experience_reject_min:
        return 0, None, None, f"experience: asks for {lo}+ years"
    if lo >= target_hi + 1:
        return 2, None, f"Asks for {lo}+ years of experience — a stretch from your 2+ years", None
    if lo == target_hi:
        return 9, f"Experience: “{snippet}”", f"Asks for {lo}+ years; you have 2+", None
    if hi is not None and hi < target_lo:
        return 6, None, f"Pitched junior (“{snippet}”) — may be below your level", None
    return 15, f"Experience fits your 2–4 year target (“{snippet}”)", None, None


# ---- location / work mode ----------------------------------------------------

_SPLIT_RE = re.compile(r"[;|/\n]|\bor\b", re.I)
_REMOTE_WORDS = ("remote", "anywhere", "work from home", "wfh", "fully remote", "distributed", "hybrid",
                 "on site", "onsite", "office", "location", "locations", "flexible", "multiple")


def _segment_score(seg: str, job: Job, loc: Locations):
    """Score one location option. Returns (points, label, concern) or (None, reason, None) if ineligible."""
    c = canonical_text(seg)
    india = (first(c, loc.tier1 + loc.tier2 + loc.other_india + loc.deprioritized) is not None
             or has(c, "india") or job.country.lower() in ("in", "ind", "india"))
    is_remote = job.work_mode == "remote" or has(c, "remote") or has(c, "anywhere") or has(c, "work from home")
    is_hybrid = job.work_mode == "hybrid" or has(c, "hybrid")

    if is_remote and not is_hybrid:
        if india or first(c, loc.remote_ok_regions):
            return 25, f"Remote, open to India ({seg.strip()})", None
        rest = c
        for w in _REMOTE_WORDS:
            rest = _phrase_re(w).sub(" ", rest)
        if not rest.strip():
            return 12, "Remote (region not stated)", "Remote, but the hiring region isn’t stated — confirm they hire in India"
        return None, f"remote limited to {seg.strip()}", None

    if not india:
        if not c:
            return 4, "Location not stated", "Location and work mode not stated"
        return None, f"outside India ({seg.strip()})", None

    mode = "hybrid" if is_hybrid else ("on-site" if job.work_mode == "onsite" else "")
    mode_txt = f", {mode}" if mode else ""
    unstated = None if mode else "Work mode not stated"
    if (city := first(c, loc.tier1)):
        return (22 if mode == "hybrid" else 18), f"Priority city: {city.title()}{mode_txt}", unstated
    if (city := first(c, loc.tier2)):
        return (15 if mode == "hybrid" else 12), f"Acceptable city: {city.title()}{mode_txt}", unstated
    if (city := first(c, loc.deprioritized)):
        return 3, f"{city.title()}{mode_txt}", f"{city.title()} is a deprioritized location"
    if (city := first(c, loc.other_india)):
        return (10 if mode == "hybrid" else 8), f"{city.title()}{mode_txt}", \
            f"{city.title()} isn’t one of your priority cities"
    return 12, f"India{mode_txt}", "City not specified"


def score_location(job: Job, loc: Locations) -> tuple[float, str | None, str | None, str | None]:
    segments = [s for s in _SPLIT_RE.split(job.location or "") if s.strip()] or [""]
    if len(segments) > 1 and job.work_mode == "remote":
        segments = [job.location]  # e.g. "Remote; India" describes one remote option
    best, rejections = None, []
    for seg in segments:
        pts, label, concern = _segment_score(seg, job, loc)
        if pts is None:
            rejections.append(label)
        elif best is None or pts > best[0]:
            best = (pts, label, concern)
    if best is None:
        return 0, None, None, f"location: {rejections[0]}"
    return best[0], best[1], best[2], None


# ---- signals, company, recency ----------------------------------------------

def match_signals(text: str, signals: list[Signal]) -> list[Signal]:
    return [s for s in signals if first(text, s.patterns)]


_SIZE_POINTS = {"large": 10, "mid": 8, "startup": 4}


def evaluate(job: Job, config: Config, now: datetime | None = None) -> str | None:
    """Score `job` in place. Returns a rejection reason, or None if it passes."""
    now = now or datetime.now(timezone.utc)
    profile = config.profile
    title = canonical_text(job.title)
    body = canonical_text(f"{job.title}\n{job.department}\n{job.description}")
    reasons, concerns = [], []

    if (bad := first(title, profile.title_exclude)):
        return f"title: excluded term {bad!r}"
    if (bad := first(title, profile.title_exclude_unless_designer)) and not has(title, "designer"):
        return f"title: {bad!r} role without a design component"
    if (hit := first(title, profile.title_core)):
        score = 25.0
        reasons.append(f"Core title match: “{hit}”")
    elif (hit := first(title, profile.title_related)):
        score = 15.0
        reasons.append(f"Related design title: “{hit}”")
    else:
        return "title: not a product/UX design role"

    age_days = None
    if job.posted_at:
        age_days = (now - job.posted_at).total_seconds() / 86400
        if age_days > profile.max_age_days:
            return f"posted: {age_days:.0f} days ago (> {profile.max_age_days})"

    pts, reason, concern, reject = score_experience(job, profile)
    if reject:
        return reject
    score += pts
    reasons += [reason] if reason else []
    concerns += [concern] if concern else []

    pts, reason, concern, reject = score_location(job, config.locations)
    if reject:
        return reject
    score += pts
    reasons += [reason] if reason else []
    concerns += [concern] if concern else []

    hits = match_signals(body, config.signals)
    job.signals = [s.name for s in hits]
    weighted = [s for s in hits if s.weight > 0]
    if weighted:
        score += min(20.0, sum(s.weight for s in weighted))
        reasons.append("Role involves " + ", ".join(s.label for s in weighted))

    flagged = match_signals(body, config.concerns)
    concerns += [s.label for s in flagged]
    if job.company_size in _SIZE_POINTS:
        score += _SIZE_POINTS[job.company_size]
        if job.company_size == "startup":
            concerns.append("Startup — expect a less established research/data practice")
        else:
            reasons.append(f"{job.company_size.title()}-size product organization")
    else:
        score += 3 if any(s.name == "early_stage" for s in flagged) else 5

    if age_days is not None:
        score += (5 if age_days <= 3 else 4 if age_days <= 7 else 3 if age_days <= 14
                      else 2 if age_days <= 30 else 1 if age_days <= 60 else 0)
        reasons.append(f"Posted {_age_phrase(age_days)}")
    else:
        score += 1
    if job.employment_type and re.search(r"contract|freelance|part.?time|temporary", job.employment_type, re.I):
        concerns.append(f"Employment type: {job.employment_type}")

    job.score = round(min(score, 100.0), 1)
    job.reasons = reasons
    job.concerns = list(dict.fromkeys(concerns))
    if job.score < profile.min_score:
        return f"score: {job.score:.0f} below minimum {profile.min_score:.0f}"
    job.tier = ("Strong" if job.score >= profile.strong_score
                else "Good" if job.score >= profile.good_score else "Possible")
    return None


def _age_phrase(days: float) -> str:
    if days < 1:
        return "today"
    if days < 2:
        return "yesterday"
    return f"{int(days)} days ago"


def filter_and_score(jobs: list[Job], config: Config, now: datetime | None = None
                     ) -> tuple[list[Job], list[Rejection]]:
    matched, rejected = [], []
    for job in jobs:
        reason = evaluate(job, config, now)
        if reason:
            rejected.append(Rejection(job, reason))
        else:
            matched.append(job)
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    matched.sort(key=lambda j: (j.score, j.posted_at or epoch), reverse=True)
    return matched, rejected
