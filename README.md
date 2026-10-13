# mailagentthing: job alerts via AgentMail

Finds new job postings that match your profile and emails you a digest through
[AgentMail](https://agentmail.to).

```
discover → normalize → source dedup → filter/score → notification dedup
        → analysis/email → AgentMail send → verify → record as sent
```

| Stage | Module | Notes |
|---|---|---|
| Discovery | `jobagent/discovery.py`, `jobagent/sources.py` | Official public job-board APIs only: Greenhouse, Lever, Ashby. No HTML scraping. One failing board is skipped; if *all* fail the run errors out instead of reporting "no jobs". |
| Normalization | `jobagent/normalize.py` | Maps each source to a common `Job` (title, company, location, remote, description, posted date, compensation). |
| Source dedup | `jobagent/dedupe.py` | Merges duplicates by source ID, URL or company+title+location fingerprint, and keeps the richest record. |
| Filter/score | `jobagent/scoring.py` | Title include/exclude, location/remote, max age, skill hits, recency. Each match carries human-readable reasons. |
| Notification dedup | `jobagent/state.py` | `state/sent.json`, committed back to the repo by the workflow. A job is recorded only after a verified send. |
| Email | `jobagent/emailgen.py` | One digest per run (HTML + text). Summaries are excerpts of the posting itself. |
| Delivery | `jobagent/agentmail.py` | `POST /v0/inboxes/{inbox}/messages/send` with an `Idempotency-Key`, then `GET` the message back to verify id, thread, subject and recipient. |

## Configure

Edit `config.toml`: set your title phrases, skills, locations and the companies to watch.
To add a company, find its board slug in its careers URL
(`boards.greenhouse.io/<slug>`, `jobs.lever.co/<slug>`, `jobs.ashbyhq.com/<slug>`).

Secrets (environment variables / GitHub Actions secrets):

| Name | Value |
|---|---|
| `AGENTMAIL_API_KEY` | AgentMail API key |
| `AGENTMAIL_INBOX_ID` | Sending inbox, e.g. `jobs@agentmail.to` |
| `ALERT_RECIPIENT` | Where alerts go |

## Run

```bash
python -m pytest -q                       # offline tests (synthetic fixtures)
python -m jobagent                        # dry run: writes out/email.html, sends nothing
python -m jobagent --send --require-sent  # real send; fails unless ≥1 job was emailed
```

In GitHub Actions: **Actions → Job alerts → Run workflow**, then pick `dry-run` or `send`.
`out/` (email preview, `run_summary.json`, `matches.json`) is uploaded as a run artifact.
