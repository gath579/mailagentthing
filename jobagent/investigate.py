"""Investigate whether specialist design-job sites offer an accessible, permitted feed.

For each site this checks, without crawling listings:
  * robots.txt (full text) and whether our candidate paths are disallowed
  * home/jobs pages: status, advertised RSS/Atom links, JSON-LD JobPosting count
  * common feed / sitemap / API paths
  * terms-of-service sentences mentioning scraping / crawling / automated access
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

from .http import USER_AGENT

SITES = {
    "role.com": {
        "base": "https://role.com",
        "pages": ["/", "/jobs", "/jobs/product-designer"],
        "feeds": ["/feed", "/rss", "/rss.xml", "/feed.xml", "/jobs.rss", "/sitemap.xml", "/api/jobs"],
        "terms": ["/terms", "/terms-of-service", "/tos", "/legal/terms"],
    },
    "uxjobs.io": {
        "base": "https://uxjobs.io",
        "pages": ["/", "/jobs"],
        "feeds": ["/feed", "/rss", "/rss.xml", "/feed.xml", "/jobs/feed", "/sitemap.xml", "/api/jobs"],
        "terms": ["/terms", "/terms-of-service", "/tos", "/legal"],
    },
    "uxdesign.com product-ux-design-jobs": {
        "base": "https://uxdesign.com",
        "pages": ["/product-ux-design-jobs"],
        "feeds": ["/product-ux-design-jobs/feed", "/feed", "/rss", "/sitemap.xml"],
        "terms": ["/terms", "/terms-of-service", "/privacy-terms"],
    },
    "UX Jobs Weekly (Substack)": {
        "base": "https://uxjobs.substack.com",
        "pages": ["/"],
        "feeds": ["/feed"],
        "terms": ["https://substack.com/tos"],
    },
    "uxness.in": {
        "base": "https://uxness.in",
        "pages": ["/", "/jobs", "/jobs/"],
        "feeds": ["/feed", "/jobs/feed", "/?feed=job_feed", "/job-feed", "/sitemap.xml",
                  "/wp-json/wp/v2/job-listings?per_page=1", "/wp-json/wp/v2/job_listing?per_page=1"],
        "terms": ["/terms", "/terms-and-conditions", "/terms-of-service", "/privacy-policy"],
    },
}

_ALT_RE = re.compile(r"<link[^>]+type=[\"']application/(rss|atom)\+xml[\"'][^>]*>", re.I)
_HREF_RE = re.compile(r"href=[\"']([^\"']+)", re.I)
_LD_RE = re.compile(r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_POLICY_RE = re.compile(r"[^.]{0,200}\b(scrap\w*|crawl\w*|spider\w*|robot\w*|automated (?:means|access|tools|systems)"
                        r"|data mining|harvest\w*|bots?)\b[^.]{0,200}\.", re.I)


def _get(url: str, limit: int = 400_000) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            body = resp.read(limit).decode("utf-8", "replace")
            return {"status": resp.status, "final_url": resp.geturl(),
                    "type": resp.headers.get("Content-Type", ""), "body": body}
    except urllib.error.HTTPError as exc:
        return {"status": exc.code, "final_url": url, "type": exc.headers.get("Content-Type", ""), "body": ""}
    except Exception as exc:  # noqa: BLE001
        return {"status": 0, "final_url": url, "type": "", "body": "", "error": repr(exc)[:200]}


def _ld_jobpostings(html: str) -> int:
    count = 0
    for block in _LD_RE.findall(html):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        items = data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []
        count += sum(1 for i in items if isinstance(i, dict) and i.get("@type") == "JobPosting")
    return count


def _describe(r: dict) -> str:
    body = r["body"]
    kind = "xml-feed" if re.search(r"<(rss|feed|urlset|sitemapindex)\b", body[:2000]) else \
        "json" if body.lstrip()[:1] in "[{" and body.strip() else "html" if "<html" in body[:2000].lower() else "other"
    extra = ""
    if kind == "xml-feed":
        extra = f" items={len(re.findall(r'<(item|entry|url)>', body))}"
        titles = re.findall(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", body)[1:4]
        if titles:
            extra += f" sample_titles={titles}"
    return f"{r['status']} {kind} {r['type'][:40]} len={len(body)}{extra}" + (f" err={r.get('error')}" if r.get("error") else "")


def investigate() -> str:
    out = []
    for name, cfg in SITES.items():
        base = cfg["base"]
        out.append(f"\n{'=' * 70}\n{name}  ({base})")
        robots = _get(urljoin(base, "/robots.txt"), 20_000)
        rp = RobotFileParser()
        rp.parse(robots["body"].splitlines())
        out.append(f"robots.txt: {robots['status']}")
        out.append("  | " + "\n  | ".join(robots["body"].strip().splitlines()[:40]) if robots["body"].strip() else "  (empty)")
        for path in cfg["pages"]:
            url = urljoin(base, path)
            r = _get(url)
            alts = [urljoin(r["final_url"], h) for tag in _ALT_RE.finditer(r["body"])
                    for h in _HREF_RE.findall(tag.group(0))]
            out.append(f"PAGE {path}: {_describe(r)} final={r['final_url']} robots_allowed={rp.can_fetch('*', url)}"
                       f" ld_jobpostings={_ld_jobpostings(r['body'])} next_data={'__NEXT_DATA__' in r['body']}"
                       f" alt_feeds={alts[:5]}")
            for alt in alts[:3]:
                out.append(f"  ALT {alt}: {_describe(_get(alt))}")
        for path in cfg["feeds"]:
            url = urljoin(base, path)
            out.append(f"FEED {path}: {_describe(_get(url))} robots_allowed={rp.can_fetch('*', url)}")
        for path in cfg["terms"]:
            url = path if path.startswith("http") else urljoin(base, path)
            r = _get(url)
            if r["status"] != 200:
                out.append(f"TERMS {path}: {r['status']}")
                continue
            text = re.sub(r"\s+", " ", _TAG_RE.sub(" ", r["body"]))
            clauses = list(dict.fromkeys(m.group(0).strip() for m in _POLICY_RE.finditer(text)))[:6]
            out.append(f"TERMS {path}: 200 len={len(text)} automated-access clauses={len(clauses)}")
            out += [f"  > {c[:400]}" for c in clauses]
            break
    report = "\n".join(out)
    print(report)
    return report


# ---- second pass: page structure of the promising sources ------------------------

DEEP = {
    "role.com": ["https://role.com/sitemap.xml", "https://role.com/jobs"],
    "uxjobs.io": ["https://jobs.uxjobs.io/robots.txt", "https://jobs.uxjobs.io/sitemap-index.xml",
                  "https://jobs.uxjobs.io/sitemap-0.xml", "https://jobs.uxjobs.io/rss.xml",
                  "https://jobs.uxjobs.io/feed.xml", "https://jobs.uxjobs.io/"],
    "uxdesign.com": ["https://uxdesign.com/product-ux-design-jobs/"],
}
_A_RE = re.compile(r"<a\b[^>]*href=[\"']([^\"'#]+)", re.I)
_IFRAME_RE = re.compile(r"<iframe\b[^>]*src=[\"']([^\"']+)", re.I)
_SCRIPT_SRC_RE = re.compile(r"<script\b[^>]*src=[\"']([^\"']+)", re.I)
_JSON_SCRIPT_RE = re.compile(r"<script\b[^>]*type=[\"']application/json[\"'][^>]*>(.{0,300})", re.I | re.S)
_LOC_RE = re.compile(r"<loc>(.*?)</loc>")


def _structure(url: str) -> list[str]:
    r = _get(url, 2_000_000)
    out = [f"-- {url}: {_describe(r)} final={r['final_url']}"]
    body = r["body"]
    if not body:
        return out
    if "<loc>" in body:
        locs = _LOC_RE.findall(body)
        out.append(f"   sitemap locs={len(locs)} sample={locs[:8]}")
        return out
    if url.endswith("robots.txt") and "<html" not in body[:500].lower():
        out.append("   robots: " + " | ".join(body.strip().splitlines()[:15]))
        return out
    host = urlparse(r["final_url"]).netloc
    links = [urljoin(r["final_url"], h) for h in _A_RE.findall(body)]
    internal = [l for l in links if urlparse(l).netloc == host]
    external = [l for l in links if urlparse(l).netloc and urlparse(l).netloc != host]
    seg = {}
    for l in internal:
        first = "/" + (urlparse(l).path.strip("/").split("/")[0] if urlparse(l).path.strip("/") else "")
        seg[first] = seg.get(first, 0) + 1
    ext_hosts = {}
    for l in external:
        ext_hosts[urlparse(l).netloc] = ext_hosts.get(urlparse(l).netloc, 0) + 1
    out.append(f"   links internal={len(internal)} by first segment={dict(sorted(seg.items(), key=lambda kv: -kv[1])[:10])}")
    out.append(f"   external link hosts={dict(sorted(ext_hosts.items(), key=lambda kv: -kv[1])[:10])}")
    job_like = [l for l in links if re.search(r"/(job|jobs|position|positions|role|roles|opening)s?/[^/?]+", l)]
    out.append(f"   job-like links={len(job_like)} sample={list(dict.fromkeys(job_like))[:6]}")
    out.append(f"   iframes={_IFRAME_RE.findall(body)[:5]}")
    out.append(f"   script hosts={sorted({urlparse(urljoin(url, s)).netloc for s in _SCRIPT_SRC_RE.findall(body)})[:12]}")
    blobs = _JSON_SCRIPT_RE.findall(body)
    out.append(f"   json script blocks={len(blobs)} first={[b[:150] for b in blobs[:2]]}")
    text = re.sub(r"\s+", " ", _TAG_RE.sub(" ", body))
    out.append(f"   text sample: {text[:600]}")
    if job_like:
        sample = list(dict.fromkeys(job_like))[0]
        jr = _get(sample)
        out.append(f"   SAMPLE JOB PAGE {sample}: {_describe(jr)} ld_jobpostings={_ld_jobpostings(jr['body'])}"
                   f" robots_ok=see above")
        jt = re.sub(r"\s+", " ", _TAG_RE.sub(" ", jr["body"]))
        out.append(f"   job text sample: {jt[:500]}")
    return out


def deep() -> str:
    out = []
    for name, urls in DEEP.items():
        out.append(f"\n{'#' * 70}\nDEEP {name}")
        for u in urls:
            out += _structure(u)
    report = "\n".join(out)
    print(report)
    return report


# ---- third pass: policy text + search behaviour ------------------------------------

def third() -> str:
    out = ["\n" + "%" * 70 + "\nTHIRD PASS"]
    r = _get("https://jobs.uxjobs.io/robots.txt", 20_000)
    out.append("uxjobs robots.txt FULL:\n" + r["body"])
    for q in ("https://role.com/jobs?q=product+designer", "https://role.com/jobs?search=product+designer",
              "https://role.com/jobs?keywords=product+designer", "https://role.com/search?q=product+designer"):
        rr = _get(q)
        titles = re.findall(r'href="(/jobs/[^"]+)"', rr["body"])
        design = [t for t in titles if re.search(r"design|ux|ui-", t)]
        out.append(f"role.com {q}: {rr['status']} job links={len(titles)} design-like={len(design)} sample={design[:5]}")
    rt = _get("https://role.com/terms")
    text = re.sub(r"\s+", " ", _TAG_RE.sub(" ", rt["body"]))
    for kw in ("automat", "scrap", "crawl", "robot", "bot", "harvest", "copy", "reproduc", "commercial", "personal"):
        for m in re.finditer(rf"[^.]{{0,180}}{kw}[^.]{{0,180}}\.", text, re.I):
            out.append(f"  role terms [{kw}]: {m.group(0).strip()[:360]}")
            break
    page = _get("https://uxdesign.com/product-ux-design-jobs/")
    legal = sorted(set(u for u in _A_RE.findall(page["body"]) if "/legal" in u))
    out.append(f"uxdesign legal links: {legal}")
    for u in legal[:4]:
        lr = _get(urljoin("https://uxdesign.com", u))
        lt = re.sub(r"\s+", " ", _TAG_RE.sub(" ", lr["body"]))
        clauses = list(dict.fromkeys(m.group(0).strip() for m in _POLICY_RE.finditer(lt)))[:5]
        out.append(f"  {u}: {lr['status']} clauses={len(clauses)}")
        out += [f"    > {c[:360]}" for c in clauses]
    listings = sorted(set(u for u in _A_RE.findall(page["body"]) if "/product-ux-design-job-listing/" in u))
    out.append(f"uxdesign listings ({len(listings)}): {listings[:8]}")
    if listings:
        jr = _get(urljoin("https://uxdesign.com", listings[0]))
        jt = re.sub(r"\s+", " ", _TAG_RE.sub(" ", jr["body"]))
        ext = [h for h in _A_RE.findall(jr["body"]) if "uxdesign.com" not in h and h.startswith("http")]
        out.append(f"  sample listing: {jr['status']} ld_jobpostings={_ld_jobpostings(jr['body'])} "
                   f"external links={ext[:6]}")
        i = jt.find("Product UX Design Jobs")
        out.append(f"  text: {jt[max(0, i):max(0, i) + 700]}")
    report = "\n".join(out)
    print(report)
    return report


def uxjobs_shape() -> str:
    """Print the field layout of the job list embedded in jobs.uxjobs.io's public home page."""
    r = _get("https://jobs.uxjobs.io/", 5_000_000)
    blocks = re.findall(r"<script\b[^>]*type=[\"']application/json[\"'][^>]*>(.*?)</script>", r["body"], re.S)
    out = [f"uxjobs home: {r['status']} len={len(r['body'])} json blocks={len(blocks)}"]
    for b in blocks:
        try:
            data = json.loads(b)
        except ValueError as exc:
            out.append(f"  block not JSON: {exc}")
            continue
        if isinstance(data, list):
            keys = {}
            for item in data:
                if isinstance(item, dict):
                    for k in item:
                        keys[k] = keys.get(k, 0) + 1
            out.append(f"  list len={len(data)} key counts={keys}")
            for item in data[:4]:
                out.append("  sample: " + json.dumps(item)[:700])
            design = [i for i in data if isinstance(i, dict) and re.search(r"product designer|ux designer|ui ux|ux/ui|interaction",
                                                                             str(i.get('t', '')), re.I)]
            out.append(f"  product/ux designer titles={len(design)}")
            for item in design[:6]:
                out.append("  design sample: " + json.dumps(item)[:400])
        else:
            out.append(f"  {type(data).__name__}: {json.dumps(data)[:300]}")
    report = "\n".join(out)
    print(report)
    return report
