import json

import pytest

from jobagent.agentmail import AgentMailClient, DeliveryError
from jobagent.pipeline import PipelineError, run
from jobagent.state import SentStore

from .conftest import NOW, FakeAgentMail, fake_fetchers


def _client(secrets, fake):
    return AgentMailClient(secrets, request=fake)


def test_dry_run_sends_nothing_and_records_nothing(config, tmp_path, secrets):
    fake = FakeAgentMail()
    summary = run(config, send=False, out_dir=tmp_path / "out", fetchers=fake_fetchers(),
                  client=_client(secrets, fake), now=NOW)
    assert summary["matched"] == 3 and summary["sent"] is None
    assert fake.calls == []
    assert not config.state_path.exists()
    assert (tmp_path / "out" / "email.html").exists()


def test_send_verifies_and_records(config, tmp_path, secrets):
    fake = FakeAgentMail()
    summary = run(config, send=True, out_dir=tmp_path / "out", secrets=secrets,
                  fetchers=fake_fetchers(), client=_client(secrets, fake), now=NOW)

    send_call, get_call = fake.calls
    assert send_call["url"] == "https://api.agentmail.to/v0/inboxes/alerts%40agentmail.to/messages/send"
    assert send_call["headers"]["Authorization"] == "Bearer test-key"
    assert send_call["headers"]["Idempotency-Key"].startswith("jobalert-")
    assert send_call["body"]["to"] == ["me@example.com"]
    assert get_call["method"] == "GET" and "%3Cmsg-1%40agentmail.to%3E" in get_call["url"]

    assert summary["sent"]["message_id"] == "<msg-1@agentmail.to>"
    assert "sent" in summary["sent"]["labels"]
    state = json.loads(config.state_path.read_text())
    assert len(state["sent"]) == 3
    assert {v["message_id"] for v in state["sent"].values()} == {"<msg-1@agentmail.to>"}


def test_second_run_does_not_resend(config, tmp_path, secrets):
    fake = FakeAgentMail()
    kw = dict(out_dir=tmp_path / "out", secrets=secrets, fetchers=fake_fetchers(),
              client=_client(secrets, fake), now=NOW)
    run(config, send=True, **kw)
    summary = run(config, send=True, **kw)
    assert summary["new_unsent"] == 0 and summary["sent"] is None
    assert len(fake.calls) == 2  # only the first run's send + verify
    with pytest.raises(PipelineError):
        run(config, send=True, require_sent=True, **kw)


def test_failed_send_records_nothing(config, tmp_path, secrets):
    fake = FakeAgentMail(send_status=400)
    with pytest.raises(DeliveryError):
        run(config, send=True, out_dir=tmp_path / "out", secrets=secrets,
            fetchers=fake_fetchers(), client=_client(secrets, fake), now=NOW)
    assert not config.state_path.exists()


def test_verification_mismatch_records_nothing(config, tmp_path, secrets):
    fake = FakeAgentMail(tamper_subject=True)
    with pytest.raises(DeliveryError, match="subject mismatch"):
        run(config, send=True, out_dir=tmp_path / "out", secrets=secrets,
            fetchers=fake_fetchers(), client=_client(secrets, fake), now=NOW)
    assert not config.state_path.exists()


def test_batch_limit_defers_rest_to_next_run(config, tmp_path, secrets):
    config.email.max_jobs_per_email = 2
    fake = FakeAgentMail()
    kw = dict(out_dir=tmp_path / "out", secrets=secrets, fetchers=fake_fetchers(),
              client=_client(secrets, fake), now=NOW)
    first = run(config, send=True, **kw)
    second = run(config, send=True, **kw)
    assert first["sent"]["jobs"] == 2 and second["sent"]["jobs"] == 1
    assert len(SentStore(config.state_path).data["sent"]) == 3
