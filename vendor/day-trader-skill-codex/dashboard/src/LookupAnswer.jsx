import React from "react";
import "./signals.css";
import { ageText, clock, num, signed, stamp } from "./deskapi.js";

// What the desk already knows about one query. Every number below came from a
// saved workspace file written by a desk command, with that file's own timestamp.
export default function LookupAnswer({
  doc,
  error,
  busy,
  onRefresh,
  onDetail,
  onReport,
  onSearch,
  onStep,
  raw,
}) {
  if (error)
    return (
      <section className="lk-panel">
        <h3>Search could not be answered</h3>
        <p className="lk-error">{error}</p>
        <button className="sig-secondary" onClick={onRefresh} disabled={!!busy}>
          {busy ? "Running…" : "Run the missing pass"}
        </button>
      </section>
    );
  if (!doc)
    return (
      <section className="lk-panel">
        <h3>Reading the desk state and one live NSE pass…</h3>
        <p className="sig-muted">
          Saved desk files answer for the symbol, then a live NSE read fetches
          bars, the chain, lot and tick for it. A strike query walks the listed
          expiries.
        </p>
      </section>
    );

  const p = doc.parsed || {},
    s = doc.sections || {},
    kind = p.kind;
  return (
    <section className="lk-panel" id="answer">
      <header className="lk-head">
        <div>
          <span className="sig-eyebrow">
            <i /> THE DESK ANSWERS ·{" "}
            {(doc.source || "").includes("live")
              ? "SAVED FILES + ONE LIVE NSE READ"
              : "SAVED FILES ONLY, NO REQUEST MADE"}
          </span>
          <h3>
            {doc.query}{" "}
            <span className="lk-kind">{kind}</span>
          </h3>
          <p className="lk-parsed">
            {kind === "option" ? (
              <>
                reading it as <b>{p.side}</b> at <b>{num(p.strike, 0)}</b> on{" "}
                <b>{p.chain_symbol}</b>
                {p.expiry ? ` · expiry ${p.expiry}` : " · nearest saved expiry"}
                {" → "}index <b>{p.symbol}</b>
              </>
            ) : (
              <>
                reading it as <b>{p.symbol || p.chain_symbol || p.bare}</b>
              </>
            )}
          </p>
        </div>
        <div className="lk-head-right">
          <span>{stamp(doc.as_of)}</span>
          <button
            className="sig-primary"
            disabled={!!busy || kind === "empty"}
            onClick={onDetail}
          >
            {busy === "live NSE detail" ? "Reading NSE…" : "⇊ Fetch live detail"}
          </button>
          <button
            className="sig-secondary"
            disabled={!!busy || kind === "empty"}
            onClick={onReport}
          >
            {busy === "symbol report" ? "Writing report…" : "¶ Report this symbol"}
          </button>
          <button className="sig-secondary" disabled={!!busy} onClick={onRefresh}>
            ⟳ Re-read saved files
          </button>
        </div>
      </header>

      {!doc.answered && (
        <p className="lk-error">
          {doc.unknown || "Nothing in the desk state or in NSE matched this query."}{" "}
          The live NSE pass gave nothing either — check the spelling of the symbol
          or the strike, or the feeds listed below.
        </p>
      )}

      <div className="lk-grid">
        {s.detail && <DetailBlock row={s.detail} onSearch={onSearch} />}
        {s.signals && <SignalsBlock row={s.signals} />}
        {s.option && <OptionBlock row={s.option} />}
        {s.scan && <ScanBlock row={s.scan} />}
        {s.ledger && <LedgerBlock row={s.ledger} />}
        {s.news && <NewsBlock row={s.news} />}
        {s.envelope && <EnvelopeBlock row={s.envelope} />}
      </div>

      {!!Object.keys(doc.absent || {}).length && (
        <details className="lk-absent" open={!doc.answered}>
          <summary>
            Searched and not found ({Object.keys(doc.absent).length})
          </summary>
          {Object.entries(doc.absent).map(([name, why]) => (
            <p key={name}>
              <b>{name}</b> — {why}
            </p>
          ))}
        </details>
      )}

      {!!doc.next?.length && (
        <div className="lk-next">
          <h4>Read next — each line is one click</h4>
          <p className="sig-muted">
            These are the desk’s own allowlisted reads. The button runs it on this
            machine, then the answer above re-reads the files it wrote. No order is
            placed and no terminal is involved.
          </p>
          {doc.next.map((step) => (
            <p key={`${step.command || "search"}|${step.label}`} className="lk-step">
              <button
                className={`sig-secondary${step.command === "setup" ? " lk-step--chain" : ""}`}
                disabled={!!busy}
                onClick={() => onStep?.(step)}
              >
                {busy === step.label ? "Running…" : step.label}
              </button>
              <span>{step.why}</span>
            </p>
          ))}
        </div>
      )}
      {raw && (
        <details className="lk-raw" open>
          <summary>
            Desk output · {(raw.command || "").replace(/^\w/, (c) =>
              c.toUpperCase())} · {(raw.returncode ?? "—") === 0
              ? "completed"
              : `stopped (exit ${raw.returncode})`} · {num(raw.seconds, 1)}s
          </summary>
          <pre className="sig-out">{raw.output}</pre>
        </details>
      )}
      <p className="sig-muted lk-foot">
        {doc.source}. Paper only — nothing here places an order.
      </p>
    </section>
  );
}

function Panel({ title, row, children, tone, wide }) {
  return (
    <article
      className={`lk-card${tone ? " sig-tone-" + tone : ""}${
        wide ? " lk-card--wide" : ""
      }`}
    >
      <h4>
        {title}
        <span>
          {clock(row.data_as_of)} · {ageText(row.age_seconds)}
          {row.stale ? " · older than 15 min" : ""}
        </span>
      </h4>
      {children}
    </article>
  );
}

function SignalsBlock({ row }) {
  const i = row.indicators || {};
  return (
    <Panel title={`Signal · ${row.symbol}`} row={row}>
      <p className="lk-verdict">
        <span className={`sig-verdict sig-tone-${row.actionable ? (row.verdict === "BUY" ? "buy" : row.verdict === "SELL" ? "sell" : "hold") : "blocked"}`}>
          {row.verdict}
        </span>
        <b>score {signed(row.score, 0)}</b>
        <em>realized vol {num(row.realized_vol_pct, 1)}%</em>
      </p>
      <div className="lk-nums">
        {[
          ["ADX", num(i.adx, 1)],
          ["+DI", num(i.di_plus, 1)],
          ["-DI", num(i.di_minus, 1)],
          ["RSI", num(i.rsi_14, 1)],
          ["ATR%", num(i.atr_pct)],
          ["MACD hist", signed(i.macd?.histogram)],
          ["Close", num(i.close)],
          ["Vol ×", num(i.volume_x)],
        ].map(([label, value]) => (
          <p key={label}>
            <span>{label}</span>
            <b>{value}</b>
          </p>
        ))}
      </div>
      <ul className="sig-reasons">
        {(row.reasons || []).map((reason) => (
          <li key={reason}>{reason}</li>
        ))}
      </ul>
      {(row.vetoes || []).map((veto) => (
        <p className="sig-veto" key={veto}>
          ! {veto}
        </p>
      ))}
      {row.bar_forming && (
        <p className="sig-forming">
          Today’s daily bar is still forming — close and volume are not final.
        </p>
      )}
    </Panel>
  );
}

function OptionBlock({ row }) {
  const legs = row.legs || {};
  return (
    <Panel title={`Option · ${row.symbol} ${row.side} ${num(row.strike, 0)}`} row={row}>
      <p className="lk-verdict">
        <b>{row.moneyness}</b>
        <em>
          {num(row.distance_from_atm)} points from ATM {num(row.atm_strike, 0)}
        </em>
        <em>expiry {row.expiry}</em>
      </p>
      <table className="sig-mini">
        <thead>
          <tr>
            <th>Leg</th>
            <th>LTP</th>
            <th>IV</th>
            <th>OI</th>
            <th>Δ OI</th>
            <th>Volume</th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(legs).map(([side, leg]) => (
            <tr key={side}>
              <td>{side === row.side ? <b>{side} (asked)</b> : side}</td>
              <td>{num(leg.ltp)}</td>
              <td>{num(leg.iv)}</td>
              <td>{num(leg.oi, 0)}</td>
              <td>{signed(leg.chg_oi, 0)}</td>
              <td>{num(leg.volume, 0)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="sig-levels">
        Underlying {num(row.underlying)} · PCR by OI {num(row.pcr_oi)} · call
        wall {num(row.resistance_oi, 0)} · put wall {num(row.support_oi, 0)} ·{" "}
        {num(row.priced_strikes, 0)}/{num(row.strikes, 0)} strikes priced
      </p>
      <p className="sig-levels">
        NSE chain timestamp {row.nse_timestamp} ·{" "}
        {(() => {
          const dates = row.expiry_dates || [];
          if (!dates.length) return "no saved expiry list";
          return `expiries saved: ${dates.slice(0, 3).join(", ")}${
            dates.length > 3 ? ` (+${dates.length - 3} more)` : ""
          }`;
        })()}
      </p>
      <p className="sig-veto">
        ! Context only: the desk is configured long-only cash (allow_options
        false) and the engine has no option exit or Greeks model, so an option
        leg is never a sized order. Lot size and tick for the strike are on the
        live detail card above.
      </p>
    </Panel>
  );
}

function ScanBlock({ row }) {
  return (
    <Panel title={`Saved daily scan · ${row.symbol}`} row={row}>
      <p className="lk-verdict">
        <b>{(row.setups || []).join(", ") || "no setup matched"}</b>
        <em>candle {clock(row.candle_as_of)}</em>
      </p>
      <div className="lk-nums">
        {[
          ["Price", num(row.price)],
          ["RSI", num(row.rsi, 1)],
          ["MA20", num(row.ma20)],
          ["20d high", num(row.high20)],
          ["Vol ×", num(row.volume_x)],
          ["5-day move", `${signed(row.run_pct)}%`],
        ].map(([label, value]) => (
          <p key={label}>
            <span>{label}</span>
            <b>{value}</b>
          </p>
        ))}
      </div>
      <p className="sig-muted">
        The scan is a saved run, not a live quote — press Recalculate signals on
        the board for today’s bars.
      </p>
    </Panel>
  );
}

function LedgerBlock({ row }) {
  const positions = row.position || [],
    alerts = row.alerts || [],
    saved = ["entry", "plan", "orders"].filter((key) => row[key]);
  return (
    <Panel title={`Paper ledger · ${row.symbol || ""}`} row={row}>
      {positions.map((p, i) => (
        <p className="sig-levels" key={i}>
          open {p.shares} shares at {num(p.average_cost)} · stop{" "}
          {num(p.stop)} · target {num(p.target)}
        </p>
      ))}
      {alerts.map((a, i) => (
        <p className="sig-levels" key={"a" + i}>
          alert {a.type} @ {num(a.level)} — {a.status || "armed"}
        </p>
      ))}
      {saved.map((key) => (
        <p className="sig-levels" key={key}>
          <b>{key}</b> ({row[key].file}):{" "}
          {Object.entries(row[key].summary || {})
            .map(([name, value]) => `${name} ${value}`)
            .join(" · ")}
        </p>
      ))}
      <p className="sig-levels">
        Cash {num(row.cash, 0)} · realized {num(row.realized_pnl, 0)}
      </p>
      {!positions.length && !alerts.length && !saved.length && (
        <p className="sig-muted">
          No open position, alert or saved plan for this symbol.
        </p>
      )}
    </Panel>
  );
}

function NewsBlock({ row }) {
  return (
    <Panel title="Headlines" row={row}>
      <ul className="sig-headlines">
        {(row.items || []).slice(0, 4).map((item) => (
          <li key={item.link || item.title}>
            <span className="sig-when">{stamp(item.published_at)}</span>
            <b className="sig-who">{item.symbol || "market"}</b>
            <a href={item.link} target="_blank" rel="noreferrer">
              {item.title}
            </a>
            <em>{item.publisher}</em>
          </li>
        ))}
      </ul>
      <p className="sig-muted">{row.note}</p>
    </Panel>
  );
}

function EnvelopeBlock({ row }) {
  return (
    <Panel title="Risk envelope (desk-wide)" row={row}>
      <div className="lk-nums">
        {[
          ["Cash", num(row.cash, 0)],
          ["Risk room", num(row.risk_room_inr, 0)],
          ["Buy room", num(row.buy_room_inr, 0)],
          ["Trades left", num(row.trades_left, 0)],
          ["Orders left", num(row.orders_left, 0)],
          ["Loss used", `${num(row.loss_at_risk?.used, 0)} / ${num(row.loss_at_risk?.limit, 0)}`],
        ].map(([label, value]) => (
          <p key={label}>
            <span>{label}</span>
            <b>{value}</b>
          </p>
        ))}
      </div>
      <p className="sig-levels">
        Entry window {row.entry_window} · last risk decision on file:{" "}
        <b>{row.last_risk_decision}</b>
        {row.counters_stale
          ? ` (counters from ${row.counters_date})`
          : ""}
      </p>
      {(row.block_reasons || []).slice(0, 4).map((reason) => (
        <p className="sig-veto" key={reason}>
          ! {reason}
        </p>
      ))}
    </Panel>
  );
}

function Section({ title, meta, children }) {
  return (
    <div className="lk-sec">
      <h5>
        {title}
        {meta && <span>{meta}</span>}
      </h5>
      {children}
    </div>
  );
}

function Nums({ items }) {
  return (
    <div className="lk-nums">
      {items.map(([label, value]) => (
        <p key={label}>
          <span>{label}</span>
          <b>{value}</b>
        </p>
      ))}
    </div>
  );
}

function LegsTable({ legs, side }) {
  const rows = Object.entries(legs || {});
  if (!rows.length) return null;
  return (
    <table className="sig-mini">
      <thead>
        <tr>
          <th>Leg</th>
          <th>LTP</th>
          <th>IV</th>
          <th>OI</th>
          <th>Δ OI</th>
          <th>Volume</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(([name, leg]) => (
          <tr key={name}>
            <td>{name === side ? <b>{name} (asked)</b> : name}</td>
            <td>{num(leg?.ltp)}</td>
            <td>{num(leg?.iv)}</td>
            <td>{num(leg?.oi, 0)}</td>
            <td>{signed(leg?.chg_oi, 0)}</td>
            <td>{num(leg?.volume, 0)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// One live pass over NSE for the searched symbol: daily bars and the verdict they
// give, today's 5m tape, the chain or the exact strike with its lot and tick, and
// what the desk's own cash and ₹500 cap would allow. Every feed that refused is
// named with its own message -- nothing here is filled in from a guess.
function DetailBlock({ row, onSearch }) {
  const s = row.sections || {},
    daily = s.daily,
    intra = s.intraday,
    option = s.option,
    chain = s.chain,
    id = row.identity || {},
    size = row.sizing,
    i = (daily && daily.indicators) || {},
    w = (daily && daily.window) || {},
    tape = option || chain,
    bias = (tape && tape.bias) || {},
    contract = (option && option.contract) || null,
    orb = (intra && intra.orb) || null,
    filings = s.announcements,
    filed = s.financials,
    missing = Object.entries(row.absent || {});
  return (
    <Panel
      wide
      title={`Live NSE detail · ${row.query}`}
      row={{ ...row, data_as_of: row.data_as_of || row.fetched_at }}
    >
      <p className="sig-levels">
        <b>{id.name || id.symbol || row.query}</b> · {id.exchange || "NSE"}
        {id.kind ? ` · ${id.kind}` : ""}
        {id.isin ? ` · ISIN ${id.isin}` : ""}
        {id.tick_inr ? ` · tick ₹${num(id.tick_inr)} (${id.tick_source || "tick source not stated"})` : ""}
        {id.freeze_quantity ? ` · freeze qty ${num(id.freeze_quantity, 0)}` : ""}
        {row.directory_cached
          ? ` · instrument directory cached ${row.directory_cached}`
          : ""}
        {" · fetched "}
        {clock(row.fetched_at)}
        {row.cache_hit ? " · served from the 15-minute detail cache" : ""}
      </p>

      {!!(row.suggestions || []).length && (
        <p className="lk-suggest">
          <span>NSE has no scrip by that name -- closest live names:</span>
          {row.suggestions.map((name) => (
            <button
              key={name}
              className="sig-chip"
              onClick={() => onSearch?.(name)}
            >
              {name}
            </button>
          ))}
        </p>
      )}

      {daily && (
        <Section
          title={`Daily bars · ${daily.symbol}`}
          meta={`${daily.bars || w.sessions || "—"} sessions ${w.from || "?"} → ${
            w.to || "?"
          } · bar ${clock(daily.bar_as_of)}${
            daily.bar_forming ? " · still forming" : ""
          }`}
        >
          <p className="lk-verdict">
            <span
              className={`sig-verdict sig-tone-${
                daily.actionable
                  ? daily.verdict === "BUY"
                    ? "buy"
                    : daily.verdict === "SELL"
                      ? "sell"
                      : "hold"
                  : "blocked"
              }`}
            >
              {daily.verdict}
            </span>
            <b>score {signed(daily.score, 0)}</b>
            <em>realized vol {num(daily.realized_vol_pct, 1)}%</em>
          </p>
          <Nums
            items={[
              ["ADX", num(i.adx, 1)],
              ["+DI", num(i.di_plus, 1)],
              ["-DI", num(i.di_minus, 1)],
              ["RSI", num(i.rsi_14, 1)],
              ["ATR", num(i.atr_14)],
              ["ATR%", num(i.atr_pct)],
              ["MACD hist", signed(i.macd?.histogram)],
              ["Close", num(i.close)],
              ["EMA20", num(i.ema20)],
              ["EMA50", num(i.ema50)],
              ["Day chg", `${signed(i.change_pct)}%`],
              ["5-day", `${signed(i.run_5_pct)}%`],
              ["Vol ×", num(i.volume_x)],
              ["20-bar high", num(i.high_20)],
              ["20-bar low", num(i.low_20)],
            ]}
          />
          <ul className="sig-reasons">
            {(daily.reasons || []).map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
          {(daily.vetoes || []).map((veto) => (
            <p className="sig-veto" key={veto}>
              ! {veto}
            </p>
          ))}
          <p className="sig-levels">
            Window: high {num(w.window_high)} on {w.window_high_date} (
            {signed(w.pct_from_window_high)}% from close) · low{" "}
            {num(w.window_low)} on {w.window_low_date} ({signed(w.pct_from_window_low)}%
            ) · 20-session avg volume {num(w.avg_volume_20, 0)} · volume{" "}
            {num(w.last_volume, 0)} on {w.volume_bar_date} ({num(w.volume_x_20)}×) · 5d{" "}
            {signed(w.return_5_pct)}% · 20d {signed(w.return_20_pct)}%
          </p>
          {daily.bar_forming && (
            <p className="sig-forming">
              Today’s daily bar is still forming — close and volume are not final.
            </p>
          )}
        </Section>
      )}

      {intra && (
        <Section
          title="Today · 5m tape"
          meta={`${intra.bars} bars to ${clock(intra.as_of)} · ${
            intra.bars_today || intra.bars
          } today`}
        >
          <Nums
            items={[
              ["Open", num(intra.open)],
              ["Last", num(intra.last)],
              ["Day high", num(intra.day_high)],
              ["Day low", num(intra.day_low)],
              ["Range", `${num(intra.range_pct)}%`],
              ["Prev close", num(intra.prev_close)],
              ["Gap", `${signed(intra.gap_pct)}%`],
              ["5m vol", `${num(intra.intraday_realized_vol_pct, 1)}%`],
            ]}
          />
          {orb && (
            <p className="sig-levels">
              15-min ORB {num(orb.low)}–{num(orb.high)} ({num(orb.width_pct)}% wide) ·
              price is{" "}
              <span className={`sig-state sig-state-${intra.state || "inside"}`}>
                {intra.state || "inside"}
              </span>{" "}
              · {num(intra.to_break_up)} to break up · {num(intra.to_break_down)} to
              break down
            </p>
          )}
          {!orb && (
            <p className="sig-muted">
              No opening range: NSE gave fewer than three 5m bars before 09:30 IST for
              this session.
            </p>
          )}
        </Section>
      )}

      {tape && (
        <Section
          title={
            option
              ? `Option · ${option.symbol} ${num(option.strike, 0)} ${option.side}`
              : `Option chain · ${tape.symbol}`
          }
          meta={`${tape.expiry} · ${tape.segment || "NSE"} · NSE stamp ${
            tape.nse_timestamp || "—"
          }`}
        >
          <p className="lk-verdict">
            {option ? (
              <>
                <b>{option.moneyness || "moneyness n/a"}</b>
                <em>
                  {num(option.distance_from_atm)} pts from ATM{" "}
                  {num(tape.atm_strike, 0)}
                </em>
                <em>
                  {signed(option.pct_from_underlying)}% from underlying{" "}
                  {num(tape.underlying)}
                </em>
              </>
            ) : (
              <>
                <b>{num(tape.pcr_oi)} PCR by OI</b>
                <em>
                  ATM {num(tape.atm_strike, 0)} · underlying {num(tape.underlying)}
                </em>
                <em>
                  {num(tape.priced_strikes, 0)}/{num(tape.strikes, 0)} strikes priced
                </em>
              </>
            )}
          </p>
          {option && <LegsTable legs={option.legs} side={option.side} />}
          {contract ? (
            <Nums
              items={[
                ["Contract", contract.trading_symbol],
                ["Lot", num(contract.lot_size, 0)],
                ["Min lot", num(contract.minimum_lot, 0)],
                ["Tick", `₹${num(contract.tick_inr)}`],
                ["Cash 1 lot", `₹${num(option.cash_for_one_lot, 0)}`],
                ["Notional 1 lot", `₹${num(option.notional_for_one_lot, 0)}`],
                ["1 tick on 1 lot", `₹${num(option.tick_value_one_lot)}`],
              ]}
            />
          ) : (
            option && (
              <p className="sig-veto">
                ! {option.contract_note ||
                  "no lot or tick record in the cached instrument directory for this strike"}
              </p>
            )
          )}
          <p className="sig-levels">
            call wall {num(tape.resistance_oi, 0)} · put wall{" "}
            {num(tape.support_oi, 0)} · today call OI {signed(tape.call_oi_change, 0)} ·
            put OI {signed(tape.put_oi_change, 0)} ·{" "}
            {(tape.expiry_dates || []).length} expiries listed
            {(tape.expiry_dates || []).length
              ? ` (${(tape.expiry_dates || []).slice(0, 3).join(", ")})`
              : ""}
          </p>
          {!!(bias.reasons || []).length && (
            <ul className="sig-reasons">
              {bias.reasons.slice(0, 5).map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          )}
          {tape.near_atm?.length > 0 && (
            <details className="lk-absent">
              <summary>Near-ATM strikes with lot size</summary>
              <table className="sig-mini">
                <thead>
                  <tr>
                    <th>Strike</th>
                    <th>CE LTP</th>
                    <th>PE LTP</th>
                    <th>Lot</th>
                  </tr>
                </thead>
                <tbody>
                  {tape.near_atm.map((r) => (
                    <tr key={r.strike}>
                      <td>{num(r.strike, 0)}</td>
                      <td>{num(r.CE?.ltp)}</td>
                      <td>{num(r.PE?.ltp)}</td>
                      <td>{num(r.contract?.lot_size, 0)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
          )}
          <p className="sig-veto">
            ! Context only: the desk is long-only cash (allow_options false) and the
            engine has no option exit or Greeks model, so this premium is not sized and
            cannot become a ticket.
          </p>
        </Section>
      )}

      {filings && (
        <Section
          title="What the issuer filed with the exchange"
          meta={`${filings.count_in_window} disclosures in ${filings.days} days · ${
            filings.fetched_at
          }${filings.cache_hit ? " (cache)" : ""}`}
        >
          {filings.items.length === 0 && (
            <p className="sig-muted">
              NSE listed no disclosure for {filings.symbol} in the last {filings.days} days.
            </p>
          )}
          <ul className="sig-reasons lk-filings">
            {filings.items.map((row) => (
              <li key={row.url}>
                <b>{row.published_display}</b> — {row.category}
                <span className="sig-muted"> {row.text}</span>{" "}
                <a href={row.url} target="_blank" rel="noreferrer">
                  filing{row.attachment_size ? ` (${row.attachment_size})` : ""}
                </a>
              </li>
            ))}
          </ul>
          <p className="sig-muted">{filings.note}</p>
        </Section>
      )}

      {filed && filed.latest && (
        <Section
          title="The numbers the issuer filed (exchange XBRL)"
          meta={`${filed.latest.filing.scope || filed.scope} · period ended ${
            filed.latest.filing.period_end_display || "not stated"
          } · filed ${filed.latest.filing.published_display || "time not stated"}`}
        >
          <p className="sig-levels">
            <a href={filed.latest.filing.url} target="_blank" rel="noreferrer">
              {filed.latest.filing.host}
            </a>{" "}
            · {num(filed.latest.fact_count, 0)} figures read ·{" "}
            {filed.latest.filing.audited || "audit status not stated"} ·{" "}
            {filed.latest.filing.filing_kind || "filing"} · fetched{" "}
            {clock(filed.fetched_at)}
            {filed.cache_hit ? " · 6-hour filing cache" : ""}
          </p>
          {filed.latest.blocks.map((block) => (
            <div className="lk-filing-block" key={block.title}>
              <h6>{block.title}</h6>
              <table className="sig-mini">
                <thead>
                  <tr>
                    <th>Figure</th>
                    <th>As filed</th>
                    <th>Value as filed</th>
                  </tr>
                </thead>
                <tbody>
                  {block.figures.map((row) => (
                    <tr key={block.title + row.label}>
                      <td>{row.label}</td>
                      <td>
                        <b>{row.display}</b>
                      </td>
                      <td>
                        {row.value_as_filed} {row.unit || "no unit stated"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}
          <p className="sig-muted">{filed.note}</p>
        </Section>
      )}

      {size && (
        <Section
          title="Cash sizing the desk's own limits allow"
          meta={size.applies_to ? `applies to ${size.applies_to}` : "whole shares"}
        >
          <Nums
            items={[
              ["Stop rule", size.stop_rule],
              ["Risk cap", `₹${num(size.risk_cap_inr, 0)}`],
              ["Cash", `₹${num(size.cash_inr, 0)}`],
              ["Shares by risk", num(size.shares_by_risk_cap, 0)],
              ["Shares by cash", num(size.shares_by_cash, 0)],
              ["Shares", num(size.shares, 0)],
              ["Position", `₹${num(size.position_value_inr, 0)}`],
              ["Risk at stop", `₹${num(size.risk_at_stop_inr, 0)}`],
            ]}
          />
          <p className="sig-muted">{size.note}</p>
        </Section>
      )}
      {row.sizing_note && <p className="sig-muted">{row.sizing_note}</p>}

      {!!missing.length && (
        <details className="lk-absent" open>
          <summary>Feeds that did not answer ({missing.length})</summary>
          {missing.map(([name, why]) => (
            <p key={name}>
              <b>{name}</b> — {why}
            </p>
          ))}
        </details>
      )}
    </Panel>
  );
}
