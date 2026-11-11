# Security

This desk holds no secrets and controls no money.

* **Nothing to steal.** No broker credentials, no API keys, no tokens. Data comes
  from NSE's public endpoints; the optional Upstox analytics token is read from
  your own environment and is never written to the repo, the ledger or a report.
* **Nothing to execute.** `execution_enabled` is `false` in every code path and no
  order client exists in this tree. The worst outcome of a bug here is a wrong
  paper ticket, never a live one.
* **Nothing to overwrite.** The ledger is append-only, evidence rooms are sealed
  with per-artifact hashes, and the dashboard binds to loopback only.
* **Limits you cannot widen from the UI.** The dashboard's risk controls only
  tighten a limit; widening the budget, the daily loss cap or the entry window
  means editing `config/desk.json` yourself, and no desk command does that for you.

## Reporting a vulnerability

Open a private security advisory on the repository instead of a public issue. Do
not include broker credentials, real account figures or an order id in any report.

## Data-terms note

NSE's terms restrict automated collection and republishing. Do not commit captured
candles, filings, option-chain payloads, the downloaded instrument master or
generated reports; `.gitignore` covers those paths and CI keeps them out.
