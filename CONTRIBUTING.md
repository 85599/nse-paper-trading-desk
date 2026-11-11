# Contributing

Paper only, always. A pull request that adds a broker order path, widens a risk
limit from the UI, or books a ticket without the user's own `yes` reply will be
closed, not reviewed — those are the guardrails, not the implementation details.

## Before you open a PR

```sh
python -m compileall -q scripts          # the desk layer is stdlib-only
python scripts/desk.py --help            # CLI wiring intact
python scripts/desk.py doctor            # config, tick registry, candle feed, ledger
cd vendor/day-trader-skill-codex && python -m unittest discover -s tests
```

The last line needs Linux or macOS — `india/job.py` imports `fcntl`, so the engine
suite does not collect on Windows. CI runs the first two commands on Python 3.10,
3.12 and 3.14, plus the engine suite and a real dashboard build.

## What belongs where

* `scripts/` — the desk. Windows-safe orchestration, the keyless NSE adapters, the
  sealed-room reviewers, the journal and the dashboard server. Keep it standard
  library only; a new dependency needs a reason in the PR description.
* `vendor/day-trader-skill-codex/` — the engine. Send rule changes upstream instead
  of patching them here.
* Never commit `workspace/`, `reports/`, `config/instruments/`, a ledger, a cached
  filing, or anything from a broker. The exchange's terms do not allow
  redistributing captured provider payloads, so upstream's test fixtures stay out
  of this tree.

## Reporting a wrong refusal

The most useful issue you can file is a room that refused for the wrong reason, or
accepted for a thin one. Include the room id (`workspace/team/<room>/`), the command you
ran, and the `CONCLUSION` line. Screenshots of the dashboard are welcome; redact
your own cash figures if you care about that.
