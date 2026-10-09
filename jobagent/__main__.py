"""CLI.

python -m jobagent                          dry run: score + write email previews, send nothing
python -m jobagent --send                   send one email per new matching job
python -m jobagent --send --max-emails 1    send exactly one (first real test)
python -m jobagent probe                    discover which candidate employer boards exist
python -m jobagent investigate              check specialist sites for permitted feeds
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import load_config
from .pipeline import run
from .probe import run_probe


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="jobagent", description="Product design job scout → AgentMail")
    ap.add_argument("command", nargs="?", default="run", choices=["run", "probe", "investigate", "inboxes", "diagnose"])
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("--candidates", default="discovery/candidates.toml")
    ap.add_argument("--send", action="store_true",
                    help="actually send via AgentMail and record state (default: dry run)")
    ap.add_argument("--max-emails", type=int, default=None, help="override email.max_emails_per_run")
    ap.add_argument("--require-sent", action="store_true",
                    help="exit non-zero unless at least one job was emailed")
    ap.add_argument("--scheduled", action="store_true",
                    help="scheduled run: throttle feeds to their min_interval_hours")
    ap.add_argument("--only-url", action="append", default=[],
                    help="send only the job with this posting URL (repeatable); fails if it isn't sendable")
    ap.add_argument("--show-url", action="append", default=[],
                    help="print full analysis and email text for this posting URL (repeatable)")
    ap.add_argument("--out", default="out", help="directory for previews and run summary")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.command == "diagnose":
        from .diagnose import diagnose
        return diagnose()
    if args.command == "inboxes":
        import os
        from .agentmail import list_inboxes
        key = os.environ.get("AGENTMAIL_API_KEY")
        if not key:
            print("AGENTMAIL_API_KEY is not set")
            return 1
        inboxes = list_inboxes(key, os.environ.get("AGENTMAIL_BASE_URL", "https://api.agentmail.to"))
        print(f"{len(inboxes)} inbox(es) on this AgentMail account:")
        for ib in inboxes:
            print(f"  inbox_id={ib.get('inbox_id')}  email={ib.get('email', '')}  name={ib.get('display_name', '')}")
        return 0
    if args.command == "investigate":
        from .investigate import deep, investigate, third, uxjobs_shape
        investigate()
        deep()
        third()
        uxjobs_shape()
        return 0
    config = load_config(args.config)
    if args.command == "probe":
        found = run_probe(Path(args.candidates), config.feeds, Path(args.out))
        return 0 if found else 1

    summary = run(config, send=args.send, out_dir=Path(args.out), require_sent=args.require_sent,
                  max_emails=args.max_emails, throttle_feeds=args.scheduled,
                  only_urls=args.only_url, show_urls=args.show_url)
    print(json.dumps({k: v for k, v in summary.items() if k != "normalize_errors"}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
