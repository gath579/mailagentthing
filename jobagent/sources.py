"""Job sources.

Only official, public, unauthenticated job-board APIs that applicant-tracking
systems publish specifically so third parties can list a company's openings:

* Greenhouse Job Board API  https://developers.greenhouse.io/job-board.html
* Lever Postings API        https://github.com/lever/postings-api
* Ashby Job Posting API     https://developers.ashbyhq.com/docs/public-job-posting-api

No HTML scraping and no sites whose terms forbid automated access (LinkedIn,
Indeed, Glassdoor, ...).
"""
from __future__ import annotations

from typing import Callable
from urllib.parse import quote

from .http import get_json
from .models import RawPosting

Fetcher = Callable[[str], list[dict]]


def fetch_greenhouse(board: str) -> list[dict]:
    data = get_json(f"https://boards-api.greenhouse.io/v1/boards/{quote(board)}/jobs?content=true")
    return data.get("jobs", [])


def fetch_lever(site: str) -> list[dict]:
    data = get_json(f"https://api.lever.co/v0/postings/{quote(site)}?mode=json")
    return data if isinstance(data, list) else []


def fetch_ashby(board: str) -> list[dict]:
    data = get_json(
        f"https://api.ashbyhq.com/posting-api/job-board/{quote(board)}?includeCompensation=true"
    )
    return [j for j in data.get("jobs", []) if j.get("isListed", True)]


FETCHERS: dict[str, Fetcher] = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
}


def fetch(source: str, slug: str, fetchers: dict[str, Fetcher] | None = None) -> list[RawPosting]:
    fetcher = (fetchers or FETCHERS)[source]
    return [RawPosting(source=source, company_slug=slug, payload=p) for p in fetcher(slug)]
