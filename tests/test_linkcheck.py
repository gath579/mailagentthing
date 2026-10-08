import urllib.error

import pytest

from jobagent import linkcheck
from jobagent.models import Job


def _job(**kw):
    base = dict(source="greenhouse", source_id="12345", company="Acme", title="Product Designer",
                url="https://boards.example/acme/jobs/12345")
    base.update(kw)
    return Job(**base)


def _page(title="Product Designer", extra=""):
    return f"<html><head><title>{title} at Acme</title></head><body><h1>{title}</h1>{extra}<a>Apply</a></body></html>"


@pytest.mark.parametrize("fetch,expected", [
    (lambda u: (200, u, _page()), "live"),
    (lambda u: (200, u, _page(extra="<p>This job is no longer accepting applications.</p>")), "closed"),
    (lambda u: (200, "https://boards.example/acme?error=true", _page("Careers")), "closed"),
    (lambda u: (200, "https://boards.example/acme/careers", _page("Join us")), "redirected"),
    (lambda u: (200, u, "<html><div id=root></div></html>"), "unverified"),
])
def test_check_outcomes(monkeypatch, fetch, expected):
    monkeypatch.setattr(linkcheck, "_fetch", fetch)
    status, detail = linkcheck.check(_job())
    assert status == expected, detail


@pytest.mark.parametrize("code,expected", [(404, "closed"), (410, "closed"), (403, "unverified")])
def test_http_errors(monkeypatch, code, expected):
    def fail(url):
        raise urllib.error.HTTPError(url, code, "x", {}, None)
    monkeypatch.setattr(linkcheck, "_fetch", fail)
    assert linkcheck.check(_job())[0] == expected


def test_network_failure_is_unverified(monkeypatch):
    def boom(url):
        raise TimeoutError()
    monkeypatch.setattr(linkcheck, "_fetch", boom)
    assert linkcheck.check(_job())[0] == "unverified"
