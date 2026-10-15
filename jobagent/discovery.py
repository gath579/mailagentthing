"""Discovery: query every configured company board and feed; collect raw postings."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

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
    fetched_feeds: list[str] = field(default_factory=list) # feeds fetched successfully this run

# Scheduled runs start late and irregularly, so allow a little slack before an interval counts as elapsed.
DUE_SLACK = timedelta(minutes=15)


def feed_label(feed: Feed) -> str:
    return f"{feed.source}/{feed.query}"


def feed_due(feed: Feed, now: datetime, last_fetched: datetime | None) -> bool:
    """On scheduled runs fetch a feed only if its min interval has elapsed since the last successful fetch."""
    if feed.min_interval_hours <= 1 or last_fetched is None:
        return True
    return now - last_fetched >= timedelta(hours=feed.min_interval_hours) - DUE_SLACK


def discover_jobs(companies: list[Company], feeds: list[Feed] | None = None,
                  fetchers: dict[str, Fetcher] | None = None, *, now: datetime | None = None,
                  throttle_feeds: bool = False, feed_last_fetched: dict[str, str] | None = None,
                  max_workers: int = 12) -> DiscoveryResult:
    """Fetch postings from every board and (due) feed.

    A failing source is logged and skipped; if *every* source fails the run is
    broken and DiscoveryError is raised, so an outage or network block never
    looks like "no new jobs".
    """
    now = now or datetime.now(timezone.utc)
    result = DiscoveryResult()
    tasks = [(c.source, c.slug) for c in companies]
    feed_labels = set()
    last = feed_last_fetched or {}
    for f in feeds or []:
        label = feed_label(f)
        prev = datetime.fromisoformat(last[label]) if label in last else None
        if throttle_feeds and not feed_due(f, now, prev):
            result.skipped.append(label)
        else:
            tasks.append((f.source, f.query))
            feed_labels.add(label)

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
            if label in feed_labels:
                result.fetched_feeds.append(label)
            result.postings.extend(postings)
            log.info("discovered %d postings from %s", len(postings), label)

    if tasks and not result.counts:
        raise DiscoveryError(f"All {len(tasks)} sources failed: {result.errors}")
    return result
