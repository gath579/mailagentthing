"""Configuration: candidate, search profile, sources from TOML; secrets from env."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .sources import BOARD_FETCHERS, FEED_FETCHERS


@dataclass
class Company:
    source: str
    slug: str
    name: str = ""
    size: str = ""   # "large" | "mid" | "startup" | ""


@dataclass
class Feed:
    source: str
    query: str
    min_interval_hours: int = 1   # politeness: fetch only every N hours on scheduled runs


@dataclass
class Signal:
    name: str
    label: str
    patterns: list[str]
    weight: float = 0.0


@dataclass
class Project:
    name: str
    triggers: list[str]           # signal names that make this project relevant
    why: str                      # factual evidence line for "why it matches"
    pitch: str                    # factual sentence for application text
    always: bool = False


@dataclass
class Candidate:
    name: str
    portfolio: str
    years: str
    intro: str


@dataclass
class Profile:
    title_core: list[str]
    title_related: list[str] = field(default_factory=list)
    title_exclude: list[str] = field(default_factory=list)
    title_exclude_unless_designer: list[str] = field(default_factory=list)
    experience_target: list[int] = field(default_factory=lambda: [2, 4])
    experience_reject_min: int = 6
    fresh_days: int = 30
    normal_days: int = 90
    stale_days: int = 180
    min_score: float = 50.0
    strong_score: float = 75.0
    good_score: float = 62.0


@dataclass
class Locations:
    tier1: list[str]
    tier2: list[str] = field(default_factory=list)
    other_india: list[str] = field(default_factory=list)
    deprioritized: list[str] = field(default_factory=list)
    remote_ok_regions: list[str] = field(default_factory=list)


@dataclass
class EmailSettings:
    max_emails_per_run: int = 10
    send_tiers: list[str] = field(default_factory=lambda: ["Strong", "Good"])
    require_live_link: bool = True


@dataclass
class Verification:
    url: str
    india_eligible: bool | None = None
    active_confirmed_on: str = ""
    note: str = ""

    def active_confirmed(self, now, valid_days: int) -> bool:
        if not self.active_confirmed_on:
            return False
        from datetime import date
        return (now.date() - date.fromisoformat(self.active_confirmed_on)).days <= valid_days


@dataclass
class Config:
    candidate: Candidate
    profile: Profile
    locations: Locations
    signals: list[Signal]
    concerns: list[Signal]
    projects: list[Project]
    email: EmailSettings
    companies: list[Company]
    feeds: list[Feed]
    state_path: Path
    verified: dict[str, Verification] = field(default_factory=dict)
    verification_valid_days: int = 14

    @property
    def company_names(self) -> dict[str, str]:
        return {c.slug: c.name for c in self.companies if c.name}

    @property
    def company_sizes(self) -> dict[str, str]:
        return {c.slug: c.size for c in self.companies if c.size}


@dataclass
class Secrets:
    agentmail_api_key: str
    inbox_id: str
    recipient: str
    base_url: str = "https://api.agentmail.to"

    @classmethod
    def from_env(cls) -> "Secrets":
        missing = [k for k in ("AGENTMAIL_API_KEY", "AGENTMAIL_INBOX_ID", "ALERT_RECIPIENT") if not os.environ.get(k)]
        if missing:
            raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")
        return cls(
            agentmail_api_key=os.environ["AGENTMAIL_API_KEY"],
            inbox_id=os.environ["AGENTMAIL_INBOX_ID"],
            recipient=os.environ["ALERT_RECIPIENT"],
            base_url=os.environ.get("AGENTMAIL_BASE_URL", "https://api.agentmail.to"),
        )


def load_config(path: str | Path) -> Config:
    path = Path(path)
    with path.open("rb") as fh:
        data = tomllib.load(fh)

    profile = Profile(**data["profile"])
    if not profile.title_core:
        raise ValueError("profile.title_core must list at least one title phrase")

    companies = [Company(**c) for c in data.get("companies", [])]
    for c in companies:
        if c.source not in BOARD_FETCHERS:
            raise ValueError(f"Unknown board source {c.source!r} for {c.slug}")
    feeds = [Feed(**f) for f in data.get("feeds", [])]
    for f in feeds:
        if f.source not in FEED_FETCHERS:
            raise ValueError(f"Unknown feed source {f.source!r}")
    if not companies and not feeds:
        raise ValueError("config lists no companies or feeds")

    signals = [Signal(**s) for s in data.get("signals", [])]
    known = {s.name for s in signals}
    projects = [Project(**p) for p in data.get("projects", [])]
    for p in projects:
        unknown = set(p.triggers) - known
        if unknown:
            raise ValueError(f"project {p.name!r} references unknown signals {sorted(unknown)}")

    state_path = Path(data.get("state", {}).get("path", "state/sent.json"))
    if not state_path.is_absolute():
        state_path = path.parent / state_path
    return Config(
        candidate=Candidate(**data["candidate"]),
        profile=profile,
        locations=Locations(**data["locations"]),
        signals=signals,
        concerns=[Signal(**s) for s in data.get("concerns", [])],
        projects=projects,
        email=EmailSettings(**data.get("email", {})),
        companies=companies,
        feeds=feeds,
        state_path=state_path,
        verified={v["url"]: Verification(**v) for v in data.get("verified", [])},
        verification_valid_days=data.get("verification", {}).get("valid_days", 14),
    )
