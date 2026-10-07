"""Shared test fixtures.

All payloads here are SYNTHETIC and only mimic the documented response shapes
of the Greenhouse / Lever / Ashby / AgentMail APIs. They are never sent
anywhere and are not real postings.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from jobagent.config import Company, Config, EmailSettings, Profile, Secrets

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)


def iso(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat()


GREENHOUSE_JOBS = [
    {
        "id": 1001,
        "title": "Senior Backend Engineer",
        "updated_at": iso(1),
        "first_published": iso(2),
        "location": {"name": "Remote - US"},
        "absolute_url": "https://example.test/gh/1001",
        "departments": [{"name": "Platform"}],
        "content": "&lt;p&gt;We build distributed systems in Python and Go on Kubernetes.&lt;/p&gt;"
                   "&lt;ul&gt;&lt;li&gt;Own Postgres-backed services end to end&lt;/li&gt;&lt;/ul&gt;",
    },
    {
        "id": 1002,
        "title": "Engineering Manager, Payments",
        "updated_at": iso(1),
        "location": {"name": "New York, NY"},
        "absolute_url": "https://example.test/gh/1002",
        "content": "&lt;p&gt;Lead a team.&lt;/p&gt;",
    },
]

LEVER_JOBS = [
    {
        "id": "lv-abc",
        "text": "Software Engineer, Infrastructure",
        "categories": {"location": "San Francisco, CA", "team": "Infra", "commitment": "Full-time"},
        "hostedUrl": "https://example.test/lever/lv-abc",
        "createdAt": int((NOW - timedelta(days=5)).timestamp() * 1000),
        "descriptionPlain": "You will work on AWS infrastructure using Terraform and Python every day.",
        "lists": [{"text": "Requirements", "content": "<li>3+ years with Kubernetes in production</li>"}],
        "workplaceType": "hybrid",
        "salaryRange": {"min": 180000, "max": 240000, "currency": "USD", "interval": "per-year-salary"},
    },
    {
        "id": "lv-old",
        "text": "Software Engineer",
        "categories": {"location": "Seattle, WA"},
        "hostedUrl": "https://example.test/lever/lv-old",
        "createdAt": int((NOW - timedelta(days=90)).timestamp() * 1000),
        "descriptionPlain": "Python.",
    },
]

ASHBY_JOBS = [
    {
        "id": "ash-1",
        "title": "Machine Learning Engineer",
        "location": "London, UK",
        "secondaryLocations": [],
        "isRemote": False,
        "isListed": True,
        "jobUrl": "https://example.test/ashby/ash-1",
        "publishedAt": iso(1),
        "descriptionPlain": "Train LLM systems with Python.",
    },
    {
        "id": "ash-2",
        "title": "Platform Engineer",
        "location": "Remote",
        "isRemote": True,
        "isListed": True,
        "jobUrl": "https://example.test/ashby/ash-2",
        "publishedAt": iso(0.5),
        "descriptionPlain": "Typescript and Postgres platform work across AWS regions for millions of users.",
        "compensation": {"compensationTierSummary": "$170K – $220K"},
    },
]


def fake_fetchers(fail: set[str] | None = None):
    data = {"greenhouse": GREENHOUSE_JOBS, "lever": LEVER_JOBS, "ashby": ASHBY_JOBS}

    def make(source):
        def fetch(slug):
            if fail and source in fail:
                raise RuntimeError(f"simulated outage for {source}/{slug}")
            return [dict(p) for p in data[source]]
        return fetch

    return {s: make(s) for s in data}


@pytest.fixture
def profile() -> Profile:
    return Profile(
        title_include=["software engineer", "backend engineer", "platform engineer", "machine learning engineer"],
        title_exclude=["manager", "intern"],
        skills=["python", "go", "kubernetes", "aws", "postgres", "typescript"],
        locations=["united states", "us", "san francisco", "new york", "seattle"],
        remote_ok=True,
        prefer_remote=True,
        max_age_days=30,
        min_score=50,
    )


@pytest.fixture
def config(tmp_path, profile) -> Config:
    return Config(
        profile=profile,
        email=EmailSettings(max_jobs_per_email=10, subject_prefix="[Job Alert]"),
        companies=[Company("greenhouse", "acme", "Acme"), Company("lever", "globex", "Globex"),
                   Company("ashby", "initech", "Initech")],
        state_path=tmp_path / "state" / "sent.json",
    )


@pytest.fixture
def secrets() -> Secrets:
    return Secrets(agentmail_api_key="test-key", inbox_id="alerts@agentmail.to", recipient="me@example.com")


class FakeAgentMail:
    """Stands in for the HTTP layer of AgentMailClient; records every request."""

    def __init__(self, tamper_subject: bool = False, send_status: int | None = None):
        self.calls = []
        self.stored = {}
        self.tamper_subject = tamper_subject
        self.send_status = send_status

    def __call__(self, method, url, headers=None, body=None, **kw):
        from jobagent.http import HttpError

        self.calls.append({"method": method, "url": url, "headers": headers, "body": body})
        if method == "POST" and url.endswith("/messages/send"):
            if self.send_status:
                raise HttpError(self.send_status, url, '{"name":"ValidationError"}')
            mid = f"<msg-{len(self.stored) + 1}@agentmail.to>"
            self.stored[mid] = {
                "message_id": mid, "thread_id": f"thr-{len(self.stored) + 1}",
                "labels": ["sent", *body.get("labels", [])], "to": body["to"],
                "subject": body["subject"] + (" (changed)" if self.tamper_subject else ""),
                "timestamp": NOW.isoformat(),
            }
            return {"message_id": mid, "thread_id": self.stored[mid]["thread_id"]}
        if method == "GET":
            from urllib.parse import unquote
            mid = unquote(url.rsplit("/", 1)[1])
            if mid not in self.stored:
                raise HttpError(404, url, "not found")
            return self.stored[mid]
        raise AssertionError(f"unexpected call {method} {url}")
