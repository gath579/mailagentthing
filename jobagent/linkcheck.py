"""Check that a job's posting URL is directly and currently accessible.

An ATS API still returning a record is not proof the role is hiring, so before
sending we load the posting page itself. Result:

  live        200, the job title is on the page, no closed/expired markers,
              and no redirect to a generic careers page
  closed      404/410, or the page says the job is closed/expired/filled
  redirected  sent somewhere that no longer identifies this job
  unverified  could not confirm (blocked, timeout, client-rendered page, ...)

Only `live` jobs are emailed when email.require_live_link is true.
"""
from __future__ import annotations

import re
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

from .models import Job
from .normalize import canonical_text, html_to_text

BROWSER_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/126.0 Safari/537.36 mailagentthing-linkcheck")

CLOSED_MARKERS = [
    "no longer accepting applications", "job is no longer available", "this job is no longer",
    "position has been filled", "job not found", "posting has expired", "this job has expired",
    "job has expired", "no longer open", "job has been closed", "this position is closed",
    "position is no longer available", "role has been filled",
    "this posting is closed", "job posting is no longer", "opening is no longer",
]
_TITLE_STOP = {"and", "the", "of", "for", "a", "an", "to", "in", "at", "with", "ii", "iii", "i"}


def _fetch(url: str, timeout: float = 20):
    req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA, "Accept": "text/html,*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.geturl(), resp.read(1_500_000).decode("utf-8", "replace")


def _identifies_job(original: str, final: str, job: Job) -> bool:
    if final.rstrip("/") == original.rstrip("/"):
        return True
    ident = str(job.source_id)
    return bool(ident) and ident in final


def _title_on_page(title: str, page_text: str) -> bool:
    words = [w for w in canonical_text(title).split() if w not in _TITLE_STOP and len(w) > 1]
    if not words:
        return True
    found = sum(1 for w in words if re.search(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])", page_text))
    return found >= max(1, round(len(words) * 0.75))


def check(job: Job) -> tuple[str, str]:
    try:
        status, final, body = _fetch(job.url)
    except urllib.error.HTTPError as exc:
        if exc.code in (404, 410):
            return "closed", f"HTTP {exc.code}"
        return "unverified", f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001
        return "unverified", f"fetch failed: {type(exc).__name__}"
    if status != 200:
        return "unverified", f"HTTP {status}"
    text = canonical_text(html_to_text(body))
    lowered = re.sub(r"\s+", " ", body.lower())
    marker = next((m for m in CLOSED_MARKERS if m in lowered), None)
    if marker:
        return "closed", f"page says “{marker}”"
    if "error=true" in final:
        return "closed", f"redirected to {final} (ATS closed-job redirect)"
    if not _identifies_job(job.url, final, job):
        return "redirected", f"redirected to {urlparse(final).netloc}{urlparse(final).path}"
    if not _title_on_page(job.title, text):
        return "unverified", "page loads but the job title isn't in its HTML (client-rendered or generic page)"
    return "live", f"HTTP 200, title present ({urlparse(final).netloc})"


def check_all(jobs: list[Job], checker=check, max_workers: int = 8) -> None:
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for job, (status, detail) in zip(jobs, pool.map(checker, jobs)):
            job.link_status, job.link_detail = status, detail
