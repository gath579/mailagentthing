"""Per-job analysis + email generation.

Every statement about the candidate comes from config.toml ([candidate] and
[[projects]]); every statement about the role comes from the posting itself.
Nothing is invented: no metrics, no claimed research/data/enterprise/AI depth.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from .config import Config, Project
from .models import Job

_ROLE_LINE_RE = re.compile(r"\b(you will|you'll|you’ll|responsib|in this role|the role|what you'll do|"
                           r"what you will do|you will be|we're looking for|we are looking for|as a)\b", re.I)
_SKIP_LINE_RE = re.compile(r"^(about (us|the company)|who we are|our mission|benefits|perks|equal opportunity)",
                           re.I)

HOOKS = {
    "end_to_end": "owning features end to end",
    "problem_definition": "framing the problem before designing screens",
    "interaction": "designing interaction flows",
    "research": "learning from users",
    "figma_prototyping": "prototyping in Figma",
    "design_systems": "keeping a growing product consistent through a design system",
    "complex": "designing for complex states and workflows",
    "consumer": "designing products people use every day",
    "collaboration": "working closely with product and engineering",
}


@dataclass
class Email:
    subject: str
    text: str
    html: str


def summarize(job: Job, limit: int = 480) -> str:
    """A short role summary taken from the posting text (no invented content)."""
    lines = [ln.strip() for ln in job.description.splitlines() if len(ln.strip()) > 30]
    lines = [ln for ln in lines if not _SKIP_LINE_RE.match(ln)]
    role_lines = [ln for ln in lines if _ROLE_LINE_RE.search(ln)]
    picked, total = [], 0
    for ln in role_lines + [ln for ln in lines if ln not in role_lines]:
        if total + len(ln) > limit and picked:
            break
        picked.append(ln if ln[-1] in ".!?:;" else ln + ".")
        total += len(ln) + 2
    text = " ".join(picked)
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0].rstrip(",.;:") + "…"
    return text or "The posting has no description text — open the link for details."


def pick_projects(job: Job, projects: list[Project]) -> list[Project]:
    scored = []
    for p in projects:
        hits = len(set(p.triggers) & set(job.signals))
        if p.always or hits:
            scored.append((p.always, hits, p))
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return [p for _, _, p in scored]


def _posted(job: Job, now: datetime) -> str:
    if not job.posted_at:
        return "Not stated"
    days = (now - job.posted_at).days
    when = "today" if days < 1 else "yesterday" if days < 2 else f"{days} days ago"
    return f"{job.posted_at.strftime('%d %b %Y')} ({when})"


def _work_mode(job: Job) -> str:
    return {"remote": "Remote", "hybrid": "Hybrid", "onsite": "On-site"}.get(job.work_mode, "Not stated")


def application_text(job: Job, config: Config, projects: list[Project]) -> str:
    cand = config.candidate
    hooks = [HOOKS[s] for s in job.signals if s in HOOKS][:3]
    parts = [f"Hi {job.company} team,", f"{cand.intro} I'd like to apply for the {job.title} role."]
    if hooks:
        focus = hooks[0] if len(hooks) == 1 else ", ".join(hooks[:-1]) + " and " + hooks[-1]
        parts.append(f"Your posting's emphasis on {focus} is the kind of work I most want to keep growing in.")
    parts += [p.pitch for p in projects]
    if "research" in job.signals:
        parts.append("My research so far has been competitor research and close work with domain experts and "
                     "developers; I'd value joining a team with an established user-research practice.")
    parts.append(f"My portfolio is at {cand.portfolio}. I'd welcome the chance to talk about how I could "
                 f"contribute to {job.company}.")
    parts.append(f"Thank you,\n{cand.name}")
    return "\n\n".join(parts)


def build_job_email(job: Job, config: Config, now: datetime | None = None) -> Email:
    now = now or datetime.now(timezone.utc)
    projects = pick_projects(job, config.projects)
    subject = f"[{job.tier} Match] {job.title} — {job.company}"
    summary = summarize(job)
    concerns = job.concerns or ["No specific mismatch found in the posting text — still check team size, "
                                "design maturity and the interview process."]
    facts = [
        ("Company", job.company), ("Role", job.title),
        ("Location", job.location or "Not stated"), ("Work mode", _work_mode(job)),
        ("Posted", _posted(job, now)), ("Experience asked", job.experience or "Not stated"),
        ("Source", job.source_label),
        ("Posting link", {"live": f"Checked {now.strftime('%d %b %Y %H:%M UTC')}: page loads and lists this role",
                          "": "Not checked"}.get(job.link_status, f"{job.link_status}: {job.link_detail}")),
        ("Fit", f"{job.tier} match · score {job.score:.0f}/100"),
    ]
    if job.compensation:
        facts.insert(6, ("Compensation", job.compensation))
    letter = application_text(job, config, projects)

    text = "\n".join([
        f"{job.title} — {job.company}",
        f"APPLY / JOB POSTING: {job.url}",
        "",
        *[f"{k}: {v}" for k, v in facts],
        "", "ROLE SUMMARY (from the posting)", summary,
        "", "WHY IT MATCHES YOU", *[f"• {r}" for r in job.reasons],
        "", "MISMATCHES / CONCERNS", *[f"• {c}" for c in concerns],
        "", "PORTFOLIO PROJECTS TO EMPHASIZE", *[f"• {p.why}" for p in projects],
        "", "READY-TO-COPY APPLICATION TEXT", "-" * 32, letter, "-" * 32,
        "", f"Also listed at: {', '.join(job.also_seen_at)}" if job.also_seen_at else "",
        f"Found via {job.source_label}. Generated {now.strftime('%Y-%m-%d %H:%M UTC')}.",
    ])

    e = html.escape
    section = lambda title, body: (  # noqa: E731
        f"<h3 style=\"font-size:14px;margin:18px 0 6px;color:#333;text-transform:uppercase;"
        f"letter-spacing:.04em\">{e(title)}</h3>{body}")
    bullets = lambda items: "<ul style=\"margin:0;padding-left:18px\">" + "".join(  # noqa: E731
        f"<li style=\"margin:3px 0\">{e(i)}</li>" for i in items) + "</ul>"
    rows = "".join(f"<tr><td style=\"padding:2px 12px 2px 0;color:#666\">{e(k)}</td>"
                   f"<td style=\"padding:2px 0\">{e(v)}</td></tr>" for k, v in facts)
    html_body = (
        "<div style=\"font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;max-width:680px;"
        "color:#1a1a1a;line-height:1.5;font-size:14px\">"
        f"<div style=\"font-size:19px;font-weight:600\">{e(job.title)} — {e(job.company)}</div>"
        f"<p style=\"margin:10px 0\"><a href=\"{e(job.url)}\" style=\"display:inline-block;background:#0b57d0;"
        f"color:#fff;padding:8px 14px;border-radius:6px;text-decoration:none;font-weight:600\">"
        f"Open job posting / apply →</a><br><span style=\"font-size:12px;color:#666\">{e(job.url)}</span></p>"
        f"<table style=\"font-size:13px;border-collapse:collapse\">{rows}</table>"
        + section("Role summary (from the posting)", f"<p style=\"margin:0\">{e(summary)}</p>")
        + section("Why it matches you", bullets(job.reasons))
        + section("Mismatches / concerns", bullets(concerns))
        + section("Portfolio projects to emphasize", bullets([p.why for p in projects]))
        + section("Ready-to-copy application text",
                  f"<pre style=\"white-space:pre-wrap;font-family:inherit;background:#f6f6f6;border:1px solid "
                  f"#e3e3e3;border-radius:6px;padding:12px;margin:0\">{e(letter)}</pre>")
        + f"<p style=\"font-size:12px;color:#777;margin-top:18px\">Found via {e(job.source_label)}. "
        f"Generated {now.strftime('%Y-%m-%d %H:%M UTC')}.</p></div>"
    )
    return Email(subject=subject, text=text, html=html_body)
