from datetime import timedelta

import pytest

from jobagent.dedupe import dedupe_sources
from jobagent.discovery import DiscoveryError, discover_jobs, feed_due
from jobagent.config import Feed
from jobagent.emailgen import build_job_email, pick_projects
from jobagent.models import Job, RawPosting
from jobagent.normalize import html_to_text, normalize, normalize_all
from jobagent.scoring import evaluate, filter_and_score, parse_experience
from jobagent.state import SentStore

from .conftest import (ASHBY_JOBS, FEED_SAMPLES, GREENHOUSE_JOBS, LEVER_JOBS, NOW, REMOTIVE_JOBS,
                       fake_fetchers)


def _job(**kw):
    base = dict(source="greenhouse", source_id="1", company="Acme", title="Product Designer",
                url="https://a.test/1", location="Pune, India", work_mode="hybrid", posted_at=NOW,
                description="2-4 years of experience. Figma prototyping, user flows, design system.")
    base.update(kw)
    return Job(**base)


# ---- discovery ---------------------------------------------------------------

def test_discovery_boards_and_feeds(config):
    result = discover_jobs(config.companies, config.feeds, fake_fetchers(), now=NOW)
    assert result.counts["greenhouse/acme"] == 4
    assert result.counts["remotive/design"] == 2
    assert not result.errors


def test_discovery_isolates_failures_and_raises_when_all_fail(config):
    result = discover_jobs(config.companies, config.feeds, fake_fetchers(fail={"lever"}), now=NOW)
    assert "lever/globex-in" in result.errors and result.postings
    everything = {"greenhouse", "lever", "ashby", "remotive", "remoteok", "jobicy", "himalayas", "weworkremotely"}
    with pytest.raises(DiscoveryError):
        discover_jobs(config.companies, config.feeds, fake_fetchers(fail=everything), now=NOW)


def test_feed_throttle_only_on_scheduled_runs(config):
    six = Feed("remotive", "design", 6)
    assert feed_due(six, NOW.replace(hour=12)) and not feed_due(six, NOW.replace(hour=13))
    result = discover_jobs([], config.feeds, fake_fetchers(), now=NOW.replace(hour=13), throttle_feeds=True)
    assert "remotive/design" in result.skipped and "remotive/design" not in result.counts


# ---- normalization -----------------------------------------------------------

def test_html_to_text_handles_double_escaping():
    text = html_to_text(GREENHOUSE_JOBS[0]["content"])
    assert "own features end to end" in text and "<" not in text


@pytest.mark.parametrize("source,payload,title,mode", [
    ("greenhouse", GREENHOUSE_JOBS[0], "Product Designer", ""),
    ("lever", LEVER_JOBS[0], "UI/UX Designer", "hybrid"),
    ("ashby", ASHBY_JOBS[0], "Interaction Designer", "remote"),
    ("remotive", REMOTIVE_JOBS[0], "Product Designer", "remote"),
    ("remoteok", FEED_SAMPLES["remoteok"][1], "UX Designer", "remote"),
    ("jobicy", FEED_SAMPLES["jobicy"][0], "Product Designer", "remote"),
    ("himalayas", FEED_SAMPLES["himalayas"][0], "UI Designer", "remote"),
    ("weworkremotely", FEED_SAMPLES["weworkremotely"][0], "Product Designer", "remote"),
    ("smartrecruiters", {"id": "9", "name": "Product Designer", "releasedDate": "2026-10-05T10:00:00.000Z",
                         "company": {"identifier": "Bosch", "name": "Bosch"},
                         "location": {"city": "Bengaluru", "country": "in", "hybrid": True},
                         "_detail": {"jobAd": {"sections": {"jobDescription": {"text": "<p>Figma</p>"}}}}},
     "Product Designer", "hybrid"),
    ("workable", {"shortcode": "AB1", "title": "UX Designer", "url": "https://example.test/w/AB1",
                  "telecommuting": False, "locations": [{"city": "Mumbai", "country": "India"}],
                  "published_on": "2026-10-05", "description": "<p>flows</p>", "_company": "Wonka"},
     "UX Designer", ""),
    ("recruitee", {"id": 3, "title": "Product Designer", "careers_url": "https://example.test/r/3",
                   "location": "Pune, India", "remote": False, "hybrid": True, "published_at": "2026-10-05 10:00:00 UTC",
                   "description": "<p>x</p>", "requirements": "<p>y</p>"},
     "Product Designer", "hybrid"),
])
def test_normalize_every_source(source, payload, title, mode):
    job = normalize(RawPosting(source, "slug", payload))
    assert job.title == title and job.url and job.work_mode == mode
    assert job.source_label


def test_feed_specifics():
    wwr = normalize(RawPosting("weworkremotely", "q", FEED_SAMPLES["weworkremotely"][0]))
    assert (wwr.company, wwr.location) == ("Vandelay", "Remote (Anywhere in the World)")
    rem = normalize(RawPosting("remotive", "design", REMOTIVE_JOBS[1]))
    assert rem.company == "Initech" and rem.location == "Remote (USA Only)"


def test_normalize_all_reports_bad_payloads():
    jobs, errors = normalize_all([RawPosting("lever", "x", {"text": "no id"}),
                                  RawPosting("ashby", "x", ASHBY_JOBS[0])])
    assert len(jobs) == 1 and len(errors) == 1


# ---- source dedup ------------------------------------------------------------

def test_dedupe_merges_same_job_across_sources_keeping_richest():
    a = _job(description="short")
    b = _job(source="lever", source_id="x", url="https://b.test/x", description="much longer description text")
    c = _job(source_id="2", title="UX Designer", url="https://a.test/2")
    out = dedupe_sources([a, b, c])
    assert len(out) == 2 and out[0] is b and out[0].also_seen_at == ["https://a.test/1"]


# ---- scoring -----------------------------------------------------------------

@pytest.mark.parametrize("title,ok", [
    ("Product Designer", True), ("UI/UX Designer", True), ("UX/UI Designer", True), ("UX Designer II", True),
    ("Interaction Designer", True), ("Associate Product Designer", True), ("Designer, Product", True),
    ("Senior Product Designer", False), ("Sr. UX Designer", False), ("Lead Product Designer", False),
    ("Staff Product Designer", False), ("Product Design Manager", False), ("Head of Design", False),
    ("Graphic Designer", False), ("Brand Designer", False), ("Marketing Designer", False),
    ("Web Designer", False), ("UX Researcher", False), ("UX Designer & Researcher", True),
    ("Design Engineer", False), ("Software Engineer", False), ("Product Design Intern", False),
    ("Game UI Designer", False), ("Visual Designer", False),
])
def test_title_rules(config, title, ok):
    reason = evaluate(_job(title=title), config, NOW)
    assert (reason is None) == ok, reason
    if not ok:
        assert reason.startswith("title")


@pytest.mark.parametrize("text,expected", [
    ("2-4 years of experience in product design", (2, 4)),
    ("3+ years of experience designing products", (3, None)),
    ("Minimum 5 years experience", (5, None)),
    ("1+ years with Figma and 4+ years of product design experience", (4, None)),
    ("We have served 10 years of customers", None),
])
def test_parse_experience(text, expected):
    got = parse_experience(text)
    assert (got[:2] if got else None) == expected


def test_experience_effects(config):
    assert evaluate(_job(description="Requires 7+ years of experience"), config, NOW).startswith("experience")
    unstated = _job(description="Figma")
    evaluate(unstated, config, NOW)
    assert unstated.experience == "not stated"
    five = _job(description="5+ years of experience required. Figma, flows, design system, end to end.")
    evaluate(five, config, NOW)
    assert any("stretch" in c for c in five.concerns)


@pytest.mark.parametrize("location,mode,expect", [
    ("Pune, India", "hybrid", "pass"),
    ("Bengaluru", "onsite", "pass"),
    ("Remote (India)", "remote", "pass"),
    ("Remote (Worldwide)", "remote", "pass"),
    ("Remote - US", "remote", "location"),
    ("Remote (USA Only)", "remote", "location"),
    ("London, UK", "onsite", "location"),
    ("San Francisco, CA; Bengaluru, India", "", "pass"),
])
def test_location_rules(config, location, mode, expect):
    job = _job(location=location, work_mode=mode,
               description="2-4 years of experience. End to end ownership, Figma prototyping, user flows, "
                           "design system, usability testing, consumer mobile app, product managers.")
    reason = evaluate(job, config, NOW)
    assert (reason is None) if expect == "pass" else reason.startswith(expect), reason


def test_location_priority_order(config):
    def score(loc, mode):
        j = _job(location=loc, work_mode=mode)
        evaluate(j, config, NOW)
        return j.score
    remote = score("Remote (India)", "remote")
    pune_h = score("Pune, India", "hybrid")
    pune_o = score("Pune, India", "onsite")
    hyd = score("Hyderabad, India", "hybrid")
    gurgaon = score("Gurugram, India", "hybrid")
    assert remote > pune_h > pune_o and pune_h > hyd > gurgaon


def test_deprioritized_city_flagged(config):
    j = _job(location="Gurugram, Haryana, India", work_mode="onsite")
    evaluate(j, config, NOW)
    assert any("deprioritized" in c for c in j.concerns)


def test_full_fixture_ranking(config):
    raws = [RawPosting("greenhouse", "acme", p) for p in GREENHOUSE_JOBS] + \
           [RawPosting("lever", "globex-in", p) for p in LEVER_JOBS] + \
           [RawPosting("ashby", "initech", p) for p in ASHBY_JOBS] + \
           [RawPosting("remotive", "design", p) for p in REMOTIVE_JOBS]
    jobs, _ = normalize_all(raws, config.company_names, config.company_sizes)
    matched, rejected = filter_and_score(jobs, config, NOW)
    titles = [(j.company, j.title) for j in matched]
    # Bengaluru/large org and worldwide-remote (Remotive) are the two strongest fixtures
    assert set(titles[:2]) == {("Acme", "Product Designer"), ("Globex", "Product Designer")}
    reasons = {(r.job.company, r.job.title): r.reason for r in rejected}
    assert reasons[("Acme", "Senior Product Designer")].startswith("title")
    assert reasons[("Initech", "Product Designer")].startswith(("location", "experience"))
    assert all(j.tier in ("Strong", "Good", "Possible") for j in matched)


# ---- notification dedup ------------------------------------------------------

def test_sent_store_roundtrip(tmp_path):
    store = SentStore(tmp_path / "s.json")
    job = _job()
    store.record_sent(job, {"message_id": "m", "thread_id": "t"}, NOW)
    store.mark_verified(job, True)
    store.save()
    again = SentStore(tmp_path / "s.json")
    assert again.already_sent(_job()) and again.already_sent(_job(title="Renamed Title"))
    assert again.already_sent(_job(source="lever", source_id="z", url="https://z"))
    assert again.data["sent"][job.fingerprint]["verified"] is True


def test_sent_store_prunes_old_entries(tmp_path):
    store = SentStore(tmp_path / "s.json")
    store.record_sent(_job(), {"message_id": "m", "thread_id": "t"}, NOW - timedelta(days=400))
    store.prune(NOW)
    assert store.data["sent"] == {}


# ---- email generation --------------------------------------------------------

def _scored(config, **kw):
    job = _job(**kw)
    assert evaluate(job, config, NOW) is None
    return job


def test_email_structure(config):
    job = _scored(config, description=GREENHOUSE_JOBS[0]["content"].replace("&lt;", "<").replace("&gt;", ">"),
                  location="Bengaluru, India")
    email = build_job_email(job, config, NOW)
    assert email.subject == f"[{job.tier} Match] Product Designer — Acme"
    lines = email.text.splitlines()
    assert lines[1] == "APPLY / JOB POSTING: https://a.test/1"           # link near the top
    for heading in ("ROLE SUMMARY", "WHY IT MATCHES YOU", "MISMATCHES / CONCERNS",
                    "PORTFOLIO PROJECTS TO EMPHASIZE", "READY-TO-COPY APPLICATION TEXT"):
        assert heading in email.text
    for label in ("Location:", "Work mode: Hybrid", "Posted:", "Source:"):
        assert label in email.text
    assert config.candidate.portfolio in email.text
    assert email.html.index("https://a.test/1") < email.html.index("Role summary")


def test_application_text_only_uses_configured_claims(config):
    job = _scored(config, description="2-4 years of experience. AI automation tools, Figma prototyping, "
                                      "user research, design system, end to end.")
    email = build_job_email(job, config, NOW)
    letter = email.text.split("-" * 32)[1]
    assert "Hi Acme team," in letter and "Product Designer role" in letter
    assert "FinalCheck" in letter                     # AI signal -> FinalCheck pitch
    assert "established user-research practice" in letter  # honest framing when research is asked
    for banned in ("%", "increased", "led a team", "startup founder", "product-market fit", "expert in AI"):
        assert banned not in letter


def test_project_selection(config):
    job = _job()
    job.signals = ["complex", "consumer"]
    assert [p.name for p in pick_projects(job, config.projects)] == ["Hapchi", "PlayStation Remote Downloads"]
    job.signals = []
    assert [p.name for p in pick_projects(job, config.projects)] == ["Hapchi"]


def test_email_escapes_html(config):
    job = _scored(config, title="Product Designer <script>x</script>")
    assert "<script>" not in build_job_email(job, config, NOW).html


def test_old_board_posting_flagged_but_old_feed_posting_dropped(config):
    old = NOW - timedelta(days=150)
    board = _job(posted_at=old)
    assert evaluate(board, config, NOW) is None
    assert any("evergreen" in c for c in board.concerns)
    feed = _job(source="remotive", location="Remote (Worldwide)", work_mode="remote", posted_at=old)
    assert evaluate(feed, config, NOW).startswith("posted")


def test_machine_learning_is_not_education(config):
    j = _job(description="2-4 years of experience. Machine learning products. Figma.")
    evaluate(j, config, NOW)
    assert "edtech" not in j.signals
