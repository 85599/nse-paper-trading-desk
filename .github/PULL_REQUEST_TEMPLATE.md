## What changes

## Why it matters for a trading decision

## Checks

- [ ] `python -m compileall -q scripts`
- [ ] `python scripts/desk.py doctor`
- [ ] `python scripts/desk.py team-review <symbol>` on a symbol you expected to be refused, and the `CONCLUSION` line still reads as a real explanation
- [ ] no `workspace/`, `reports/`, ledger, cached filing or broker artifact is in the diff

## Guardrails

- [ ] nothing in this diff can submit an order (`execution_enabled` stays `false`)
- [ ] a ticket still requires the user's own reply beginning with `yes`
- [ ] risk limits remain tighten-only from the dashboard
