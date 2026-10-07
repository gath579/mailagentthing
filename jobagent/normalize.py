"""Normalization: turn source-specific payloads into `Job` objects."""
from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional

from .models import Job, RawPosting

_TAG_RE = re.compile(r"<[^>]+>")
_BLOCK_TAG_RE = re.compile(r"</?(p|div|br|li|ul|ol|h[1-6]|tr)\b[^>]*>", re.I)
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_REMOTE_RE = re.compile(r"\bremote\b|\banywhere\b|\bwork from home\b|\bwfh\b", re.I)
_HYBRID_RE = re.compile(r"\bhybrid\b", re.I)

SOURCE_LABELS = {
    "greenhouse": "Greenhouse (company job board)",
    "lever": "Lever (company job board)",
    "ashby": "Ashby (company job board)",
    "smartrecruiters": "SmartRecruiters (company job board)",
    "workable": "Workable (company job board)",
    "recruitee": "Recruitee (company job board)",
    "remotive": "Remotive (remotive.com)",
    "remoteok": "Remote OK (remoteok.com)",
    "jobicy": "Jobicy (jobicy.com)",
    "himalayas": "Himalayas (himalayas.app)",
    "weworkremotely": "We Work Remotely (weworkremotely.com)",
}


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
    """Parse ISO-8601 / RFC-822 strings, epoch seconds or epoch milliseconds into aware UTC datetimes."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
        num = float(value)
        return datetime.fromtimestamp(num / 1000 if num > 1e11 else num, tz=timezone.utc)
    text = str(value).strip()
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = parsedate_to_datetime(text)
        except (TypeError, ValueError):
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def infer_work_mode(explicit: str = "", *texts: str) -> str:
    explicit = (explicit or "").lower().replace("-", "").replace("_", "")
    if explicit in ("remote", "fullyremote"):
        return "remote"
    if explicit == "hybrid":
        return "hybrid"
    if explicit in ("onsite", "inoffice", "office"):
        return "onsite"
    joined = " ".join(t for t in texts if t)
    if _HYBRID_RE.search(joined):
        return "hybrid"
    if _REMOTE_RE.search(joined):
        return "remote"
    return ""


def _join(*parts: str, sep: str = ", ") -> str:
    return sep.join(p for p in parts if p)


# ---- company boards ----------------------------------------------------------

def _greenhouse(p: dict, company: str) -> Job:
    location = (p.get("location") or {}).get("name", "")
    description = html_to_text(p.get("content", ""))
    return Job(
        source="greenhouse", source_id=str(p["id"]), company=p.get("company_name") or company,
        title=p.get("title", "").strip(), url=p.get("absolute_url", ""), location=location,
        work_mode=infer_work_mode("", location, p.get("title", "")),
        department=", ".join(d.get("name", "") for d in p.get("departments") or [] if d.get("name")),
        description=description,
        posted_at=parse_datetime(p.get("first_published") or p.get("updated_at")),
    )


def _lever(p: dict, company: str) -> Job:
    cats = p.get("categories") or {}
    location = cats.get("location", "") or ", ".join(cats.get("allLocations") or [])
    lists = "\n".join(f"{i.get('text', '')}\n{html_to_text(i.get('content', ''))}" for i in p.get("lists") or [])
    salary = p.get("salaryRange") or {}
    comp = ""
    if salary.get("min") and salary.get("max"):
        comp = f"{salary.get('currency', '')} {salary['min']:,}–{salary['max']:,} {salary.get('interval', '')}".strip()
    return Job(
        source="lever", source_id=str(p["id"]), company=company, title=p.get("text", "").strip(),
        url=p.get("hostedUrl", ""), location=location, country=p.get("country") or "",
        work_mode=infer_work_mode(p.get("workplaceType", ""), location),
        department=cats.get("team", "") or cats.get("department", ""),
        employment_type=cats.get("commitment", ""),
        description=_join(p.get("descriptionPlain", ""), lists, p.get("additionalPlain", ""), sep="\n"),
        posted_at=parse_datetime(p.get("createdAt")), compensation=comp,
    )


def _ashby(p: dict, company: str) -> Job:
    locations = [p.get("location", "")] + [
        s.get("location", "") for s in p.get("secondaryLocations") or [] if isinstance(s, dict)]
    location = "; ".join(loc for loc in locations if loc)
    address = ((p.get("address") or {}).get("postalAddress") or {})
    mode = "remote" if p.get("isRemote") else ""
    return Job(
        source="ashby", source_id=str(p["id"]), company=company, title=p.get("title", "").strip(),
        url=p.get("jobUrl") or p.get("applyUrl", ""), location=location,
        country=address.get("addressCountry", ""),
        work_mode=infer_work_mode(p.get("workplaceType") or mode, location),
        department=p.get("team") or p.get("department", ""), employment_type=p.get("employmentType", ""),
        description=p.get("descriptionPlain") or html_to_text(p.get("descriptionHtml", "")),
        posted_at=parse_datetime(p.get("publishedAt")),
        compensation=(p.get("compensation") or {}).get("compensationTierSummary") or "",
    )


def _smartrecruiters(p: dict, company: str) -> Job:
    loc = p.get("location") or {}
    location = loc.get("fullLocation") or _join(loc.get("city", ""), loc.get("region", ""), loc.get("country", ""))
    sections = (((p.get("_detail") or {}).get("jobAd") or {}).get("sections") or {})
    description = "\n".join(html_to_text((sections.get(k) or {}).get("text", ""))
                            for k in ("jobDescription", "qualifications", "additionalInformation"))
    ident = (p.get("company") or {}).get("identifier") or company
    mode = "remote" if loc.get("remote") else ("hybrid" if loc.get("hybrid") else "")
    return Job(
        source="smartrecruiters", source_id=str(p["id"]),
        company=(p.get("company") or {}).get("name") or company, title=p.get("name", "").strip(),
        url=(p.get("_detail") or {}).get("postingUrl") or f"https://jobs.smartrecruiters.com/{ident}/{p['id']}",
        location=location, country=loc.get("country", ""), work_mode=infer_work_mode(mode, location),
        department=(p.get("department") or {}).get("label", ""),
        employment_type=(p.get("typeOfEmployment") or {}).get("label", ""),
        description=description.strip(), posted_at=parse_datetime(p.get("releasedDate")),
    )


def _workable(p: dict, company: str) -> Job:
    locs = p.get("locations") or []
    location = "; ".join(_join(l.get("city", ""), l.get("region", ""), l.get("country", ""))
                         for l in locs if not l.get("hidden")) or _join(p.get("city", ""), p.get("state", ""),
                                                                         p.get("country", ""))
    return Job(
        source="workable", source_id=str(p.get("shortcode") or p.get("id")),
        company=p.get("_company") or company, title=p.get("title", "").strip(),
        url=p.get("url") or p.get("shortlink") or p.get("application_url", ""), location=location,
        country=p.get("country", ""),
        work_mode=infer_work_mode("remote" if p.get("telecommuting") else p.get("workplace", ""), location),
        department=p.get("department", ""), employment_type=p.get("employment_type", ""),
        description=html_to_text(p.get("description", "")),
        posted_at=parse_datetime(p.get("published_on") or p.get("created_at")),
    )


def _recruitee(p: dict, company: str) -> Job:
    location = p.get("location") or _join(p.get("city", ""), p.get("country", ""))
    mode = "remote" if p.get("remote") else ("hybrid" if p.get("hybrid") else "")
    return Job(
        source="recruitee", source_id=str(p["id"]), company=p.get("company_name") or company,
        title=p.get("title", "").strip(), url=p.get("careers_url", ""), location=location,
        country=p.get("country_code") or p.get("country", ""),
        work_mode=infer_work_mode(mode, location), department=p.get("department") or "",
        description=_join(html_to_text(p.get("description", "")), html_to_text(p.get("requirements", "")),
                          sep="\n"),
        posted_at=parse_datetime(p.get("published_at") or p.get("created_at")),
    )


# ---- feeds (all remote job boards) ---------------------------------------------

def _remotive(p: dict, _: str) -> Job:
    location = p.get("candidate_required_location", "")
    return Job(
        source="remotive", source_id=str(p["id"]), company=p.get("company_name", ""),
        title=p.get("title", "").strip(), url=p.get("url", ""), location=f"Remote ({location})" if location else "Remote",
        work_mode="remote", employment_type=p.get("job_type", ""), department=p.get("category", ""),
        description=html_to_text(p.get("description", "")),
        posted_at=parse_datetime(p.get("publication_date")), compensation=p.get("salary", "") or "",
    )


def _remoteok(p: dict, _: str) -> Job:
    comp = ""
    if p.get("salary_min") and p.get("salary_max"):
        comp = f"USD {int(p['salary_min']):,}–{int(p['salary_max']):,}"
    location = p.get("location", "")
    return Job(
        source="remoteok", source_id=str(p["id"]), company=p.get("company", ""),
        title=(p.get("position") or "").strip(), url=p.get("url") or p.get("apply_url", ""),
        location=f"Remote ({location})" if location else "Remote", work_mode="remote",
        description=html_to_text(p.get("description", "")),
        posted_at=parse_datetime(p.get("epoch") or p.get("date")), compensation=comp,
    )


def _jobicy(p: dict, _: str) -> Job:
    geo = p.get("jobGeo", "")
    industry = p.get("jobIndustry")
    return Job(
        source="jobicy", source_id=str(p["id"]), company=p.get("companyName", ""),
        title=html.unescape(p.get("jobTitle", "")).strip(), url=p.get("url", ""),
        location=f"Remote ({geo})" if geo else "Remote", work_mode="remote",
        department=", ".join(industry) if isinstance(industry, list) else (industry or ""),
        employment_type=", ".join(p.get("jobType") or []) if isinstance(p.get("jobType"), list) else "",
        description=html_to_text(p.get("jobDescription") or p.get("jobExcerpt", "")),
        posted_at=parse_datetime(p.get("pubDate")),
    )


def _himalayas(p: dict, _: str) -> Job:
    restrictions = p.get("locationRestrictions") or []
    if restrictions and isinstance(restrictions[0], dict):
        restrictions = [r.get("name", "") for r in restrictions]
    where = ", ".join(r for r in restrictions if r)
    return Job(
        source="himalayas", source_id=str(p.get("guid") or p.get("applicationLink")),
        company=p.get("companyName", ""), title=(p.get("title") or "").strip(),
        url=p.get("applicationLink") or p.get("guid", ""),
        location=f"Remote ({where})" if where else "Remote", work_mode="remote",
        employment_type=p.get("employmentType", ""),
        description=html_to_text(p.get("description") or p.get("excerpt", "")),
        posted_at=parse_datetime(p.get("pubDate")),
    )


def _weworkremotely(p: dict, _: str) -> Job:
    company, _, title = (p.get("title") or "").partition(": ")
    if not title:
        company, title = "", company
    region = p.get("region", "")
    return Job(
        source="weworkremotely", source_id=p.get("guid") or p.get("link", ""), company=company.strip(),
        title=title.strip(), url=p.get("link", ""),
        location=f"Remote ({region})" if region else "Remote", work_mode="remote",
        employment_type=p.get("type", ""), description=html_to_text(p.get("description", "")),
        posted_at=parse_datetime(p.get("pubDate")),
    )


_NORMALIZERS = {
    "greenhouse": _greenhouse, "lever": _lever, "ashby": _ashby, "smartrecruiters": _smartrecruiters,
    "workable": _workable, "recruitee": _recruitee, "remotive": _remotive, "remoteok": _remoteok,
    "jobicy": _jobicy, "himalayas": _himalayas, "weworkremotely": _weworkremotely,
}


def normalize(raw: RawPosting, company_names: dict[str, str] | None = None,
              company_sizes: dict[str, str] | None = None) -> Job:
    """Map one raw posting to a `Job`. Raises KeyError/ValueError on unusable payloads."""
    company = (company_names or {}).get(raw.company_slug) or raw.company_slug.replace("-", " ").title()
    job = _NORMALIZERS[raw.source](raw.payload, company)
    if not job.title or not job.url:
        raise ValueError(f"{raw.source}/{raw.company_slug} posting {job.source_id} lacks title or url")
    job.source_label = SOURCE_LABELS[raw.source]
    job.company_size = (company_sizes or {}).get(raw.company_slug, "")
    return job


def normalize_all(raws: list[RawPosting], company_names: dict[str, str] | None = None,
                  company_sizes: dict[str, str] | None = None) -> tuple[list[Job], list[str]]:
    jobs, errors = [], []
    for raw in raws:
        try:
            jobs.append(normalize(raw, company_names, company_sizes))
        except (KeyError, ValueError, TypeError, AttributeError) as exc:
            errors.append(f"normalize {raw.source}/{raw.company_slug}: {exc!r}")
    return jobs, errors
