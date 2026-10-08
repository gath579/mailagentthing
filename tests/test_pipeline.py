import json

import pytest

from jobagent.agentmail import AgentMailClient, DeliveryError
from jobagent.pipeline import PipelineError, run
from jobagent.probe import run_probe
from jobagent.state import SentStore

from .conftest import NOW, FakeAgentMail, fake_fetchers, fake_link_checker


def _run(config, tmp_path, secrets, fake, link_checker=None, **kw):
    return run(config, out_dir=tmp_path / "out", secrets=secrets, fetchers=fake_fetchers(),
               client=AgentMailClient(secrets, request=fake), now=NOW,
               link_checker=link_checker or fake_link_checker(), **kw)


def test_dry_run_sends_nothing_and_writes_previews(config, tmp_path, secrets):
    fake = FakeAgentMail()
    summary = _run(config, tmp_path, secrets, fake, send=False)
    assert summary["matched"] >= 3 and summary["sent"] == []
    assert fake.calls == [] and not config.state_path.exists()
    previews = sorted((tmp_path / "out" / "emails").glob("*.txt"))
    assert len(previews) == summary["new_unsent"]
    assert previews[0].read_text().startswith("Subject: [")
    assert "TOP" in (tmp_path / "out" / "top_matches.txt").read_text()


def test_one_email_per_job_each_recorded(config, tmp_path, secrets):
    fake = FakeAgentMail()
    summary = _run(config, tmp_path, secrets, fake, send=True)
    sends = [c for c in fake.calls if c["method"] == "POST"]
    assert len(sends) == summary["sendable"] == len(summary["sent"]) >= 3
    assert summary["sendable"] < summary["new_unsent"]          # Possible matches are report-only
    assert len({c["headers"]["Idempotency-Key"] for c in sends}) == len(sends)     # per-job keys
    assert all(c["body"]["subject"].split("]")[0] in ("[Strong Match", "[Good Match") for c in sends)
    assert all(c["url"].endswith("/v0/inboxes/scout%40agentmail.to/messages/send") for c in sends)
    state = json.loads(config.state_path.read_text())
    assert len(state["sent"]) == len(sends)
    assert all(v["verified"] is True for v in state["sent"].values())
    assert len({v["message_id"] for v in state["sent"].values()}) == len(sends)


def test_max_emails_one_then_rest_next_run(config, tmp_path, secrets):
    fake = FakeAgentMail()
    first = _run(config, tmp_path, secrets, fake, send=True, max_emails=1)
    assert len(first["sent"]) == 1
    second = _run(config, tmp_path, secrets, fake, send=True)
    assert len(second["sent"]) == first["sendable"] - 1
    third = _run(config, tmp_path, secrets, fake, send=True)
    assert third["sent"] == [] and third["sendable"] == 0


def test_failed_send_is_not_recorded_and_retried_next_run(config, tmp_path, secrets):
    fake = FakeAgentMail(fail_sends={1})
    with pytest.raises(DeliveryError, match="1 send failure"):
        _run(config, tmp_path, secrets, fake, send=True)
    store = SentStore(config.state_path)
    summary = json.loads((tmp_path / "out" / "run_summary.json").read_text())
    failed_title = summary["send_failures"][0]["title"]
    assert len(store.data["sent"]) == len(summary["sent"])          # successes recorded, failure not
    retry = _run(config, tmp_path, secrets, FakeAgentMail(), send=True)
    assert [s["title"] for s in retry["sent"]] == [failed_title]


def test_verification_failure_still_recorded_to_avoid_duplicates(config, tmp_path, secrets):
    fake = FakeAgentMail(tamper_subject=True)
    with pytest.raises(DeliveryError, match="verification failure"):
        _run(config, tmp_path, secrets, fake, send=True, max_emails=1)
    entry = next(iter(json.loads(config.state_path.read_text())["sent"].values()))
    assert entry["verified"] is False and "subject mismatch" in entry["verify_detail"]


def test_require_sent_when_nothing_new(config, tmp_path, secrets):
    fake = FakeAgentMail()
    _run(config, tmp_path, secrets, fake, send=True)
    with pytest.raises(PipelineError):
        _run(config, tmp_path, secrets, fake, send=True, require_sent=True)


def test_probe_reports_live_boards(config, tmp_path):
    cands = tmp_path / "c.toml"
    cands.write_text('slugs = ["acme"]')
    found = run_probe(cands, config.feeds, tmp_path / "out", fetchers=fake_fetchers())
    gh = next(r for r in found if r["source"] == "greenhouse")
    assert gh["total"] == 4 and gh["design"] == 4 and gh["design_india"] == 3
    assert (tmp_path / "out" / "probe.json").exists()


def test_only_strong_good_unconditional_live_jobs_are_sent(config, tmp_path, secrets):
    fake = FakeAgentMail()
    closed_url = "https://example.test/remotive/99"
    summary = _run(config, tmp_path, secrets, fake, send=True,
                   link_checker=fake_link_checker(closed={closed_url}))
    matches = json.loads((tmp_path / "out" / "matches.json").read_text())
    sent_titles = {(s["company"], s["title"]) for s in summary["sent"]}
    for m in matches:
        key = (m["company"], m["title"])
        if m["send_status"] == "WILL SEND":
            assert key in sent_titles
        else:
            assert key not in sent_titles
    statuses = {m["url"]: m["send_status"] for m in matches}
    assert statuses[closed_url].startswith("NOT SENT: link closed")
    assert any(s.startswith("report only") for s in statuses.values())
    sends = [c for c in fake.calls if c["method"] == "POST"]
    assert all(c["body"]["subject"].startswith(("[Strong Match]", "[Good Match]")) for c in sends)


def test_conditional_remote_job_not_sent(config, tmp_path, secrets):
    from jobagent.models import RawPosting  # noqa: F401
    fetchers = fake_fetchers()
    fetchers["remotive"] = lambda q: [{
        "id": 7, "url": "https://example.test/remotive/7", "title": "Product Designer", "company_name": "Nowhere Region Co",
        "publication_date": NOW.isoformat(), "candidate_required_location": "",
        "description": "2-4 years of experience. End to end, Figma prototyping, user flows, design system."}]
    fake = FakeAgentMail()
    summary = run(config, out_dir=tmp_path / "out", secrets=secrets, fetchers=fetchers,
                  client=AgentMailClient(secrets, request=fake), now=NOW, send=True,
                  link_checker=fake_link_checker())
    assert all(s["company"] != "Nowhere Region Co" for s in summary["sent"])
    hooli = next(m for m in json.loads((tmp_path / "out" / "matches.json").read_text())
                 if m["company"] == "Nowhere Region Co")
    assert hooli["send_status"].startswith("CONDITIONAL") and "India eligibility unconfirmed" in hooli["send_status"]
