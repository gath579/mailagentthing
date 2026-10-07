"""Shared test fixtures.

All payloads here are SYNTHETIC: they only mimic the documented response
shapes of each source and of AgentMail. They are never sent anywhere and are
not real postings.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from jobagent.config import Company, Secrets, load_config

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
REPO = Path(__file__).parent.parent


def iso(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat()


GOOD_DESC = (
    "<p>As a Product Designer you will own features end to end, from problem definition to launch.</p>"
    "<ul><li>Design user flows and interaction design for our consumer mobile app used by millions of users</li>"
    "<li>Run usability testing and user research with our research team</li>"
    "<li>Prototype in Figma and contribute to our design system</li>"
    "<li>Work closely with product managers and engineers on complex workflows and edge cases</li></ul>"
    "<p>Requirements: 2-4 years of experience in product design.</p>"
)

GREENHOUSE_JOBS = [
    {"id": 1, "title": "Product Designer", "first_published": iso(2), "updated_at": iso(1),
     "location": {"name": "Bengaluru, Karnataka, India"}, "absolute_url": "https://example.test/gh/1",
     "content": GOOD_DESC.replace("<", "&lt;").replace(">", "&gt;")},
    {"id": 2, "title": "Senior Product Designer", "updated_at": iso(1),
     "location": {"name": "Pune, India"}, "absolute_url": "https://example.test/gh/2", "content": GOOD_DESC},
    {"id": 3, "title": "Graphic Designer", "updated_at": iso(1),
     "location": {"name": "Mumbai, India"}, "absolute_url": "https://example.test/gh/3", "content": ""},
    {"id": 4, "title": "Product Designer", "updated_at": iso(1),
     "location": {"name": "Remote - US"}, "absolute_url": "https://example.test/gh/4", "content": GOOD_DESC},
]

LEVER_JOBS = [
    {"id": "lv-1", "text": "UI/UX Designer", "categories": {"location": "Pune", "commitment": "Full-time"},
     "hostedUrl": "https://example.test/lever/lv-1", "createdAt": int((NOW - timedelta(days=5)).timestamp() * 1000),
     "descriptionPlain": "Design flows in Figma for our B2B SaaS platform. 3+ years of experience.",
     "workplaceType": "hybrid", "country": "IN"},
    {"id": "lv-2", "text": "Product Designer", "categories": {"location": "Gurugram"},
     "hostedUrl": "https://example.test/lever/lv-2", "createdAt": int((NOW - timedelta(days=1)).timestamp() * 1000),
     "descriptionPlain": "Product design. 2+ years of experience.", "workplaceType": "onsite", "country": "IN"},
]

ASHBY_JOBS = [
    {"id": "a1", "title": "Interaction Designer", "location": "Remote (India)", "isRemote": True, "isListed": True,
     "workplaceType": "Remote", "jobUrl": "https://example.test/ashby/a1", "publishedAt": iso(0.5),
     "descriptionPlain": "Design AI-assisted automation tools. Prototyping in Figma. Experience with design systems."},
    {"id": "a2", "title": "UX Researcher", "location": "Mumbai", "isListed": True,
     "jobUrl": "https://example.test/ashby/a2", "publishedAt": iso(1), "descriptionPlain": "Research."},
    {"id": "a3", "title": "Product Designer", "location": "Hyderabad", "isListed": True, "workplaceType": "OnSite",
     "jobUrl": "https://example.test/ashby/a3", "publishedAt": iso(3),
     "descriptionPlain": "Requires 7+ years of experience in product design."},
]

REMOTIVE_JOBS = [
    {"id": 99, "url": "https://example.test/remotive/99", "title": "Product Designer", "company_name": "Globex",
     "category": "Design", "job_type": "full_time", "publication_date": iso(4),
     "candidate_required_location": "Worldwide", "salary": "", "description": GOOD_DESC},
    {"id": 98, "url": "https://example.test/remotive/98", "title": "Product Designer", "company_name": "Initech",
     "category": "Design", "publication_date": iso(4), "candidate_required_location": "USA Only",
     "description": GOOD_DESC},
]

FEED_SAMPLES = {
    "remoteok": [{"legal": "notice"}, {"id": "7", "position": "UX Designer", "company": "Acme", "location": "Worldwide",
                                      "url": "https://example.test/rok/7", "epoch": int(NOW.timestamp()) - 3600,
                                      "description": "<p>Figma prototyping</p>"}],
    "jobicy": [{"id": 5, "url": "https://example.test/jobicy/5", "jobTitle": "Product Designer",
                "companyName": "Umbrella", "jobGeo": "APAC", "pubDate": "2026-10-06 10:00:00",
                "jobDescription": "<p>design systems</p>"}],
    "himalayas": [{"guid": "https://example.test/him/1", "title": "UI Designer", "companyName": "Hooli",
                   "locationRestrictions": ["India"], "applicationLink": "https://example.test/him/1",
                   "pubDate": int(NOW.timestamp()) - 7200, "description": "Figma"}],
    "weworkremotely": [{"title": "Vandelay: Product Designer", "region": "Anywhere in the World",
                        "link": "https://example.test/wwr/1", "guid": "https://example.test/wwr/1",
                        "pubDate": "Tue, 06 Oct 2026 10:00:00 +0000", "description": "&lt;p&gt;flows&lt;/p&gt;"}],
}


def fake_fetchers(fail: set[str] | None = None):
    data = {"greenhouse": GREENHOUSE_JOBS, "lever": LEVER_JOBS, "ashby": ASHBY_JOBS, "remotive": REMOTIVE_JOBS,
            **FEED_SAMPLES}

    def make(source):
        def fetch(key):
            if fail and source in fail:
                raise RuntimeError(f"simulated outage for {source}/{key}")
            items = [dict(p) for p in data.get(source, [])]
            return [p for p in items if "legal" not in p] if source == "remoteok" else items
        return fetch

    return {s: make(s) for s in list(data) + ["smartrecruiters", "workable", "recruitee"]}


@pytest.fixture
def config(tmp_path):
    """The real repo config.toml, with synthetic company boards and an isolated state file."""
    cfg = load_config(REPO / "config.toml")
    return dataclasses.replace(
        cfg,
        companies=[Company("greenhouse", "acme", "Acme", "large"), Company("lever", "globex-in", "GlobexIN", "mid"),
                   Company("ashby", "initech", "Initech", "startup")],
        state_path=tmp_path / "state" / "sent.json",
    )


@pytest.fixture
def secrets() -> Secrets:
    return Secrets(agentmail_api_key="test-key", inbox_id="scout@agentmail.to", recipient="me@example.com")


class FakeAgentMail:
    """Stands in for the HTTP layer of AgentMailClient; records every request."""

    def __init__(self, tamper_subject: bool = False, fail_sends: set[int] | None = None):
        self.calls, self.stored = [], {}
        self.tamper_subject = tamper_subject
        self.fail_sends = fail_sends or set()   # 1-based send attempt numbers that fail
        self.attempts = 0

    def __call__(self, method, url, headers=None, body=None, **kw):
        from urllib.parse import unquote

        from jobagent.http import HttpError

        self.calls.append({"method": method, "url": url, "headers": headers, "body": body})
        if method == "POST" and url.endswith("/messages/send"):
            self.attempts += 1
            if self.attempts in self.fail_sends:
                raise HttpError(500, url, "internal error")
            mid = f"<msg-{len(self.stored) + 1}@agentmail.to>"
            self.stored[mid] = {
                "message_id": mid, "thread_id": f"thr-{len(self.stored) + 1}",
                "labels": ["sent", *body.get("labels", [])], "to": body["to"],
                "subject": body["subject"] + (" (changed)" if self.tamper_subject else ""),
                "timestamp": NOW.isoformat(),
            }
            return {"message_id": mid, "thread_id": self.stored[mid]["thread_id"]}
        if method == "GET":
            mid = unquote(url.rsplit("/", 1)[1])
            if mid not in self.stored:
                raise HttpError(404, url, "not found")
            return self.stored[mid]
        raise AssertionError(f"unexpected call {method} {url}")
