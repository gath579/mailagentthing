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


ORACLE = "https://efpv.fa.us6.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/8136"


def test_client_rendered_ats_page_counts_as_live(monkeypatch):
    monkeypatch.setattr(linkcheck, "_fetch", lambda u: (200, u, "<html><div id=root></div></html>"))
    status, detail = linkcheck.check(_job(source="uxjobs", source_id="ux-designer-ii-infoblox", url=ORACLE))
    assert status == "live" and "client-rendered" in detail


def test_client_rendered_ats_still_honours_closed_markers(monkeypatch):
    page = "<html><div id=root></div><p>This job is no longer available</p></html>"
    monkeypatch.setattr(linkcheck, "_fetch", lambda u: (200, u, page))
    assert linkcheck.check(_job(source="uxjobs", source_id="x", url=ORACLE))[0] == "closed"


def test_oracle_site_rewrite_keeps_job_identity(monkeypatch):
    url = "https://emit.fa.ca3.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX/job/97579"
    final = "https://emit.fa.ca3.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_2001/job/97579"
    monkeypatch.setattr(linkcheck, "_fetch", lambda u: (200, final, "<html><div id=root></div></html>"))
    assert linkcheck.check(_job(source="uxjobs", source_id="ui-ux-designer-wsp", url=url))[0] == "live"


def test_unknown_host_without_title_stays_unverified(monkeypatch):
    monkeypatch.setattr(linkcheck, "_fetch", lambda u: (200, u, "<html><div id=root></div></html>"))
    assert linkcheck.check(_job(url="https://careers.example.com/jobs/8136"))[0] == "unverified"
