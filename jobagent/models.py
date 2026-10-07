"""Core data types shared by every pipeline stage."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional


@dataclass
class RawPosting:
    """A posting exactly as a source returned it, before normalization."""

    source: str          # e.g. "greenhouse"
    company_slug: str    # board token / site name used in the API URL
    payload: dict        # untouched JSON object from the source


@dataclass
class Job:
    """A normalized job posting. Every source maps into this shape."""

    source: str
    source_id: str
    company: str
    title: str
    url: str
    location: str = ""
    remote: bool = False
    department: str = ""
    employment_type: str = ""
    description: str = ""
    posted_at: Optional[datetime] = None
    compensation: str = ""
    # Filled in by later stages.
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    also_seen_at: list[str] = field(default_factory=list)

    @property
    def source_key(self) -> str:
        """Unique within a source; stable across runs."""
        return f"{self.source}:{self.company.lower()}:{self.source_id}"

    @property
    def fingerprint(self) -> str:
        """Source-independent identity used for cross-source dedup and notification dedup."""
        from .normalize import canonical_text

        return "|".join(
            (canonical_text(self.company), canonical_text(self.title), canonical_text(self.location))
        )

    def to_dict(self) -> dict:
        d = asdict(self)
        d["posted_at"] = self.posted_at.isoformat() if self.posted_at else None
        return d
