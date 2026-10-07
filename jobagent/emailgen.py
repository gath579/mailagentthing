"""Analysis + email generation: one digest email per run with all new matches."""
from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from .models import Job

_SKIP_LINE_RE = re.compile(r"^(about (us|the company)|who we are|our mission|benefits|perks)\b", re.I)


@dataclass
class Email:
    subject: str
    text: str
    html: str


def summarize(job: Job, limit: int = 420) -> str:
    """A short role summary taken from the posting itself (no invented content)."""
    lines = [ln.strip() for ln in job.description.splitlines() if len(ln.strip()) > 40]
    lines = [ln for ln in lines if not _SKIP_LINE_RE.match(ln)]
    text = " ".join(lines)
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(",.;:") + "…"


def _meta(job: Job) -> str:
    parts = [job.location or "Location not listed"]
    if job.remote and "remote" not in job.location.lower():
        parts.append("Remote")
    if job.employment_type:
        parts.append(job.employment_type)
    if job.department:
        parts.append(job.department)
    return " · ".join(parts)


def build_email(jobs: list[Job], subject_prefix: str, now: datetime | None = None) -> Email:
    if not jobs:
        raise ValueError("build_email needs at least one job")
    now = now or datetime.now(timezone.utc)
    top = jobs[0]
    n = len(jobs)
    subject = f"{subject_prefix} {n} new match{'es' if n != 1 else ''}: {top.title} at {top.company}"
    if n > 1:
        subject += f" + {n - 1} more"

    text_parts = [f"{n} new job{'s' if n != 1 else ''} matched your profile.\n"]
    html_parts = [
        "<div style=\"font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;max-width:680px;"
        "color:#1a1a1a;line-height:1.45\">",
        f"<p style=\"font-size:15px\">{n} new job{'s' if n != 1 else ''} matched your profile.</p>",
    ]
    for i, job in enumerate(jobs, 1):
        summary = summarize(job)
        text_parts.append(
            f"{i}. {job.title} — {job.company}  (score {job.score:.0f}/100)\n"
            f"   {_meta(job)}\n"
            + (f"   Compensation: {job.compensation}\n" if job.compensation else "")
            + "".join(f"   • {r}\n" for r in job.reasons)
            + (f"   {summary}\n" if summary else "")
            + f"   Apply: {job.url}\n"
        )
        e = html.escape
        html_parts.append(
            "<div style=\"border:1px solid #e3e3e3;border-radius:8px;padding:14px 16px;margin:12px 0\">"
            f"<div style=\"font-size:17px;font-weight:600\"><a href=\"{e(job.url)}\" "
            f"style=\"color:#0b57d0;text-decoration:none\">{e(job.title)}</a></div>"
            f"<div style=\"font-size:14px;margin:2px 0 6px\"><b>{e(job.company)}</b> · {e(_meta(job))}"
            f" · score {job.score:.0f}/100</div>"
            + (f"<div style=\"font-size:13px\">Compensation: {e(job.compensation)}</div>" if job.compensation else "")
            + "<ul style=\"font-size:13px;margin:6px 0;padding-left:18px\">"
            + "".join(f"<li>{e(r)}</li>" for r in job.reasons)
            + "</ul>"
            + (f"<p style=\"font-size:13px;color:#444;margin:6px 0\">{e(summary)}</p>" if summary else "")
            + f"<a href=\"{e(job.url)}\" style=\"font-size:13px\">View posting →</a></div>"
        )
    footer = (f"Sources: official Greenhouse / Lever / Ashby job-board APIs. "
              f"Generated {now.strftime('%Y-%m-%d %H:%M UTC')}.")
    text_parts.append(footer)
    html_parts.append(f"<p style=\"font-size:12px;color:#777\">{html.escape(footer)}</p></div>")
    return Email(subject=subject, text="\n".join(text_parts), html="".join(html_parts))
