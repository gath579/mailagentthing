"""Normalization: turn source-specific payloads into `Job` objects."""
from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from typing import Optional

from .models import Job, RawPosting

_TAG_RE = re.compile(r"<[^>]+>")
_BLOCK_TAG_RE = re.compile(r"</?(p|div|br|li|ul|ol|h[1-6]|tr)\b[^>]*>", re.I)
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_REMOTE_RE = re.compile(r"\bremote\b|\banywhere\b|\bdistributed\b", re.I)


def html_to_text(raw: str) -> str:
    """Convert (possibly entity-escaped) HTML into readable plain text."""
    if not raw:
        return ""
    text = html.unescape(raw)          # Greenhouse double-escapes: &lt;p&gt; -> <p>
    text = _BLOCK_TAG_RE.sub("\n", text)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    lines = [_WS_RE.sub(" ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def canonical_text(value: str) -> str:
    return _NON_ALNUM_RE.sub(" ", (value or "").lower()).strip()


def parse_datetime(value) -> Optional[datetime]:
    """Parse ISO-8601 strings or epoch milliseconds into aware UTC datetimes."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _looks_remote(*parts: str) -> bool:
    return any(_REMOTE_RE.search(p or "") for p in parts)


def _greenhouse(raw: RawPosting, company: str) -> Job:
    p = raw.payload
    location = (p.get("location") or {}).get("name", "")
    depts = ", ".join(d.get("name", "") for d in p.get("departments") or [] if d.get("name"))
    return Job(
        source="greenhouse",
        source_id=str(p["id"]),
        company=p.get("company_name") or company,
        title=p.get("title", "").strip(),
        url=p.get("absolute_url", ""),
        location=location,
        remote=_looks_remote(location),
        department=depts,
        description=html_to_text(p.get("content", "")),
        posted_at=parse_datetime(p.get("first_published") or p.get("updated_at")),
    )


def _lever(raw: RawPosting, company: str) -> Job:
    p = raw.payload
    cats = p.get("categories") or {}
    location = cats.get("location", "") or ", ".join(cats.get("allLocations") or [])
    workplace = (p.get("workplaceType") or "").lower()
    lists = "\n".join(
        f"{item.get('text', '')}\n{html_to_text(item.get('content', ''))}" for item in p.get("lists") or []
    )
    description = "\n".join(
        part for part in (p.get("descriptionPlain", ""), lists, p.get("additionalPlain", "")) if part
    )
    salary = p.get("salaryRange") or {}
    comp = ""
    if salary.get("min") and salary.get("max"):
        comp = f"{salary.get('currency', '')} {salary['min']:,}–{salary['max']:,} {salary.get('interval', '')}".strip()
    return Job(
        source="lever",
        source_id=str(p["id"]),
        company=company,
        title=p.get("text", "").strip(),
        url=p.get("hostedUrl", ""),
        location=location,
        remote=workplace == "remote" or _looks_remote(location),
        department=cats.get("team", "") or cats.get("department", ""),
        employment_type=cats.get("commitment", ""),
        description=description,
        posted_at=parse_datetime(p.get("createdAt")),
        compensation=comp,
    )


def _ashby(raw: RawPosting, company: str) -> Job:
    p = raw.payload
    locations = [p.get("location", "")] + [
        s.get("location", "") for s in p.get("secondaryLocations") or [] if isinstance(s, dict)
    ]
    location = "; ".join(loc for loc in locations if loc)
    comp = (p.get("compensation") or {}).get("compensationTierSummary") or ""
    return Job(
        source="ashby",
        source_id=str(p["id"]),
        company=company,
        title=p.get("title", "").strip(),
        url=p.get("jobUrl") or p.get("applyUrl", ""),
        location=location,
        remote=bool(p.get("isRemote")) or (p.get("workplaceType") or "").lower() == "remote"
        or _looks_remote(location),
        department=p.get("team") or p.get("department", ""),
        employment_type=p.get("employmentType", ""),
        description=p.get("descriptionPlain") or html_to_text(p.get("descriptionHtml", "")),
        posted_at=parse_datetime(p.get("publishedAt")),
        compensation=comp,
    )


_NORMALIZERS = {"greenhouse": _greenhouse, "lever": _lever, "ashby": _ashby}


def normalize(raw: RawPosting, company_names: dict[str, str] | None = None) -> Job:
    """Map one raw posting to a `Job`. Raises KeyError/ValueError on unusable payloads."""
    company = (company_names or {}).get(raw.company_slug) or raw.company_slug.replace("-", " ").title()
    job = _NORMALIZERS[raw.source](raw, company)
    if not job.title or not job.url:
        raise ValueError(f"{raw.source}/{raw.company_slug} posting {job.source_id} lacks title or url")
    return job


def normalize_all(raws: list[RawPosting], company_names: dict[str, str] | None = None) -> tuple[list[Job], list[str]]:
    jobs, errors = [], []
    for raw in raws:
        try:
            jobs.append(normalize(raw, company_names))
        except (KeyError, ValueError, TypeError) as exc:
            errors.append(f"normalize {raw.source}/{raw.company_slug}: {exc!r}")
    return jobs, errors
