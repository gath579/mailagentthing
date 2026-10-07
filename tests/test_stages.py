from datetime import timedelta

import pytest

from jobagent.config import Company, load_config
from jobagent.dedupe import dedupe_sources
from jobagent.discovery import DiscoveryError, discover_jobs
from jobagent.emailgen import build_email
from jobagent.models import Job, RawPosting
from jobagent.normalize import html_to_text, normalize, normalize_all
from jobagent.scoring import evaluate, filter_and_score
from jobagent.state import SentStore

from .conftest import ASHBY_JOBS, GREENHOUSE_JOBS, LEVER_JOBS, NOW, fake_fetchers


# ---- discovery ---------------------------------------------------------------

def test_discovery_collects_all_boards(config):
    result = discover_jobs(config.companies, fake_fetchers())
    assert result.counts == {"greenhouse/acme": 2, "lever/globex": 2, "ashby/initech": 2}
    assert not result.errors


def test_discovery_isolates_failing_board(config):
    result = discover_jobs(config.companies, fake_fetchers(fail={"lever"}))
    assert "lever/globex" in result.errors
    assert len(result.postings) == 4


def test_discovery_raises_when_everything_fails(config):
    with pytest.raises(DiscoveryError):
        discover_jobs(config.companies, fake_fetchers(fail={"greenhouse", "lever", "ashby"}))


# ---- normalization -----------------------------------------------------------

def test_html_to_text_handles_greenhouse_double_escaping():
    text = html_to_text(GREENHOUSE_JOBS[0]["content"])
    assert "distributed systems in Python" in text
    assert "<" not in text and "&lt;" not in text


def test_normalize_greenhouse():
    job = normalize(RawPosting("greenhouse", "acme", GREENHOUSE_JOBS[0]), {"acme": "Acme"})
    assert (job.company, job.title, job.remote) == ("Acme", "Senior Backend Engineer", True)
    assert job.posted_at == NOW - timedelta(days=2)  # first_published preferred over updated_at
    assert job.department == "Platform"


def test_normalize_lever():
    job = normalize(RawPosting("lever", "globex", LEVER_JOBS[0]))
    assert job.company == "Globex"
    assert job.location == "San Francisco, CA" and not job.remote
    assert "Kubernetes" in job.description
    assert job.compensation.startswith("USD 180,000")
    assert job.posted_at == NOW - timedelta(days=5)


def test_normalize_ashby():
    job = normalize(RawPosting("ashby", "initech", ASHBY_JOBS[1]))
    assert job.remote and job.compensation == "$170K – $220K"


def test_normalize_all_reports_bad_payloads():
    jobs, errors = normalize_all([RawPosting("lever", "x", {"text": "no id"}),
                                  RawPosting("ashby", "x", ASHBY_JOBS[0])])
    assert len(jobs) == 1 and len(errors) == 1


# ---- source dedup ------------------------------------------------------------

def _job(**kw):
    base = dict(source="greenhouse", source_id="1", company="Acme", title="Software Engineer",
                url="https://a.test/1", location="Remote")
    base.update(kw)
    return Job(**base)


def test_dedupe_merges_same_job_across_sources_keeping_richest():
    a = _job(description="short")
    b = _job(source="lever", source_id="x", url="https://b.test/x", description="much longer description",
             posted_at=NOW)
    c = _job(source_id="2", title="Data Engineer", url="https://a.test/2")
    out = dedupe_sources([a, b, c])
    assert len(out) == 2
    assert out[0] is b and out[0].also_seen_at == ["https://a.test/1"]


def test_dedupe_by_url_and_source_key():
    a = _job()
    b = _job(title="Software Engineer II")  # same source key + url, title edited
    assert len(dedupe_sources([a, b])) == 1


# ---- filtering / scoring -----------------------------------------------------

def _all_jobs():
    raws = [RawPosting("greenhouse", "acme", p) for p in GREENHOUSE_JOBS] + \
           [RawPosting("lever", "globex", p) for p in LEVER_JOBS] + \
           [RawPosting("ashby", "initech", p) for p in ASHBY_JOBS]
    return normalize_all(raws)[0]


def test_filter_and_score(profile):
    matched, rejected = filter_and_score(_all_jobs(), profile, NOW)
    titles = [j.title for j in matched]
    assert titles == ["Senior Backend Engineer", "Platform Engineer", "Software Engineer, Infrastructure"]
    reasons = {r.job.title: r.reason for r in rejected}
    assert "does not match" in reasons["Engineering Manager, Payments"]
    assert "days ago" in reasons["Software Engineer"]          # 90 days old
    assert "not in preferred" in reasons["Machine Learning Engineer"]  # London, not remote


def test_score_explains_itself(profile):
    job = normalize(RawPosting("greenhouse", "acme", GREENHOUSE_JOBS[0]))
    assert evaluate(job, profile, NOW) is None
    assert any("python" in r for r in job.reasons)
    assert "Remote-friendly" in job.reasons
    assert 50 <= job.score <= 100


def test_title_exclude(profile):
    job = _job(title="Software Engineer Intern", posted_at=NOW)
    assert "excluded term 'intern'" in evaluate(job, profile, NOW)


def test_min_score_rejects(profile):
    profile.min_score = 99
    job = normalize(RawPosting("lever", "globex", LEVER_JOBS[0]))
    assert evaluate(job, profile, NOW).startswith("score")


def test_short_location_terms_use_word_boundaries(profile):
    job = _job(location="Austin, TX", posted_at=NOW)
    job.remote = False
    assert "not in preferred" in evaluate(job, profile, NOW)   # "us" must not match "austin"


# ---- notification dedup ------------------------------------------------------

def test_sent_store_roundtrip(tmp_path):
    store = SentStore(tmp_path / "s.json")
    job = _job()
    assert store.unsent([job]) == [job]
    store.record_sent([job], {"message_id": "m", "thread_id": "t"}, NOW)
    store.save()
    again = SentStore(tmp_path / "s.json")
    assert again.already_sent(_job())
    assert again.already_sent(_job(title="Renamed Title"))   # same source key
    assert again.already_sent(_job(source="lever", source_id="z", url="https://z"))  # same fingerprint


def test_sent_store_prunes_old_entries(tmp_path):
    store = SentStore(tmp_path / "s.json")
    store.record_sent([_job()], {"message_id": "m", "thread_id": "t"}, NOW - timedelta(days=400))
    store.prune(NOW)
    assert store.data["sent"] == {}


# ---- email generation --------------------------------------------------------

def test_build_email(profile):
    matched, _ = filter_and_score(_all_jobs(), profile, NOW)
    email = build_email(matched, "[Job Alert]", NOW)
    assert email.subject == "[Job Alert] 3 new matches: Senior Backend Engineer at Acme + 2 more"
    for job in matched:
        assert job.url in email.text and job.url in email.html
    assert "USD 180,000" in email.text


def test_build_email_escapes_html():
    job = _job(title="<script>x</script> Engineer", score=60, reasons=["a & b"])
    email = build_email([job], "[J]", NOW)
    assert "<script>" not in email.html and "&lt;script&gt;" in email.html


# ---- config ------------------------------------------------------------------

def test_repo_config_loads():
    from pathlib import Path
    cfg = load_config(Path(__file__).parent.parent / "config.toml")
    assert cfg.companies and all(isinstance(c, Company) for c in cfg.companies)
    assert cfg.state_path.name == "sent.json"
