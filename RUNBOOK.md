# RUNBOOK -- Indian day-trading desk (paper only)

Read this once, then use it as a checklist. Every number here is ₹ and every
clock is IST. Times assume the NSE cash session, 09:15-15:30.

The desk is a simulation. It sizes, checks and prints a ticket; it never places
an order, and it will not record a decision unless *you* type the words.

---

## 08:45-09:15 -- before the open

```sh
python scripts/desk.py doctor
python scripts/desk.py premarket
```

`doctor` is the health check. Everything must be green before you trust a scan:

```text
== tick registry ==
98 symbols with dated INR ticks
registry is fresh
orb list    25 symbols, 0 without ticks
daily list  98 symbols, 0 without ticks

== market clock ==
2026-11-03 | 2026-11-03 10:47 IST | trading day yes | session 09:15-15:30 | entries 09:30-14:45 IST | flatten 15:05

== candle feed ==
NSE candles ok: 1 scanned, 0 failed | TCS opening range 3/3 bars, complete True

== paper ledger ==
cash INR 100,000.00 | initial capital INR 100,000.00 | open positions 0 | realized INR 0.00
```

If the registry is stale, rebuild it (see "Weekly maintenance").

`premarket` runs the daily-setup scan over all 98 names. Real run, 2026-11-03:

```text
== daily scan | 98 symbols ==
scanned 98 of 98 | failures 0
... 48 matched ...
A daily match is a lead, not an eligible entry. Candle dates: 2026-11-02
```

48 leads is not 48 trades. Pick three names and write them down with the reason
you like them (breakout plus volume expansion, not just the label). Daily setups
need 20 completed sessions behind them, so they are context for the day, not
entries.

Optional news pass:

```sh
python scripts/desk.py news --hours 12
```

## 09:15-09:30 -- the opening range forms

Do nothing. The engine only trusts *completed* bars, and the 15-minute range is
not complete until 09:30. Trading inside a forming range is how you buy the
exact top of a spike.

## 09:30 onward -- the breakout scan

```sh
python scripts/desk.py index      # benchmark context first: NIFTY/BANKNIFTY ranges, futures premium
python scripts/desk.py scan orb
```

`index` answers "should I be buying breaks at all today". It reads the same NSE
chart host, so it costs one extra command and no new credentials. Real run at
12:35 IST:

```text
NIFTY 50              23,671.40  chg +0.98%  day 23,482.80-23,705.20  orb 23,482.80-23,628.30  above
NIFTY BANK            55,528.85  chg +0.96%  day 55,174.10-55,599.50  orb 55,174.10-55,486.95  above
NIFTY FIN SERVICE     25,290.15  chg +1.57%  day 25,048.15-25,314.45  orb 25,048.15-25,203.35  above
NIFTY IT              32,343.95  chg -0.30%  day 32,306.05-32,879.35  orb 32,529.30-32,857.85  below

futures  NIFTY26NOVFUT         23,717.30 | premium 45.9 pts (0.194%) as of 2026-11-03T12:30:00+05:30
breadth  98 scanned | 66.3% above MA20 | 27 above 20d high | median RSI 50.2 | 48 setup matches
```

Three things to read out of it:

* `state` -- `above` on three indices with `below` on IT is a normal-trend tape;
  equity long breaks have a tailwind. If NIFTY and BANKNIFTY are both `inside`
  their range, breakouts are unreliable and the honest play is to sit out.
* `premium` -- futures over spot is carry; a *negative* premium during a rally is
  a warning that the move is not being carried by position builders.
* `breadth` -- only appears when a daily scan is saved in the workspace. Under
  ~40% above MA20, individual breakouts fail far more often.

It writes `workspace/index.json` and never proposes a trade. Premiums, IV and
open interest are **not on this host** -- the chart endpoint has no option
candles. They come from `desk.py options`, further down.

The ORB table shows every range and how far each name is from breaking out, closest
first. Real run at 10:49 IST:

```text
== orb scan | 25 symbols ==
scanned 25 of 25 | failures 0
triggered 9 | ranges set 25 | still forming 0
symbol             or-high    or-low     last  width% to-break state      note
M&M.NS              2888.5    2861.0   2901.0    0.95 breakout triggered  the 10:15 bar closed above the range high
AXISBANK.NS         1227.9    1212.4   1232.4    1.26 breakout triggered  the 10:20 bar closed above the range high
...
ICICIBANK.NS        1410.0    1395.5   1409.8    1.03   +0.01% waiting    last close ₹1409.80 (10:40 bar), ₹0.20 under the range hig
MARUTI.NS          11769.0   11675.0  11740.0    0.80   +0.25% waiting    last close ₹11740.00 (10:40 bar), ₹29.00 under the range h
```

Two different things:

* `triggered` -- the range is already broken. Useful only if the break happened
  within the last bar step (see the five-minute rule below).
* `waiting` with a small `to-break` -- these are the ones to watch.

## 09:35 -- what the option chain is positioning for

```sh
python scripts/desk.py options                 # NIFTY, nearest listed expiry
python scripts/desk.py options BANKNIFTY --strikes 11
python scripts/desk.py options RELIANCE --expiry 23-Nov-2026
```

Real capture, 2026-11-03 13:47 IST:

```text
NIFTY 03-Nov-2026 | underlying   23,730.65 | NSE timestamp 03-Nov-2026 13:47:28
  strikes 114 (priced 114) | PCR by OI 1.93 | by today's OI change 2.35
  OI resistance     23,800 | OI support     23,700 | today's buildup  1,388,939 call /  3,258,931 put

      strike |   CE ltp   CE iv      CE oi     CE dOI |   PE ltp   PE iv      PE oi     PE dOI
      23,550 |   178.70     0.0     50,839    -26,883 |     4.15    10.1    297,006    294,744
      23,600 |   132.50     0.0    120,383    -39,747 |     7.55     9.3    622,042    617,993
      23,650 |    89.30     6.6    144,153     77,229 |    14.70     8.8    501,942    500,872
      23,700 |    53.45     7.1    325,827    210,006 |    28.95     8.5    635,929    634,700
      23,750 |    28.05     7.3    356,182    306,409 |    53.35     8.6    237,717    237,424  <= ATM
      23,800 |    12.70     7.4    442,486    334,208 |    88.60     9.1    148,734    147,938
      23,850 |     5.30     7.7    327,058    299,645 |   130.65    10.0     30,873     30,735
```

Three reads, none of which is a signal on its own:

* `OI resistance` / `OI support` are the heaviest call and put strike walls. With
  NIFTY at 23,730 between a 23,700 put wall and a 23,800 call wall, the day is
  bracketed -- a break of either wall is where trend followers get paid, and the
  middle is where premium sellers get paid.
* `PCR by OI` (1.93 here) is put OI over call OI. Very high PCR is not a
  directional buy signal; it says hedging or premium selling dominates the put
  side. Use the *level of the walls*, not the ratio, to decide whether an equity
  breakout has room.
* `today's buildup` is the change in OI, which matters more intraday than the
  stock. 3.26M put contracts added against 1.39M calls means the pin is being
  defended from below. If one side is *unwinding*, the desk prints
  `n/a (one side unwound)` instead of a ratio, because the sign of such a ratio
  is meaningless.

`0.0` IV means NSE published no IV for that strike; the desk keeps the zero
rather than back-solving one. NSE's own IST timestamp is shown, not your clock.

Why this works and an earlier probe of the same endpoint did not: the chain API
needs a **cookie handshake**. Visit `/option-chain` first, keep the `nsit` and
`bm_sz` cookies, and call the API from that same session with a browser
`Referer`. Without it NSE answers 403/404 -- which looks identical to "the data
does not exist". The desk retries with a fresh handshake on 401/403/429, and if
it still fails it says so instead of showing an empty table.

The boundary has not moved: this is context. The lot and tick *are* on file --
from the cached instrument directory, and `detail` goes further and prints the
premium and notional for one lot -- but `allow_options` is false and the engine
has no option exit or Greeks model, so nothing here can become an order.

## The five-minute rule

This is the rule people trip on, so it gets its own section.

The risk stage only passes a signal whose confirming bar closed within one bar
step ago: five minutes for `5m` bars, one minute for `1m`. The engine says so
with:

```text
reason: Entry signal is forming, expired or belongs to another session
```

The NSE public feed was about three minutes behind the wall clock when measured
today, so the practical window after a breakout bar closes is roughly **two
minutes**.
Consequences:

* Do not scan, then read, then type a long command. Use `watch`, which runs the
  whole chain the second it sees the trigger. On the dashboard that is
  **Watch the next bar and run the chain** on the row -- it exists precisely so
  you never have to beat the clock by hand. Start it on a row that is still
  `waiting`: on a symbol that has already broken, the watch returns on its very
  first check and inherits the same old bar, so the desk shows you
  **The break already happened, and the window closed with it** with no button
  at all rather than pretending a re-press renews anything.
* A symbol that broke out at 10:45 and is still listed as `triggered` at 10:57 is
  history, not a trade. The desk will show you the blocked reasons; that is the
  correct answer, not a bug to argue with.
* The risk stage re-prices against the *live* quote, not the trigger price. Chase
  a ₹1410.30 breakout at ₹1415.00 and the same bracket becomes risk ₹12.30 a share
  with 0.85 reward:risk instead of 7.60 a share with 2.00 -- which is exactly how
  a late entry turns into a bad trade. Real numbers from 2026-11-03:

```text
== exit plan ==
entry 1410.30 | stop 1402.70 (opening-range midpoint) | target 1425.50 (2R | 2.00R)
risk per share 7.60 | reward/risk 2.00 | flatten by 15:05 | tick 0.1

== risk check ==
  [BLOCK] ICICIBANK.NS buy 42 shares | stop 1402.7 | max loss INR 638.60 | net R:R 0.50 | tick VERIFIED
      reason: reward:risk: 0.85 (target 1425.50, entry 1415.00, stop 1402.70) is under your minimum 1
      reason: Entry signal is forming, expired or belongs to another session
```

## Catching a live entry

```sh
python scripts/desk.py watch ICICIBANK.NS --every 15 --max-checks 60 --stop or-mid --target 2R
```

**From the dashboard instead** -- expand the row on Opportunities and press
**Watch the next bar and run the chain on <SYMBOL>**. It polls every 20 seconds for
five minutes and, the moment a completed bar closes above the range high, runs plan
→ sizing → risk → ticket in that same pass. Each check prints one readable line:
the bar time, the status, and how far the close sat under the level. Captured
2026-11-06:

```text
== watching LT.NS for a completed-bar orb entry | every 20s, stops after 15 checks or the first trigger ==
tick INR 0.10 | a trigger writes workspace\entry.json and runs the chain in this same pass
  10:31:20 waiting   last close ₹4040.10 (10:25 bar), ₹8.90 under the range high ₹4049.00 (-0.22%)
  10:32:23 waiting   last close ₹4043.40 (10:25 bar), ₹5.60 under the range high ₹4049.00 (-0.14%)
  10:35:07 waiting   last close ₹4038.00 (10:30 bar), ₹11.00 under the range high ₹4049.00 (-0.27%)
  10:36:09 waiting   last close ₹4040.40 (10:30 bar), ₹8.60 under the range high ₹4049.00 (-0.21%)

No trigger inside the poll window; entry.json still holds waiting.
```

Five minutes without a break is a normal, correct answer: the desk booked nothing
and said so, rather than stretching an old bar into a signal.

### The two refusal panels, and why only one of them has a button

A chain that comes back blocked tells you which half of the clock you missed, and
the desk output panel reacts differently to each.

**No completed bar has broken the range yet** -- the row is still `waiting`, so
there is genuinely nothing to size:

```text
== entry check | LT.NS | tick INR 0.10 ==
status waiting | eligible False | price None at None
opening range 3996.1 - 4049.0 (15 min, 2026-11-06)
reason: last close ₹4031.60 (11:05 bar), ₹17.40 under the range high ₹4049.00 (-0.43%)

No triggered entry, so nothing was planned, sized, risk-checked or ticketed.
```

That panel puts **Watch the next bar and run the chain** underneath it, because
watching is exactly the right move: the break has not happened yet, so the next
completed bar is still catchable. Pressed there at 11:12, the pass above ran
290.3 s and booked nothing.

**The break already happened, and the window closed with it** -- the levels are
real and the timing is not. JSWSTEEL.NS broke on its 10:15 bar; re-checked at
11:10 the chain still produced a full bracket, and still refused:

```text
== risk check ==
2026-11-06 2026-11-06 11:10 IST | pass 0 | block 1 | cash after INR 100,000.00
  [BLOCK] JSWSTEEL.NS buy 36 shares | stop 1336.6 | max loss INR 492.02 | net R:R 1.39 | tick VERIFIED
      reason: Entry signal is forming, expired or belongs to another session

== paper ticket ==
BLOCKED | BUY JSWSTEEL.NS 36 shares | max loss INR 492.02
      reason: This exact order has no risk PASS
      reason: Entry signal is forming, expired or belongs to another session
```

This panel carries **no button**. Re-running `setup` re-reads the same first-fill
bar, and a `watch` on a symbol that has already broken returns on its very first
check and inherits that same bar -- so offering either control would be a lie.
The desk will not dress an old bar up as a fresh signal; move to a row that is
still `waiting`.

`watch` polls in the foreground (bounded, Ctrl-C stops), writes
`workspace/entry.json` on a trigger, and immediately runs plan → sizing → risk →
ticket. That is the only reliable way to land inside the window.

One watch at a time: every stage hands files through the same `workspace/`, so a
second concurrent `watch` would overwrite `entry.json` mid-chain. Watch the
closest candidate first, re-scan, then move on.

The chain prints five stages. Here is a complete healthy run, captured verbatim on
2026-11-03 while the signal was still fresh:

```text
== entry check | AXISBANK.NS | tick INR 0.10 ==
status triggered | eligible True | price 1231.0 at 10:25
opening range 1212.4 - 1227.9 (15 min, 2026-11-03)
reason: the 10:20 bar closed above the range high

== exit plan ==
entry 1231.0 | stop 1220.1 (opening-range midpoint) | target 1252.8 (2R | 1.9999999999999791R)
risk per share 10.900000000000091 | reward/risk 1.9999999999999791 | flatten by 15:05 | tick 0.1

== position sizing ==
BUY AXISBANK.NS 37 shares | stop 1220.1 target 1252.8 | Completed-bar orb; proposed local paper bracket only
status proposed | whole shares after cash buffer, order cap and concentration limits

== risk check ==
2026-11-03 2026-11-03 10:29 IST | pass 1 | block 0 | cash after INR 54,411.03
  [PASS] AXISBANK.NS buy 37 shares | stop 1220.1 | max loss INR 496.79 | net R:R 1.432633301549761 | tick VERIFIED

== paper ticket ==
READY_PAPER_TICKET | buy AXISBANK.NS 37 shares | max loss at stop (cost inclusive) INR 496.79
fingerprint 970ca7a3111990ad645ac61cf36d182b09862eade5dc3d3b43e6190a11b5a6c7
execution_enabled False

Nothing was approved or recorded. If you want this local paper position, type your own reply:
  python scripts/desk.py approve --reply "yes 970ca7a31119"
```

(The long-tail decimals above, like `1.9999999999999791R`, now print as `2.00R` --
display rounding was tightened after this capture; the numbers themselves are
unchanged. The closing line above also predates the approval box: the desk now
tells you to *type "yes <fingerprint>" into this ticket's approval box on the
dashboard*, and prints no command.)

`cash after INR 54,411.03` is what the ₹1,00,000 becomes after committing
37 × 1231 plus costs: one trade at this size already uses 45% of the account,
which is why `max_position_pct` is 75 and the daily trade count is 3.

Read `max loss ... (cost inclusive)` as your real exposure: brokerage, STT,
stamp duty, SEBI, GST, transaction charges and 5 bps of slippage are already in
that number. That is why a 37-share order at ₹500 risk per trade shows ₹496.79
and not ₹403.30 (37 × 10.90).

### Cost-inclusive risk and the risk-budget control

The sizing stage divides your risk limit by the risk *per share* and ignores
costs; the risk stage then adds costs back. On a wide stop this can produce
`Loss including costs exceeds per-trade limit`. Tighten the budget for that one
run instead of editing the config -- and do it from the refusal, not from a
terminal. When a chain output carries that reason, the desk output panel grows a
**Risk budget for this run** box: type a smaller number and press **Re-run the
paper chain with this budget**. Anything above your configured cap is refused
before the engine is even started, so the control can only shrink a position.

Both passes below are the same order on the same morning, captured 2026-11-06 --
the first at the default ₹500, the second after ₹450 was typed into that box:

```text
== risk check ==
2026-11-06 2026-11-06 09:55 IST | pass 0 | block 1 | cash after INR 100,000.00
  [BLOCK] RELIANCE.NS buy 36 shares | stop 1301.0 | max loss INR 536.22 | net R:R 1.19 | tick VERIFIED
      reason: Entry signal is forming, expired or belongs to another session
      reason: Loss including costs exceeds per-trade limit
```

```text
== risk check ==
2026-11-06 2026-11-06 10:10 IST | pass 0 | block 1 | cash after INR 100,000.00
  [BLOCK] RELIANCE.NS buy 33 shares | stop 1301.0 | max loss INR 448.59 | net R:R 1.40 | tick VERIFIED
      reason: Entry signal is forming, expired or belongs to another session
```

36 shares became 33, ₹536.22 of exposure became ₹448.59, and the costs reason is
gone -- which also lifted net reward:risk from 1.19 to 1.40, because the smaller
order pays less in absolute charges against the same per-share bracket. The
signal-timing reason survives, correctly: no budget fixes a stale bar, and on a
symbol that has already broken the watch cannot fix it either -- see
**The break already happened, and the window closed with it** above.

The equivalent command, if you would rather have it:

```sh
python scripts/desk.py setup RELIANCE.NS --stop or-mid --target 2R --risk 450
```

## Deciding -- the part only you do

```sh
python scripts/desk.py ticket                       # re-show the current ticket
python scripts/desk.py approve --reply "yes, take the 37-share AXISBANK buy at 1231"
```

The engine checks all of this before it records anything:

* the ticket must still be `ready` and its fingerprint must match the order;
* your reply must be an unambiguous yes -- `let me think`, `ok we'll see`,
  `sure` are rejected;
* if your reply names a symbol, it must be *this* symbol;
* the ticket must be under 60 seconds old;
* the same fingerprint cannot be decided twice.

Verified on this desk: `approve --reply "let me think"` and
`approve --reply "yes buy TCS.NS"` both failed closed and the ledger still read
`0 ledger events`. Approving is a local simulated event, never a broker
acknowledgement.

To record the simulated fill afterwards:

```sh
python scripts/desk.py fill --fingerprint 970ca7a3... --qty 37 --price 1231.0 --fill-id f1
```

To record that you passed: `python scripts/desk.py decline --reply "no, stop too wide"`.

## During the session

```sh
python scripts/desk.py status
python scripts/desk.py envelope
python scripts/desk.py alerts add --from-plan
python scripts/desk.py alerts check
python scripts/desk.py risk
```

* `status` -- clock, entry window, cash, alert count, today's usage against the
  3 trades / ₹1,500 daily loss / 10 orders limits.
* `envelope` -- the same limits as usage bars, plus how much room the next trade
  actually has. Real run at 13:10 IST:

```text
== risk envelope ==
as of 2026-11-03T13:10:50.279883+05:30 | paper True | no live execution
entry window 09:30-14:45 IST | flatten 15:05
  loss_at_risk     0.0% |.........................| used 0.0 / 1500.0
  trades           0.0% |.........................| used 0 / 3
  orders           0.0% |.........................| used 0 / 10
  position_cost    0.0% |.........................| used 0 / 75000.0
  cash             0.0% |.........................| used 0.0 / 100000.0
  room now: risk 500.0 | buy 75000.0 | trades left 3 | orders left 10
  last risk decision on file: BLOCK
    blocked: loss: 47 shares x 15.00 risk a share = ₹705 at the stop, over your ₹500 max loss per trade
    blocked: reward:risk: 0.52 (target 1425.50, entry 1417.70, stop 1402.70) is under your minimum 1
    blocked: Entry signal is forming, expired or belongs to another session
    blocked: Loss including costs exceeds per-trade limit
    blocked: Net reward/risk below minimum
  pending signal: ICICIBANK.NS (triggered, eligible False)
```

  Read `room now: risk` before you size anything: it is
  `min(₹500 per trade, ₹1,500 - loss already at risk - realized losses today)`,
  so once ₹1,100 of stop exposure and losses are on the board the panel shows
  ₹400 of room, not a fresh ₹500. The block list is the last decision the engine
  wrote to `state/risk.json`, copied verbatim -- `envelope` never calls the risk
  role, because a fresh decision file would overwrite the one your consent chain
  is keyed to. It is a read-only mirror, not a check. If the counters came from
  an earlier date the panel prints `NOTE counters come from the last risk run`;
  re-run `risk` or a scan for today's numbers.
* `alerts add --from-plan` persists the stop, target and the 15:05 flat-by rule
  from the plan you just made; `alerts check` fires each rule once per event.
* `risk` re-runs the check on the saved `orders.json` -- do it after every move,
  because a PASS is only valid for its moment.
* `signals` re-scores the whole saved universe on daily bars:

```sh
python scripts/desk.py signals
```

```text
  symbol                  close    ADX    DI+    DI-    RSI   ATR%     MACDh   volx    rv% verdict
  RELIANCE.NS            1307.9   27.4   35.4   16.8  66.79   2.51    16.799   2.27  29.91 BUY (7)
  INDHOTEL.NS            842.25   35.1   31.8    7.2  75.75   2.36    7.0828   0.86  23.16 BUY (7)
verdicts: BUY 15 HOLD 16 SELL 1 | 32 of 32 clear of vetoes
```

  Use it to re-rank the shortlist, not to enter: `BUY` means the daily rules agree
  with a long cash position, and `SELL` means get out or stay out (never a short).
  +DI above -DI with ADX rising is what separates a trending name from a choppy
  one at the same RSI. It also writes `workspace/signals.json`, which is what the
  Opportunities view and the search box read, so run it before you open the
  dashboard. India VIX is intentionally absent -- see Troubleshooting.

`risk`, `ticket` and `approve` need a *fresh completed price* (within 15
minutes), so they only work while the market is open. Outside the session they
fail with `A dated fresh completed price is required` -- correct, not broken.

## Look one symbol up live

Everything above reads saved files. This is the one command that fetches on
demand, and it is the engine behind the dashboard search box:

```sh
python scripts/desk.py detail RELIANCE
python scripts/desk.py detail "NIFTY 50"
python scripts/desk.py detail "NIFTY 24000 CE"
python scripts/desk.py detail "BANKNIFTY 03-Dec-2026 55800 PE" --refresh
python scripts/desk.py filings RELIANCE
python scripts/desk.py report --symbol RELIANCE
```

* `detail` resolves the name through the cached instrument directory (78,291
  rows, no API key), then asks the chart host for daily candles and today's
  5-minute tape and the chain API for the option chain. It prints the daily
  verdict with all fifteen indicators, the 15-minute opening range, the OI walls
  and PCR, the contract geometry for one lot (lot, tick, premium, notional) and
  an ATR stop sized against the cash and the ₹500 cap.
* Answers are cached for 15 minutes per symbol in `workspace/state/details/`, so
  a second search is instant; `--refresh` forces the live read. The cache is a
  read of the tape only -- it never feeds `orders.json` or the ledger.
* A feed that refuses is printed as a refusal with its own reason, and a name the
  chart host does not carry (delisted or renamed) comes back with the closest
  names on file. Nothing is interpolated and no IV, Greeks or VIX is invented.
* `filings RELIANCE` (and the same block inside `detail`) reads
  `/api/corporate-announcements` on the exchange's own site: dated disclosures
  with the filing PDF on `nsearchives.nseindia.com`. It needs the same cookie
  handshake as the chain, so a bare request 403s and looks like missing data.
  This is the desk's only primary source, and it is what lets the specialist
  `news` role answer with a citation instead of `UNAVAILABLE`. It lists filings;
  `financials` below reads the instance each one points at, so fundamental
  numbers now come out of it too.
* `report --symbol` writes `reports/symbol-<SYM>-<date>.md` and `.html` from the
  cached detail document, so the prose and the numbers cannot disagree with the
  card you just read.

README has a full captured run of both, including the honest refusal for
`TATAMOTORS`.

## Work one Nifty index at a time

The saved universe in `config/` is a hand-kept list of liquid names, which is not
the same question as "what is inside Nifty IT right now". NSE publishes one
constituent CSV per index on `archives.nseindia.com/content/indices/`, and the
desk reads exactly that file -- symbol, company, industry, series, ISIN. It
carries no price, so membership and verdicts stay separate steps:

```sh
python scripts/desk.py universe --list          # all 39, and which have a file
python scripts/desk.py universe "NIFTY IT"      # the constituents, cached
python scripts/desk.py scan daily --index "NIFTY IT"   # verdicts for those names
python scripts/desk.py signals                  # the board now describes that index
```

```text
== Nifty IT constituents | 10 stocks | 7 already on the desk scan lists | 2026-11-09T13:48:23+05:30 ==
  HCLTECH.NS       on   HCL Technologies Ltd.          Information Technology
  TCS.NS           on   Tata Consultancy Services Ltd. Information Technology
  ...
Membership only: NSE lists no prices in this file. To get verdicts for these stocks,
scan the index: desk.py scan daily --index "Nifty IT"
```

On the dashboard the same three steps are the **NIFTY INDEX** picker under the
board filters: **Find these stocks** (one second, no market read), **Scan these
stocks for verdicts** (a daily pass over exactly those symbols, minutes on a 500
name), and **Show only these on the board** to hold the filter still while you
work. The list is cached in `workspace/universes/` for a day because membership
moves at the monthly rebalance, not intraday.

Two things to keep straight. An index scan rewrites the daily snapshot with that
index's universe -- that is how the verdicts get there, and the button says so
before it runs; **＋ New request → Universe scan** puts the saved list back. And
nine screens have no published file at all (Total Market, Midcap Select, Microcap
250, Financial Services, Private Bank, Low Volatility 50, Alpha 50, Momentum 30,
Quality 30): the desk refuses them instead of copying a list from somewhere else.

## The numbers the issuer filed with the exchange

The financial analyst used to have nothing admissible to cite: the desk could list an
issuer's filings but not read them. `financials` closes that gap by downloading the
SEBI regulation-33 XBRL instance the exchange itself hosts on
`nsearchives.nseindia.com` and reading the facts straight out of it, with the
exchange's own filing stamp as the publication time. Real run, 5 Nov 2026:

```sh
python scripts/desk.py financials "M&M" --quarters 1
```

```
M&M -- Standalone filing, period ended 30-JUN-2026
  source   nsearchives.nseindia.com
  filed    30-Jul-2026 16:11:12 (Original, Un-Audited)
  link     https://nsearchives.nseindia.com/corporate/xbrl/INTEGRATED_FILING_INDAS_1705128_30072026041112_WEB.xml
  fetched  2026-11-05T12:45:02.105516+05:30 (cache)

  Quarter ended 2026-06-30 (as filed 2026-04-01 to 2026-06-30)
    Revenue from operations                Rs 41,958.76 cr (as filed 419587600000 INR)
    Total income (revenue + other income)  Rs 42,735.14 cr (as filed 427351400000 INR)
    Cost of materials consumed             Rs 30,680.15 cr (as filed 306801500000 INR)
    Employee benefit expense               Rs 1,406.24 cr (as filed 14062400000 INR)
    Depreciation and amortisation          Rs 1,050.33 cr (as filed 10503300000 INR)
    Net profit / loss for the period       Rs 3,684.97 cr (as filed 36849700000 INR)
    Comprehensive income for the period    Rs 3,672.70 cr (as filed 36727000000 INR)
    Paid-up equity share capital           Rs 601.19 cr   (as filed 6011900000 INR)
    Face value per equity share            Rs 5.00        (as filed 5 INRPerShare)
    Basic EPS, continuing operations       Rs 30.65       (as filed 30.65 INRPerShare)
    Basic EPS, continuing and discontinued Rs 30.65       (as filed 30.65 INRPerShare)
    Debt / equity, as filed                0.0128         (as filed 0.0128 pure)

  Every figure is the issuer's own regulation-33 filing, quoted with the period and scope it was filed in, and converted to crore for legibility only. The desk reads the instance the exchange publishes; it estimates nothing and carries nothing over from an earlier quarter.
```

Every figure is quoted with the period and scope it was filed in; crore conversion is
for legibility only and the as-filed value is printed next to it. `--scope
Consolidated` and `--refresh` exist, and the parsed instance is cached for six hours
per symbol and scope under `workspace/state/financials/`. The same block appears in
`detail`, in `report --symbol` and as the "numbers the issuer filed" card in the
search answer, and it is what a reviewer cites when it returns `PASS` or `WARN`
instead of `UNAVAILABLE`.

## Ten specialists, one sealed room

When you want a name argued from ten independent angles instead of one score:

```sh
python scripts/desk.py team INDHOTEL.NS                     # seal the evidence
python scripts/desk.py team-review ASTRAL.NS                # seal + run all ten roles
python scripts/desk.py team-finish <session>                # run all ten on an ALREADY sealed room
python scripts/desk.py team-dispatch <session> risk --agent-id risk-review
python scripts/desk.py team-record <session> risk --agent-id risk-review --review risk.json
python scripts/desk.py team-report <session>                # finalize + roll up
```

* `team` writes candles, the setup, entry, plan, sizing, risk, draft ticket and
  alerts into `workspace/team/<session>/` and hash-binds them to a `snapshot_id`.
  It starts no agent and clears no trade. It prints `preflight: NOT_READY` when
  the sealed window holds no completed-bar setup -- that is a finding, not an
  error; the reviewers can still audit the evidence. **A `team` seal on its own
  leaves the room at `0/10 PENDING`**, because nothing dispatched anyone; that is
  correct behaviour of the command, not a bug, and it is why the dashboard button
  does not stop at a seal.
* `team-review SYMBOL` is the whole room in one command: it seals, dispatches ten
  distinct `desk-checker-<role>` identities, runs each role's own deterministic
  checker from `scripts/reviewers.py` against **only** that role's sealed
  artifact, records the ten verdict files under `state/review-drafts/<session>/`,
  and finalizes. It cannot book a ticket -- a ticket still needs your own typed
  "yes" -- and it cannot launder a veto, because finalize re-derives budget and
  risk from the sealed artifacts and refuses any review that disagrees with them.
  It accepts `--tick`, `--days`, `--simulations` and `--as-of` exactly as `team`
  does, so a replay room can be run in one pass too.
* `team-finish <session>` is for a room that is already on disk at `0/10`: it runs
  the same ten checkers against the artifacts that were sealed then, leaves any
  role that is already recorded untouched, refuses to vote a role whose dispatch
  belongs to another reviewer, and finalizes. It seals no second room, so the
  evidence a stuck Observatory is showing stays the evidence being judged. The
  Observatory prints this as **Run the ten specialists on this room** on the stuck
  room itself; re-searching the symbol is not the way out.
* `team-dispatch` binds an identity to a role. An id cannot be reused across two
  roles or rebound later, which is what stops one reviewer from voting twice.
* `team-record` takes the reviewer's *own* `verdict`, `summary` and `evidence`
  list and stamps the engine's bindings on it -- agents never supply
  `snapshot_id` or artifact hashes themselves.
* `team-report` calls `finalize`, which re-derives budget and risk numerically and
  invalidates any review that does not match the sealed artifacts. `UNAVAILABLE`
  is a legitimate verdict and keeps the room open; `NOT_APPLICABLE` is rejected.
  The printed roll-up gives every role's reason line and ends with one
  `CONCLUSION:` sentence that says how many roles refused, how many had nothing to
  certify yet, and how many found no dated source -- so a room where one entry veto
  emptied six downstream artifacts reads as one decision, not seven failures.
* `team SYMBOL --as-of 2026-11-05T10:41:00` seals a **labelled replay room**: the
  engine keeps only the bars whose five minutes had already ended by that instant,
  so sealing at a past moment is not lookahead. Every artifact then carries
  `historical_replay: true` and the Observatory prints a banner saying the room is
  evidence about a past session, never a live seal. Use it when the real first-fill
  window has passed and you still want ten roles to argue a genuine setup.
* A `news` or `analyst` `PASS`/`WARN` is only admitted if every cited source is an
  https URL on `nseindia.com`/`bseindia.com` (or a subdomain) with exactly the keys
  `url`, `published_at`, `accessed_at`, published at or before the room cutoff. One
  inadmissible source invalidates the whole review, which is why `filings` and
  `financials` exist -- a reviewer must be able to open something real.
* Those two roles read the exchange **inside their own review**, not inside the
  sealed artifact. `_artifacts()` writes the news and analyst artifacts with empty
  `items`/`sources` at seal time, and a seal is immutable by design -- rewriting an
  artifact would change its `artifact_hashes` entry and invalidate every review in
  the room. The admissible route is the review document's own `sources` key, which
  `finalize` checks for those two roles, so the desk-side checker fetches the
  disclosure list or the XBRL instance, cites it with real timestamps, and answers
  `WARN` with the citation instead of `UNAVAILABLE` with an excuse. If the exchange
  refuses, or nothing was published before the cutoff, it still says `UNAVAILABLE`
  and names the failure -- it never recycles an older room's numbers.

Reviewers must be genuinely separate runs -- each reads only its own artifact and
writes its own conclusion. Ten copies of one model's opinion is not ten reviews.
The room for INDHOTEL.NS on 2026-11-04 came back `BLOCKED` with 10/10 reviews and
no ticket; that is what a working room looks like when there is nothing to take.

The replay room for M&M.NS sealed at 10:41 on 2026-11-05 is the one where all ten
roles had something to check: nine returned a verdict and the time machine vetoed,
so the room is `BLOCKED` with **zero** `UNAVAILABLE`. Its full roll-up, the analyst's
XBRL citation and what the veto actually found are in the README.

### Reading a room from the dashboard

You do not need the CLI to see any of this. Searching a stock in **Individual
research** now answers with a **THE SPECIALIST TEAM** block: that symbol's newest
sealed room, its status badge, one row per role with the role's own verdict,
**reason line** and agent id, and one conclusion sentence above the rows that says
what the room decided. Two buttons
run the desk's allowlisted commands -- **Seal and run the 10 specialists**
(`team-review`, which becomes **Re-seal and run the 10 specialists** once a room
exists) and **Roll up what is recorded** (`team-report`) -- plus **Open in
Observatory** for the full artifact view. A symbol nobody has reviewed says
`No sealed room yet` with zero rows and offers only the run button; the panel never
prints a tally it does not have. One click is the whole room: searching a symbol
that had never been sealed showed `rows: 0`, and after pressing the button the same
panel read `BLOCKED 10 of 10 independent reviews` with ten verdicts and
`Distinct recorded agent ids: 10 of 10`.

### Why a badge is no longer the whole answer, 9 Nov 2026

Searching a stock used to leave you with ten badges and no conclusion: six
`BLOCK`/`UNAVAILABLE` in a row, with nothing saying which of them was an actual
refusal. It was one veto -- the entry stage found no completed-bar trigger -- and
every desk downstream of it correctly reported that it had nothing to certify.

Each checker now records a `reason_kind` with its verdict, and both the CLI and the
dashboard group by it:

| kind | chip | means |
|---|---|---|
| `veto` | says no | read its own evidence and refused this bar |
| `nothing-to-check` | nothing yet | no plan, order or ticket exists, so there is no evidence to answer about |
| `evidence-gap` | no source | looked for dated exchange or issuer proof and found none before the cutoff |
| `confirmed` | checked | answered from what it opened -- the reason line says what |

Captured from the browser on `m&m-20261109-145956` (`.check/reasons.cjs` and
`.check/research_reasons.cjs`, both with zero JS errors):

```text
conclusion -> No trade at this bar. The completed-bar trigger never printed in the
              sealed window, and that one fact is what the desks below it are reporting.
groups     -> Real vetoes 1 (Entry specialist)
              Nothing to certify yet 6 (Exit, Time machine, Position sizing,
                                        Risk manager, Paper broker, Alerts)
              Answered from its own evidence 3 (Market scanner, News, Analyst)
sidebar    -> Real vetoes = 1 of 4 BLOCK badge(s)
search page-> Sealed 9 Nov 2026, 2:59 pm IST - 10 of 10 reviews recorded - room
              status BLOCKED. Real vetoes: Entry specialist; Nothing to certify yet:
              Exit specialist, Time machine, Position sizing, Risk manager, Paper
              broker, Alerts; Answered from its own evidence: Market scanner, News &
              catalysts, Financial analyst. No paper ticket was released.
```

The conclusion sentence, the grouping and the chip labels come from one module,
`dashboard/src/verdictKinds.js`, used by both the Observatory and the search page,
so the two views cannot drift apart. `needs attention` on the Observatory means a
refusal, a missing dated source or no answer -- not "anything that is not a green
PASS", which is why a `WARN` that says *I read the exchange's filing list* no longer
shows up as a problem. Rooms recorded before this change have no stored kind, so
they fall back to the badge and the conclusion says who vetoed, nothing more.

If you want to know whether a quiet room is a real refusal or an empty artifact,
read the `CONCLUSION:` line: a room with one veto and six `nothing-to-check` is the
entry stage saying no, and the only way to change it is a bar that actually
triggers -- not a re-run.

A `BLOCK` badge is red and a `PASS` badge green -- they used to share the green of
a completed step, which made a veto read like an approval.

The **Observatory** sidebar badge is the real count of sealed rooms on this
machine, and its `FIND A SYMBOL'S ROOM` box narrows the session dropdown to that
symbol's rooms, newest first. Searching a stock on the research page now also
moves the Observatory to that stock's newest room instead of leaving it pinned to
whatever was opened last; a symbol with no sealed room leaves it where it was,
because there is nothing honest to show yet. A room that is already on disk but
still at `0/10` is not a dead end in the Observatory either: the decision panel on
that room carries **Run the ten specialists on this room**, which posts
`team-finish` and leaves the sealed evidence exactly where it is. Pressing it on a
room deliberately stranded by a bare seal took it from `AWAITING FINALIZATION` with
eleven `PENDING`/`Queued` badges on the page to `BLOCKED` with all ten roles
recorded and `pending: 0`.

And a search that matches nothing is no longer a dead end either. Typing a symbol
nobody has ever reviewed used to leave the dropdown reading `No sealed room matches
that search` with nothing to press, so the only way forward was to leave the page and
retype the symbol in the ＋ New request dialog. Below that dropdown there is now a
**Review <SYMBOL> with the ten specialists** button, which posts the same allowlisted
`team review` pass: it seals fresh evidence, runs the ten desk-side checkers on it
and finalizes, then the Observatory follows the room it just made. Pressing it for
TCS, which held no room at all, produced `tcs-20261109-131235` at
`BLOCKED · 10/10 recorded · pending: 0` in about two minutes, with no JS errors on
the page. The button states its own cost (a fresh seal reads the market again) and
its own limit (no ticket is booked; that waits for your "yes").

The **＋ New request** button no longer copies a prompt for you to paste -- it runs
the desk command itself. Pick "Review one stock with the 10 specialists", type a
symbol (bare or `.NS`), and the one click seals the evidence room *and* runs all ten
roles through the same allowlisted `team-review` command the research page uses,
then prints the engine's own answer (`preflight : READY`/`NOT_READY`, or the refusal
line if the seal failed) and moves the research page to that stock. IRFC.NS took
31.1s for the whole room. The panel shows the limits this desk actually enforces,
read from `config/desk.json` via `/api/limits`, not numbers you retype. Nothing
there approves or fills anything.

The **Trading journal** lists every time you ran the paper chain, newest first and
across every symbol -- not just the last stock the engine touched. Orders that
reached the risk check show their full sized line; runs that stopped because no
entry triggered are kept as an *entry stage* row in the engine's own words ("last
close ₹792.00, ₹7.00 under the range high"), so a scan that found nothing reads as
a real answer rather than a broken panel. It is capped at the newest 200 attempts in
`state/ticket-attempts.json`, deduplicated by timestamp and order fingerprint.

Three defects the SBIN.NS reviewers on 2026-11-06 pointed at, and where each one
stands now:

* `metrics.json` seals empty (`automated_filings_adapter: false`), so the analyst
  had to re-fetch filed numbers the desk could have sealed. **Resolved on 9 Nov,
  but not by sealing them**: the artifacts are hash-bound in `context.json`, so
  writing them after the seal would invalidate every review in the room. The
  analyst instead reads the issuer's regulation-33 instance off the exchange
  archive inside its own review and cites it under `sources`, which `finalize`
  already admits for that role.
* `settings.cost_config` is `null` inside the room, which was read as making the
  risk role's cost-inclusive claim vacuous. **That reading was wrong.** `market.costs()`
  falls back to the engine's own documented NSE defaults when the config is empty
  (brokerage 0.03% capped at ₹20, STT 0.025% on the sell, stamp 0.00003, SEBI
  turnover, GST 18%, exchange transaction charges, 5 bps slippage), so the costs
  are computed from a stated rulebook rather than from a missing file.
* `allow_options` is not carried in `context.json`. **True, and not load-bearing**:
  this desk is a cash-equity model with no option exit and no Greeks model, so the
  flag cannot change any sizing or veto. The broker role confirms paper-only from
  `execution_enabled` inside the sealed artifacts; `config/desk.json` is only where
  the flag itself lives.

## 15:05 -- flat by

`flat_by` is 15:05 and the engine refuses entries after 14:45. If you are holding
a paper position:

```sh
python scripts/desk.py flatten AXISBANK.NS
```

That proposes a closing order and re-runs risk, so you see the cost-inclusive
result before recording it. The same command is a button: **Propose closing** on
each row of *Open paper positions* in the Trading journal prints the desk's real
answer under the table and reloads the ledger. A journal with no open positions
shows no rows and no button, because there is nothing to close.

## After the close

```sh
python scripts/desk.py journal
python scripts/desk.py report
python scripts/desk.py sim RELIANCE.NS --days 28
python scripts/desk.py dashboard
```

`journal` prints the ledger and the authoritative SQLite path
(`workspace/state/paper.sqlite`). Every event is append-only and dated; that file
is the record of what you actually did, as opposed to what you remember doing.

`report` writes `reports/report-<date>.md` and `.html` from the files that are
already on disk -- envelope, signal board, option tape, the newest sealed team
room, headlines and the ledger. It fetches nothing, so the report and the
dashboard cannot disagree, and the team block carries the caveat with it: agent
identities are host-recorded provenance, not a signed attestation.

`dashboard` serves through `scripts/serve.py`, which wraps the upstream handler
and adds `/api/envelope`, `/api/options`, `/api/signals`, `/api/news`,
`/api/lookup`, `/api/actions` and `POST /api/action`. Opportunities is the signal
board with a row action that runs the paper chain (and one that seals a team
room), Research is the search box, the Trading journal leads with today's ticket
queue, and the observatory views read `workspace/team/`. Every button posts to
the same allowlist the CLI uses, one command at a time, and lands in
`workspace/state/actions.jsonl` with its real exit code.

The envelope, chain and signal panels only render while `workspace/envelope.json`,
`workspace/options.json` and `workspace/signals.json` are fresher than 15 minutes
-- run those three commands right before you look. Past that the APIs answer 409
and the panels hide rather than showing stale numbers.

`sim` replays your stop/target rule over prior sessions and samples remaining
paths with costs. Real replay of the ICICIBANK bracket over 18 prior sessions:

```text
status UNAVAILABLE
reason INSUFFICIENT_HISTORY
  18 prior complete sessions available, 20 needed for sampled scenarios. ...
replay       18 sessions | 11 triggered | 11 trades | net P&L INR 780.50 (costs included, next-bar-open fills)
  2026-10-21 entry 1406.6    stop 1399.7    target 1420.4     51 shares -> 1399.7    INR -496.01
  2026-10-29 entry 1376.6    stop 1372.2    target 1386.9     54 shares -> 1386.9    INR 407.85
  note: Historical fills use next-bar open, stop-first ambiguity and estimated costs.
```

The replay is real; the Monte-Carlo part needs 20 completed sessions and NSE caps
5-minute history at about 28 days, hence `INSUFFICIENT_HISTORY`. For longer
studies pass authorized CSV data or `--provider upstox`. Either way the engine
says plainly that replays are not forecasts.

## Weekly maintenance

```sh
python scripts/registry.py build      # cross-check directory + live candles, rewrite the CSV
python scripts/registry.py verify     # coverage and age check against the desk lists (no downloads)
python scripts/desk.py doctor         # confirm "registry is fresh"
```

Tick sizes do change (splits, price-band revisions), and a wrong tick silently
changes stop rounding, so the desk refuses to call a ticket VERIFIED on stale
data. The builder cross-checks Upstox's public instrument directory (whose ticks
are in **paise**) against the observed price grid in live NSE candles and records
both, plus provenance, in `config/security-master.csv`.

## Troubleshooting

| What you see | What it means | What to do |
|---|---|---|
| `Entry signal is forming, expired or belongs to another session` | The trigger bar is older than one bar step | Start **Watch the next bar and run the chain** on a row that is still `waiting`, so the chain runs on the trigger instead of after it. On a symbol that has already broken nothing renews it -- the desk says so and offers no button |
| `Risk decision is expired or future-dated` | `state/risk.json` is from an earlier moment | Press **Run the paper chain on `<SYMBOL>`** -- one pass re-runs risk and the ticket together |
| `Configuration or paper portfolio changed; rerun risk` | The stored review was made under a different config (e.g. you used the risk-budget box once) or a different ledger | Press **Re-run the paper chain with this budget** so the review and the ticket are made under the same numbers |
| `Loss including costs exceeds per-trade limit` | Costs push you past the limit | Type a smaller number (450 is the usual first try) in **Risk budget for this run** and re-run the chain. Nothing is widened -- the box cannot go above your cap |
| `Net reward/risk below minimum` | Costs ate the reward on a wide stop | Tighter stop or further target; do not "fix" the config minimum |
| `A dated fresh completed price is required` | Market closed or feed is stale | These stages only run in-session |
| `tick verification: UNVERIFIED` | Symbol missing from the registry | `python scripts/registry.py build`, or drop the symbol |
| `MISMATCH` | Registry tick disagrees with the one supplied | Trust the registry; check for a recent price-band change |
| `INSUFFICIENT_HISTORY` in `sim` | NSE 5m history is ~28 days, 20 sessions needed | `--days 28`, or `--csv` with longer authorized data |
| `No OHLCV data available` on `--days 60/120` with 5m bars | Beyond the NSE window | Use `--days 28` for 5m, 120 only for the daily scan |
| `Saved scan is unavailable or changed. Refresh the opportunities list.` in `/api/opportunities` | The dashboard's Opportunities view only accepts a saved **daily** scan. Since 9 Nov 2026 `scan orb` writes `workspace/watchlist-orb.json` instead of overwriting the daily snapshot, so an opening-range pass can no longer age the board | Press **Run the daily scan now** in that banner -- it runs the desk's daily pass and the view reloads itself. `python scripts/desk.py scan daily` is the same call. If the banner says the file *changed*, someone ran a daily scan since the view loaded |
| `Risk envelope is 23.2 h old, so the desk will not show it as current.` in `/api/envelope` (panel missing) | The dashboard refuses to draw limits from a stale snapshot | Press **Refresh the risk envelope** on the panel; `python scripts/desk.py envelope` writes the same file |
| `No risk envelope on file yet.` | `workspace/envelope.json` has never been written | Press the button the panel offers, or run `python scripts/desk.py envelope` once |
| `NOTE counters come from the last risk run` in `envelope` | Trades/orders/loss-at-risk counters came from an earlier date | Re-run `risk` (in-session) or a scan so today's decision file exists |
| `Option chain is 23.2 h old, so the desk will not show it as current.` (panel missing) | The dashboard refuses to draw a stale chain snapshot | Press **Refresh the option chain**; `python scripts/desk.py options` writes the same file |
| `Signal board is 4.6 h old, so the desk will not show it as current.` with a **Recalculate the signal board** button | The saved `signals.json` is older than its own budget (2 h, same session) | Click the button. No terminal step exists for it and none is needed -- the same refusal covers the headline feed with **Collect dated headlines** |
| `NSE gave no session cookies` / `NSE served an HTML block page instead of JSON` | The chain API's cookie handshake failed -- bot protection, or a cloud/datacenter IP that NSE refuses | Re-run `desk.py options` (it retries with a fresh handshake). From a hosted/VPS IP it may never pass; from a normal home connection it does |
| `NSE listed no expiries for X` | That symbol has no listed options on the chain | Use a chain symbol: NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, or a liquid F&O equity like RELIANCE |
| `Expiry ... is not listed for ...; try one of ...` | Wrong expiry string | Copy one from the `expiries on file:` line the command prints |
| `preflight : NOT_READY`, `dispatch specialists = False` from `team` | The sealed window holds no completed-bar setup | Nothing is broken: seal anyway and let the ten roles audit the evidence. A room that ends `BLOCKED` with no ticket is the correct answer |
| `NSE has no 5m candles that far back (60 days) -- asking for 45` | The chart host caps 5-minute history | Normal. The desk shortens the window out loud (45/40/35/28) instead of inventing bars; expect ~21 complete prior sessions |
| Observatory shows `0/10` and every role `PENDING` on an old room | It was sealed by `team`, which writes evidence and dispatches nobody | Press **Run the ten specialists on this room** on that room, or `python scripts/desk.py team-finish <session>`. Re-searching the symbol only seals a second room |
| `engine vetoes: entry, budget, risk, broker` in `team-report` | `finalize` re-derived the numbers and disagreed with the room | Read the reasons; do not edit reviews to match. Re-seal after a real completed-bar trigger |
| `Unknown command. Allowed: ...` in a server log when a dashboard button is clicked | That server process was started before the command joined the allowlist; the allowlist is frozen when the server imports | The page no longer pastes that list: it now says `"team review" is not allowed by this server process, which started before that command existed. The page you have open is current; the desk behind it is not.` Start a fresh server on another port (`--port 8793`) and open the address it prints. Never kill the running one to free a port |
| `NSE publishes no constituent file for this index` under the board's index picker | Nine of the 39 screens (Total Market, Midcap Select, Microcap 250, Financial Services, Private Bank, Low Volatility 50, Alpha 50, Momentum 30, Quality 30) have no CSV on the exchange archive | Use the filter box for a stock you already know, or pick one of the 30 that do have a file. The desk will not copy a constituent list from a third party -- an invented universe would drive a scan and every verdict after it |
| `Nifty 500 has no constituent list on this machine yet.` | A GET never reaches NSE; fetching is the `universe` command's job, so an uncached index refuses instead of silently hitting the network | Press **Find the stocks in Nifty 500** in that banner, or `python scripts/desk.py universe "NIFTY 500"` |
| A member row reads `not on the board` | The signal board keeps the 30 best-scoring rows, so a constituent can be well known to the desk and still have no verdict | Press **Scan these stocks for verdicts** -- the daily pass runs on exactly that index and every member gets a row |
| The board only holds one index's names after an index scan | `scan daily --index` rewrites `watchlist.json` with that universe. That is how the verdicts arrive, and the button says so before it runs | Press **＋ New request → Universe scan**, or `python scripts/desk.py scan daily`, to put the saved list back |
| Risk envelope panel says nothing is shown, and its button does nothing visible | The panel reads `envelope.json`, which only `desk.py envelope` writes; `risk` writes a different file | Press **Rebuild the envelope**. If it still refuses, the reason it prints is the desk's own, e.g. `envelope is 74.5 h old` |
| `A symbol like RELIANCE, NIFTY 50 or NIFTY 24000 CE is required` from an index search's chain button | The step was posted with no symbol. NIFTY IT has no published chain at all, so it offers no button | Re-search; the chain step now carries the symbol NSE takes (`BANKNIFTY`). If a name you expect has no button, that index has no chain to read |
| `This watch run reported N checks but wrote nothing for X` | `entry.json` belongs to an earlier symbol and the desk will not chain off another name's bar | Normal and intentional. Watch a row that is still waiting instead |
| `the desk process was left running` after a long scan | The server's wrapper budget expired while the desk's own market stage was still inside its budget | Wrapper budgets now sit above the inner ones. If it recurs, run `desk.py scan orb` in a terminal and read its real finish time before assuming a stall |
| Search box returns only the envelope for an option strike | The live read refused, or this server predates the `detail` command in the allowlist | Click **⇊ Fetch live detail**, or run `python scripts/desk.py detail "NIFTY 23800 CE"` and read the refusal it prints under `absent`. If the button is unknown to the server, start a fresh one on another port |
| `vix not published` in `signals` | India VIX resolves on the chart host but its series reads ~100x the published index and no keyless second source answered | Use `rv%` (realized vol) and the ATM straddle's expected move. Do not paste a VIX from somewhere else into this desk |
| `(today's daily bar is still forming)` under the equity table | The close is live; `volx` uses the last completed session | Read volume ratios as yesterday's until after the close |
| `Port 8788 already has a listener` | Something else owns it | `--port 8801`. The desk never kills another process |
| `UnicodeEncodeError ... 'charmap'` | Console is cp1252 and something printed ₹ | The desk reconfigures UTF-8; if you bypassed it, run through `scripts/desk.py` |
| ZOMATO.NS missing | Directory tick and observed grid disagree | Leave it out; an UNVERIFIED tick blocks the ticket anyway |
| `NSE could not uniquely resolve equity TATAMOTORS.NS` | The chart host returns zero cash-equity rows for the pre-demerger symbol (it is now `TMCV` and `TMPV`) | Not a bug. `detail` refuses feed by feed and lists the names that are on file; the dashboard shows them as clickable chips. Search `TMCV` or `TMPV` |

## Hard limits, restated

* Paper only. `execution_enabled: false` everywhere; no broker connection exists
  in this code path.
* Whole shares, long only, cash segment for anything this desk can size. No
  futures, no margin, no shorting -- and no option order in any form. The option
  chain is read-only context: lot, tick, premium and notional for one lot are on
  file from the instrument directory, but there is no option exit or Greeks
  model, so a premium here can never become a ticket.
* ₹1,00,000 capital, ₹500 risk per trade, ₹1,500 daily loss, 3 trades/day,
  10 orders/day, max 75% in one name, 10% cash buffer.
* A sealed team room is evidence, not execution. `team` starts no agent and clears
  no trade; `finalize` can only ever print a paper ticket, and every roll-up ends
  with `live orders submitted: 0`. Reviewer identities are host-recorded
  provenance, not a signed attestation.
* Entry window 09:30-14:45, flat by 15:05.
* The holiday calendar is fail-closed and covers 2026 only. For 2027 supply a
  sourced `--calendar` file or every scan refuses to trust the date.
* NSE's public charting feed is experimental and can rate-limit or lag. Nothing
  here is a licensed feed.
