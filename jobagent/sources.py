"""Job sources.

Two kinds:

* Company boards (one employer each) via official public ATS job-board APIs:
  Greenhouse, Lever, Ashby, SmartRecruiters, Workable, Recruitee.
* Multi-employer feeds with public APIs/RSS intended for re-use:
  Remotive, RemoteOK, Jobicy, Himalayas, We Work Remotely.
* UX Jobs (jobs.uxjobs.io), a design-specialist board. Its robots.txt allows
  general user-agents everywhere except /api/ (blocked here too) and only bars
  named AI-training crawlers; its terms have no automated-access clause. We read
  the public job index embedded in its home page (at most every few hours) and
  the job pages of a small pre-filtered set of candidates.
  (Their terms ask for attribution/links back; emails name the source and
  link to the original posting.)

Not used, because their terms forbid automated access and they offer no public
job API: LinkedIn, Naukri, Indeed, Glassdoor, Wellfound, Instahyre, Dribbble,
Behance, uxdesign.com (terms bar "automated tools, bots, or scraping").
Not used because they are not reliably reachable: UX Jobs Weekly on Substack
(403 to automated clients), uxness.in (429, no feed). role.com is a general
10k-job board with no design index, so it would require bulk crawling.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Callable
from urllib.parse import quote, urlencode

from .http import get_json, get_text
from .models import RawPosting

Fetcher = Callable[[str], list[dict]]

# Only postings whose title looks design-related get a (costly) detail fetch.
DESIGN_HINT = re.compile(r"design|\bux\b|\bui\b|user experience|interaction", re.I)


# ---- company boards ----------------------------------------------------------

def fetch_greenhouse(board: str) -> list[dict]:
    data = get_json(f"https://boards-api.greenhouse.io/v1/boards/{quote(board)}/jobs?content=true")
    return data.get("jobs", [])


def fetch_lever(site: str) -> list[dict]:
    data = get_json(f"https://api.lever.co/v0/postings/{quote(site)}?mode=json")
    return data if isinstance(data, list) else []


def fetch_ashby(board: str) -> list[dict]:
    data = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{quote(board)}?includeCompensation=true")
    return [j for j in data.get("jobs", []) if j.get("isListed", True)]


def fetch_smartrecruiters(company: str) -> list[dict]:
    base = f"https://api.smartrecruiters.com/v1/companies/{quote(company)}/postings"
    postings, offset = [], 0
    while True:
        page = get_json(f"{base}?limit=100&offset={offset}")
        content = page.get("content", [])
        postings.extend(content)
        offset += len(content)
        if not content or offset >= page.get("totalFound", 0) or offset >= 2000:
            break
    for p in postings:
        if DESIGN_HINT.search(p.get("name", "")):
            try:
                p["_detail"] = get_json(f"{base}/{quote(str(p['id']))}")
            except Exception:  # noqa: BLE001 - description is optional
                p["_detail"] = {}
    return postings


def fetch_workable(account: str) -> list[dict]:
    data = get_json(f"https://apply.workable.com/api/v1/widget/accounts/{quote(account)}?details=true")
    jobs = data.get("jobs", [])
    for j in jobs:
        j.setdefault("_company", data.get("name", ""))
    return jobs


def fetch_recruitee(company: str) -> list[dict]:
    data = get_json(f"https://{quote(company)}.recruitee.com/api/offers/")
    return data.get("offers", [])


BOARD_FETCHERS: dict[str, Fetcher] = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "smartrecruiters": fetch_smartrecruiters,
    "workable": fetch_workable,
    "recruitee": fetch_recruitee,
}


# ---- multi-employer feeds ----------------------------------------------------

def fetch_remotive(query: str) -> list[dict]:
    # query is a Remotive category slug, e.g. "design"
    data = get_json(f"https://remotive.com/api/remote-jobs?{urlencode({'category': query})}")
    return data.get("jobs", [])


def fetch_remoteok(query: str) -> list[dict]:
    data = get_json(f"https://remoteok.com/api?{urlencode({'tag': query})}")
    # First element is RemoteOK's legal/attribution notice, not a job.
    return [j for j in data if isinstance(j, dict) and j.get("position")] if isinstance(data, list) else []


def fetch_jobicy(query: str) -> list[dict]:
    data = get_json(f"https://jobicy.com/api/v2/remote-jobs?{urlencode({'count': 100, 'tag': query})}")
    return data.get("jobs", [])


def fetch_himalayas(query: str) -> list[dict]:
    data = get_json(f"https://himalayas.app/jobs/api/search?{urlencode({'q': query})}")
    return data.get("jobs", []) if isinstance(data, dict) else []


def fetch_weworkremotely(query: str) -> list[dict]:
    # query is a WWR category slug, e.g. "remote-design-jobs"
    root = ET.fromstring(get_text(f"https://weworkremotely.com/categories/{quote(query)}.rss"))
    items = []
    for item in root.iter("item"):
        items.append({child.tag: (child.text or "") for child in item})
    return items


_UXJOBS_JSON_RE = re.compile(r"<script\b[^>]*type=[\"']application/json[\"'][^>]*>(.*?)</script>", re.S)
_LD_RE = re.compile(r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>", re.I | re.S)
_UXJOBS_SKIP_SENIORITY = re.compile(r"senior|lead|principal|staff|director|head|manager|intern", re.I)
UXJOBS_MAX_DETAIL = 60


def _uxjobs_candidate(item: dict) -> bool:
    """Cheap pre-filter so only plausible roles get a job-page fetch."""
    return (bool(DESIGN_HINT.search(item.get("r", "")))
            and not _UXJOBS_SKIP_SENIORITY.search(f"{item.get('sen', '')} {item.get('r', '')}")
            and (item.get("c") == "IN" or bool(item.get("rm"))))


def _jobposting_ld(html: str) -> dict:
    import json
    for block in _LD_RE.findall(html):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for d in (data if isinstance(data, list) else [data]):
            if isinstance(d, dict) and d.get("@type") == "JobPosting":
                return d
    return {}


def fetch_uxjobs(query: str) -> list[dict]:
    import json
    html = get_text("https://jobs.uxjobs.io/")
    jobs: list[dict] = []
    for block in _UXJOBS_JSON_RE.findall(html):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        if isinstance(data, list) and data and isinstance(data[0], dict) and "u" in data[0]:
            jobs = data
            break
    candidates = [j for j in jobs if _uxjobs_candidate(j)][:UXJOBS_MAX_DETAIL]
    for j in candidates:
        try:
            j["_detail"] = _jobposting_ld(get_text(f"https://jobs.uxjobs.io/jobs/{quote(j['s'])}/"))
        except Exception:  # noqa: BLE001 - detail is optional
            j["_detail"] = {}
    return jobs


FEED_FETCHERS: dict[str, Fetcher] = {
    "uxjobs": fetch_uxjobs,
    "remotive": fetch_remotive,
    "remoteok": fetch_remoteok,
    "jobicy": fetch_jobicy,
    "himalayas": fetch_himalayas,
    "weworkremotely": fetch_weworkremotely,
}

ALL_FETCHERS: dict[str, Fetcher] = {**BOARD_FETCHERS, **FEED_FETCHERS}


def fetch(source: str, key: str, fetchers: dict[str, Fetcher] | None = None) -> list[RawPosting]:
    """`key` is a board slug for company boards, or a query/category for feeds."""
    fetcher = (fetchers or ALL_FETCHERS)[source]
    return [RawPosting(source=source, company_slug=key, payload=p) for p in fetcher(key)]
