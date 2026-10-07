"""CLI: python -m jobagent [--send] [--config config.toml]"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import load_config
from .pipeline import run


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="jobagent", description="Job discovery → AgentMail alerts")
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("--send", action="store_true",
                    help="actually send via AgentMail and record state (default: dry run)")
    ap.add_argument("--require-sent", action="store_true",
                    help="exit non-zero unless at least one job was emailed (for the manual E2E check)")
    ap.add_argument("--out", default="out", help="directory for email preview and run summary")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = load_config(args.config)
    summary = run(config, send=args.send, out_dir=Path(args.out), require_sent=args.require_sent)
    print(json.dumps(summary, indent=2))
    if args.require_sent and not summary.get("sent"):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
