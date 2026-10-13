"""Discovery probe: find which candidate employer boards and feeds really exist.

Tries every candidate slug against every company-board ATS, plus each feed,
and reports total postings, design-title postings and India-located design
postings. Output is used to build the production company list.
"""
from __future__ import annotations

import json
import logging
import re
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .config import Feed
from .http import HttpError
from .normalize import normalize
from .sources import ALL_FETCHERS, BOARD_FETCHERS, fetch

log = logging.getLogger(__name__)
DESIGN_TITLE = re.compile(r"design|\bux\b|\bui\b", re.I)


def _summarize(source: str, key: str, fetchers) -> dict:
    try:
        raws = fetch(source, key, fetchers)
    except HttpError as exc:
        return {"source": source, "key": key, "ok": False, "status": exc.status}
    except Exception as exc:  # noqa: BLE001 - probe must survive odd payloads
        return {"source": source, "key": key, "ok": False, "error": repr(exc)[:200]}
    design, india, errors, sample_titles = 0, 0, 0, []
    for raw in raws:
        try:
            job = normalize(raw)
        except Exception:  # noqa: BLE001
            errors += 1
            continue
        if DESIGN_TITLE.search(job.title):
            design += 1
            if "india" in job.location.lower() or job.country.lower() in ("in", "india"):
                india += 1
            if len(sample_titles) < 5:
                sample_titles.append(f"{job.title} | {job.location} | {job.work_mode}")
    out = {"source": source, "key": key, "ok": True, "total": len(raws), "design": design,
           "design_india": india, "normalize_errors": errors, "sample_design": sample_titles}
    if raws:
        out["payload_keys"] = sorted(raws[0].payload.keys())[:40]
    return out


def run_probe(candidates_path: Path, feeds: list[Feed], out_dir: Path, fetchers=None) -> list[dict]:
    slugs = tomllib.loads(candidates_path.read_text())["slugs"]
    tasks = [(s, slug) for slug in dict.fromkeys(slugs) for s in BOARD_FETCHERS]
    tasks += [(f.source, f.query) for f in feeds]
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(lambda t: _summarize(t[0], t[1], fetchers or ALL_FETCHERS), tasks))
    # Some ATS APIs answer 200 with an empty list for any name, so a board only
    # counts as live when it actually lists postings.
    found = [r for r in results if r["ok"] and r["total"] > 0]
    found.sort(key=lambda r: (-r["design_india"], -r["design"], -r["total"]))
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "probe.json").write_text(json.dumps(found, indent=2))
    lines = [f"{'source':16} {'key':24} {'total':>6} {'design':>6} {'IN':>4}  sample"]
    for r in found:
        if r["source"] in BOARD_FETCHERS and r["design"] == 0:
            continue  # live board, but no design roles right now: keep in probe.json only
        lines.append(f"{r['source']:16} {r['key']:24} {r['total']:>6} {r['design']:>6} {r['design_india']:>4}  "
                     + (r['sample_design'][0] if r['sample_design'] else ""))
    lines.append(f"\n{len(found)} live boards/feeds out of {len(tasks)} probes "
                 f"({sum(r['design'] > 0 for r in found)} with design roles)")
    lines.append("LIVE WITHOUT DESIGN ROLES: " + ", ".join(
        f"{r['source']}/{r['key']}" for r in found if r["design"] == 0))
    report = "\n".join(lines)
    (out_dir / "probe.txt").write_text(report)
    print(report)
    return found
