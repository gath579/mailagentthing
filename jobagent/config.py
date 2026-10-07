"""Configuration: search profile + company list from TOML, secrets from env."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

VALID_SOURCES = ("greenhouse", "lever", "ashby")


@dataclass
class Company:
    source: str
    slug: str
    name: str = ""


@dataclass
class Profile:
    title_include: list[str]
    title_exclude: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    location_exclude: list[str] = field(default_factory=list)
    remote_ok: bool = True
    prefer_remote: bool = False
    max_age_days: int = 30
    min_score: float = 50.0


@dataclass
class EmailSettings:
    max_jobs_per_email: int = 10
    subject_prefix: str = "[Job Alert]"


@dataclass
class Config:
    profile: Profile
    email: EmailSettings
    companies: list[Company]
    state_path: Path

    @property
    def company_names(self) -> dict[str, str]:
        return {c.slug: c.name for c in self.companies if c.name}


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
    if not profile.title_include:
        raise ValueError("profile.title_include must list at least one title phrase")

    companies = []
    for entry in data.get("companies", []):
        c = Company(**entry)
        if c.source not in VALID_SOURCES:
            raise ValueError(f"Unknown source {c.source!r} for {c.slug}; expected one of {VALID_SOURCES}")
        companies.append(c)
    if not companies:
        raise ValueError("config lists no companies")

    state_path = Path(data.get("state", {}).get("path", "state/sent.json"))
    if not state_path.is_absolute():
        state_path = path.parent / state_path
    return Config(
        profile=profile,
        email=EmailSettings(**data.get("email", {})),
        companies=companies,
        state_path=state_path,
    )
