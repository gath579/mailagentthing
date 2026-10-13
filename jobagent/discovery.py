"""Discovery: query every configured company board and feed; collect raw postings."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .config import Company, Feed
from .models import RawPosting
from .sources import Fetcher, fetch

log = logging.getLogger(__name__)


class DiscoveryError(RuntimeError):
    """Raised when no source could be reached at all."""


@dataclass
class DiscoveryResult:
    postings: list[RawPosting] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)   # "source/key" -> postings
    errors: dict[str, str] = field(default_factory=dict)   # "source/key" -> error
    skipped: list[str] = field(default_factory=list)       # feeds throttled this run


def feed_due(feed: Feed, now: datetime) -> bool:
    """Stateless throttle: on scheduled runs fetch a feed only every N hours."""
    return feed.min_interval_hours <= 1 or now.hour % feed.min_interval_hours == 0


def discover_jobs(companies: list[Company], feeds: list[Feed] | None = None,
                  fetchers: dict[str, Fetcher] | None = None, *, now: datetime | None = None,
                  throttle_feeds: bool = False, max_workers: int = 12) -> DiscoveryResult:
    """Fetch postings from every board and (due) feed.

    A failing source is logged and skipped; if *every* source fails the run is
    broken and DiscoveryError is raised, so an outage or network block never
    looks like "no new jobs".
    """
    now = now or datetime.now(timezone.utc)
    result = DiscoveryResult()
    tasks = [(c.source, c.slug) for c in companies]
    for f in feeds or []:
        if throttle_feeds and not feed_due(f, now):
            result.skipped.append(f"{f.source}/{f.query}")
        else:
            tasks.append((f.source, f.query))

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(fetch, source, key, fetchers) for source, key in tasks]
        for (source, key), future in zip(tasks, futures):
            label = f"{source}/{key}"
            try:
                postings = future.result()
            except Exception as exc:  # noqa: BLE001 - isolate per-source failures
                result.errors[label] = str(exc)[:300]
                log.warning("discovery failed for %s: %s", label, exc)
                continue
            result.counts[label] = len(postings)
            result.postings.extend(postings)
            log.info("discovered %d postings from %s", len(postings), label)

    if tasks and not result.counts:
        raise DiscoveryError(f"All {len(tasks)} sources failed: {result.errors}")
    return result
