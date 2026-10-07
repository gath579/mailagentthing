"""Job sources.

Two kinds:

* Company boards (one employer each) via official public ATS job-board APIs:
  Greenhouse, Lever, Ashby, SmartRecruiters, Workable, Recruitee.
* Multi-employer feeds with public APIs/RSS intended for re-use:
  Remotive, RemoteOK, Jobicy, Himalayas, We Work Remotely.
  (Their terms ask for attribution/links back; emails name the source and
  link to the original posting.)

Not used, because their terms forbid automated access and they offer no public
job API: LinkedIn, Naukri, Indeed, Glassdoor, Wellfound, Instahyre, Dribbble,
Behance.
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


FEED_FETCHERS: dict[str, Fetcher] = {
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
