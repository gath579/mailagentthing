"""Discovery: query every configured company board and collect raw postings."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .config import Company
from .sources import Fetcher, fetch
from .models import RawPosting

log = logging.getLogger(__name__)


class DiscoveryError(RuntimeError):
    """Raised when no source could be reached at all."""


@dataclass
class DiscoveryResult:
    postings: list[RawPosting] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)   # "source/slug" -> postings
    errors: dict[str, str] = field(default_factory=dict)   # "source/slug" -> error


def discover_jobs(companies: list[Company], fetchers: dict[str, Fetcher] | None = None,
                  max_workers: int = 8) -> DiscoveryResult:
    """Fetch postings from every company board.

    A single failing board is logged and skipped; if *every* board fails the
    run is considered broken and DiscoveryError is raised, so that an outage
    or network block never looks like "no new jobs".
    """
    result = DiscoveryResult()

    def one(company: Company):
        return company, fetch(company.source, company.slug, fetchers)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(one, c) for c in companies]
        for company, future in zip(companies, futures):
            key = f"{company.source}/{company.slug}"
            try:
                _, postings = future.result()
            except Exception as exc:  # noqa: BLE001 - isolate per-board failures
                result.errors[key] = str(exc)
                log.warning("discovery failed for %s: %s", key, exc)
                continue
            result.counts[key] = len(postings)
            result.postings.extend(postings)
            log.info("discovered %d postings from %s", len(postings), key)

    if companies and not result.counts:
        raise DiscoveryError(f"All {len(companies)} job boards failed: {result.errors}")
    return result
