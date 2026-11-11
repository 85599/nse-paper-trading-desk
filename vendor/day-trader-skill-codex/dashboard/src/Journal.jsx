import React, { useEffect, useState } from "react";
import { readJSON, runAction } from "./deskapi";

const number = (value) =>
  Number.isFinite(value)
    ? new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 }).format(value)
    : "—";
const money = (value) => (Number.isFinite(value) ? "₹" + number(value) : "—");
const when = (value) =>
  new Intl.DateTimeFormat("en-IN", {
    timeZone: "Asia/Kolkata",
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
function EquityCurve({ points }) {
  if (points.length < 2)
    return (
      <div className="journal-no-chart">
        The realized P&amp;L curve appears after two recorded paper exits.
      </div>
    );
  const values = [0, ...points.map((p) => p.realized_pnl)],
    low = Math.min(...values),
    high = Math.max(...values),
    range = high - low || 1;
  const x = (i) => 20 + (i * 720) / (values.length - 1),
    y = (v) => 20 + ((high - v) * 145) / range;
  return (
    <svg
      viewBox="0 0 760 190"
      className="journal-curve"
      role="img"
      aria-label="Cumulative realized paper profit and loss after recorded exits"
    >
      <line x1="20" x2="740" y1={y(0)} y2={y(0)} className="op-high-line" />
      <polyline
        points={values.map((v, i) => `${x(i)},${y(v)}`).join(" ")}
        fill="none"
        stroke={values.at(-1) >= 0 ? "#34d399" : "#fb7185"}
        strokeWidth="2"
      />
      <text x="20" y="185">
        First paper exit → latest
      </text>
    </svg>
  );
}
function RiskEnvelope({ envelope, onRefresh, busy, error }) {
  if (!envelope)
    return (
      <section className="panel" aria-label="Risk envelope">
        <div className="panel-head">
          <h2>Risk envelope</h2>
          <button className="sig-secondary" disabled={!!busy} onClick={onRefresh}>
            {busy ? "Rebuilding…" : "Rebuild the envelope"}
          </button>
        </div>
        {error && <p className="notice error">{error}</p>}
        <p className="micro">
          Nothing is shown because the desk will not present an envelope from an
          earlier session — the ₹500 loss cap and the trade and order counters
          reset every trading day, so an old number would be a wrong number. This
          button re-runs the desk’s own envelope read, which recomputes those
          counters from the last risk decision saved in the workspace. It writes
          no new decision, sizes nothing and places nothing.
        </p>
      </section>
    );
  const labels = {
    loss_at_risk: "Loss at risk today",
    trades: "Entries today",
    orders: "Orders today",
    position_cost: "Cost in open positions",
    cash: "Capital committed",
  };
  const blocked = envelope.last_risk_decision?.startsWith("BLOCK");
  return (
    <section className="panel" aria-label="Risk envelope">
      <div className="panel-head">
        <h2>Risk envelope</h2>
        <span className="micro">
          {envelope.entry_window} · flatten {envelope.flat_by} · paper only, no live execution
        </span>
      </div>
      {Object.entries(envelope.bars || {}).map(([key, row]) => (
        <div key={key} style={{ margin: "10px 0" }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
            <span>{labels[key] || key}</span>
            <span className="micro">
              {row.used} / {row.limit}
            </span>
          </div>
          <div
            role="meter"
            aria-valuemin={0}
            aria-valuemax={row.limit}
            aria-valuenow={row.used}
            aria-label={labels[key] || key}
            style={{ height: 6, background: "rgba(255,255,255,.12)", borderRadius: 3, marginTop: 4 }}
          >
            <div
              style={{
                height: "100%",
                width: `${Math.min(100, row.pct || 0)}%`,
                background: row.pct >= 80 ? "#fb7185" : "#34d399",
                borderRadius: 3,
              }}
            />
          </div>
        </div>
      ))}
      <p className="micro" style={{ marginTop: 12 }}>
        Room now: {money(envelope.risk_room_inr)} riskable · {money(envelope.buy_room_inr)} buyable ·{" "}
        {envelope.trades_left} entries and {envelope.orders_left} orders left
        {envelope.counters_stale
          ? ` · counters from ${envelope.counters_date || "an earlier day"}`
          : ""}
      </p>
      {envelope.pending?.symbol && (
        <p className="micro">
          Pending: {envelope.pending.symbol} {envelope.pending.amount || envelope.pending.status || ""}
        </p>
      )}
      {(envelope.block_reasons || []).length > 0 && (
        <div className="notice error" style={{ marginTop: 10 }}>
          <strong>Last risk decision on file: {envelope.last_risk_decision}</strong>
          <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
            {envelope.block_reasons.slice(0, 6).map((reason, i) => (
              <li key={i}>{reason}</li>
            ))}
          </ul>
        </div>
      )}
      <p className="micro">
        <button className="sig-secondary" disabled={!!busy} onClick={onRefresh}>
          {busy ? "Rebuilding…" : "Rebuild the envelope"}
        </button>{" "}
        Recomputes every bar above from the local paper ledger and the last risk
        decision the desk wrote.
      </p>
    </section>
  );
}

function OptionChain({ chain, onRefresh, busy }) {
  if (!chain)
    return (
      <section className="panel" aria-label="Index option chain">
        <div className="panel-head">
          <h2>Index option chain</h2>
          <button className="sig-secondary" disabled={!!busy} onClick={() => onRefresh("NIFTY")}>
            {busy ? "Reading NSE…" : "Read the NIFTY chain from NSE"}
          </button>
        </div>
        <p className="micro">
          No chain is on disk for today, so nothing is drawn. The desk reads the
          NSE option-chain endpoint for the symbol you pick and stores that one
          snapshot with its own timestamp.
        </p>
      </section>
    );
  const rows = (chain.near_atm || []).slice(0, 9),
    iv = (value) => (value ? number(value) : "—");
  return (
    <section className="panel" aria-label="Index option chain">
      <div className="panel-head">
        <h2>Index option chain</h2>
        <span className="micro">
          {chain.symbol} {chain.expiry} · NSE {chain.timestamp}
        </span>
      </div>
      <p className="micro" style={{ marginTop: 0 }}>
        Underlying {number(chain.underlying)} · ATM {number(chain.atm_strike)} · PCR by OI{" "}
        {number(chain.pcr_oi)} · OI wall {number(chain.resistance_oi)} (call) /{" "}
        {number(chain.support_oi)} (put) · {chain.priced_strikes} of {chain.strikes} strikes priced
      </p>
      {rows.length ? (
        <table>
          <thead>
            <tr>
              <th>Strike</th>
              <th>CE LTP</th>
              <th>CE IV</th>
              <th>CE OI</th>
              <th>PE LTP</th>
              <th>PE IV</th>
              <th>PE OI</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr
                key={row.strike}
                style={
                  row.strike === chain.atm_strike
                    ? { background: "rgba(52,211,153,.08)" }
                    : undefined
                }
              >
                <td>{number(row.strike)}</td>
                <td>{money(row.CE.ltp)}</td>
                <td>{iv(row.CE.iv)}</td>
                <td>{number(row.CE.oi)}</td>
                <td>{money(row.PE.ltp)}</td>
                <td>{iv(row.PE.iv)}</td>
                <td>{number(row.PE.oi)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="micro">No priced strikes on this expiry.</p>
      )}
      <p className="micro">
        {chain.notes}{" "}
        <button className="sig-secondary" disabled={!!busy} onClick={() => onRefresh(chain.symbol)}>
          {busy ? "Reading NSE…" : "Re-read this chain from NSE"}
        </button>{" "}
        The desk asks NSE for {chain.symbol} and rewrites the saved snapshot.
      </p>
    </section>
  );
}

function TicketQueue({ data, onRefresh, busy, query, onQuery }) {
  const counts = data?.ticket_counts || {};
  const all = data?.tickets || [];
  const tickets = all.filter((ticket) =>
    String(ticket.symbol || "").toLowerCase().includes(query.toLowerCase()),
  );
  const tone = (verdict) =>
    verdict === "PASS" ? "green" : verdict === "BLOCK" ? "red" : "amber";
  const plain = (ticket) =>
    (ticket.reasons || []).length
      ? ticket.reasons.join(". ")
      : (ticket.warnings || []).length
        ? ticket.warnings.join(". ")
        : "Every check the engine runs on an order like this passed.";
  return (
    <section className="panel ticket-queue">
      <div className="panel-head">
        <div>
          <h2>Ticket attempts</h2>
          <p className="panel-subtitle">
            Every time you ran the paper chain, newest first — the orders that
            were sized and blocked, and the runs that stopped because no entry
            triggered. {counts.today ?? 0} today, {counts.saved ?? 0} on file
            across {counts.symbols ?? 0} symbols.
          </p>
        </div>
        <div className="ticket-tools">
          <input
            aria-label="Filter ticket attempts by symbol"
            placeholder="Filter symbol…"
            value={query}
            onChange={(event) => onQuery(event.target.value)}
          />
          <button className="sig-secondary" disabled={!!busy} onClick={onRefresh}>
            {busy ? "Running…" : "Re-run the risk check"}
          </button>
        </div>
      </div>
      {data?.tickets_note && <p className="notice error">{data.tickets_note}</p>}
      <div className="sig-chips">
        <span className="sig-chip">PASS {counts.pass ?? 0}</span>
        <span className="sig-chip">BLOCK {counts.block ?? 0}</span>
        <span className="sig-chip">
          NOT SIZED {counts.unsized ?? 0}
        </span>
        <span className="sig-chip">SHOWING {tickets.length}</span>
      </div>
      {!all.length && (
        <p className="journal-no-chart">
          The desk has not run a paper chain on this machine yet. Search a stock,
          then press <b>Run the paper chain</b> — that is the only button here
          that sizes an order. A scan, a search or a specialist review never does.
        </p>
      )}
      {!!all.length && !tickets.length && (
        <p className="journal-no-chart">
          None of the {all.length} attempts on file match “{query}”. Clear the
          filter to see them all.
        </p>
      )}
      {tickets.map((ticket) => (
        <article
          className="ticket-card"
          key={`${ticket.checked_at}-${ticket.symbol}-${ticket.fingerprint || ticket.stage}`}
        >
          <header>
            <b className="ticket-symbol">{ticket.symbol || "unknown symbol"}</b>
            <span className={`badge ${tone(ticket.verdict)}`}>{ticket.verdict}</span>
            <span className="micro">
              {ticket.stage === "risk" ? "sized and risk-checked" : "entry stage"} ·{" "}
              {ticket.command || "the desk"} ·{" "}
              {ticket.checked_at ? when(ticket.checked_at) : "time not recorded"} IST
            </span>
          </header>
          {ticket.stage === "risk" ? (
            <p className="ticket-call">
              {ticket.side} {ticket.qty} at {money(ticket.entry)} (entry from{" "}
              {ticket.entry_from || "the plan"}) · stop {money(ticket.stop)} ·
              target {money(ticket.target)} · max loss {money(ticket.max_loss)} ·
              net reward:risk after costs {number(ticket.net_reward_risk)} ·
              order {money(ticket.value_inr)} · tick {ticket.tick_status}
            </p>
          ) : (
            <p className="ticket-call">{ticket.note}</p>
          )}
          <p className={`ticket-why ${ticket.verdict === "PASS" ? "yes" : "no"}`}>
            {plain(ticket)}
          </p>
          {ticket.stage === "risk" && (
            <details className="ticket-info">
              <summary>Every number and check the engine used</summary>
              <dl className="ticket-levels">
                {[
                  ["side · quantity", `${ticket.side || "—"} · ${ticket.qty || "—"}`],
                  ["entry taken from", ticket.entry_from || "—"],
                  ["risk a share", money(ticket.risk_per_share)],
                  ["reward:risk before costs", number(ticket.reward_risk)],
                  ["tick verification", ticket.tick_status || "—"],
                  ["order fingerprint", String(ticket.fingerprint || "").slice(0, 12) || "—"],
                  ["sized by", ticket.command || "the desk"],
                ].map(([label, value]) => (
                  <div key={label}>
                    <dt>{label}</dt>
                    <dd>{value}</dd>
                  </div>
                ))}
              </dl>
              {!!(ticket.info || []).length && (
                <ul>
                  {ticket.info.map((line, j) => (
                    <li key={j}>{line}</li>
                  ))}
                </ul>
              )}
            </details>
          )}
          {ticket.stage !== "risk" && !!(ticket.info || []).length && (
            <p className="micro">{ticket.info.join(" · ")}</p>
          )}
        </article>
      ))}
      {!!(data?.decisions || []).length && (
        <div className="ticket-decisions">
          <h3>Your approve and decline replies</h3>
          <ul>
            {data.decisions
              .slice()
              .reverse()
              .map((event, i) => (
                <li key={i}>
                  <b>{event.command}</b> {event.symbol ? `${event.symbol} — ` : ""}
                  {event.outcome} · {when(event.time)} IST
                </li>
              ))}
          </ul>
          <p className="micro">
            Nothing above is a fill. A ticket is booked only after you type a reply
            that starts with “yes” into its own approval box.
          </p>
        </div>
      )}
    </section>
  );
}

export default function Journal({ refreshToken, onResearch }) {
  const [data, setData] = useState(null),
    [envelope, setEnvelope] = useState(null),
    [envelopeError, setEnvelopeError] = useState(""),
    [chain, setChain] = useState(null),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(""),
    [flattenNote, setFlattenNote] = useState(""),
    [query, setQuery] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    let busy = false;
    async function refresh() {
      if (busy) return;
      busy = true;
      try {
        const r = await fetch("/api/journal", {
          cache: "no-store",
          signal: controller.signal,
        });
        const d = await r.json();
        if (!r.ok) throw new Error(d.error || "Unable to read paper journal.");
        setData(d);
        setError("");
        try {
          const e = await fetch("/api/envelope", { cache: "no-store", signal: controller.signal });
          const ed = await e.json();
          setEnvelope(e.ok ? ed : null);
          setEnvelopeError(e.ok ? "" : ed.error || "");
        } catch (e) {
          if (e.name !== "AbortError") {
            setEnvelope(null);
            setEnvelopeError("");
          }
        }
        try {
          const o = await fetch("/api/options", { cache: "no-store", signal: controller.signal });
          const od = await o.json();
          setChain(o.ok ? od : null);
        } catch (o) {
          if (o.name !== "AbortError") setChain(null);
        }
      } catch (e) {
        if (e.name !== "AbortError") setError(e.message);
      } finally {
        busy = false;
      }
    }
    refresh();
    const timer = setInterval(refresh, 10000);
    return () => {
      controller.abort();
      clearInterval(timer);
    };
  }, [refreshToken]);
  const exits = (data?.exits || []).filter((e) =>
    e.symbol.toLowerCase().includes(query.toLowerCase()),
  );
  async function recheck() {
    setBusy("risk");
    try {
      // A BLOCK makes desk.py exit non-zero on purpose; the verdict it wrote is
      // what we want to show, so the refetch happens either way.
      await runAction("risk").catch(() => null);
      setData(await readJSON("/api/journal"));
      setError("");
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy("");
    }
  }
  // Both panels below refuse to draw a snapshot from another session, so each one
  // carries the button that re-runs the desk command it reads.
  async function refreshEnvelope() {
    setBusy("envelope");
    setEnvelopeError("");
    try {
      // `envelope` is the command that writes this snapshot. `risk` would instead write a
      // fresh decision into state/risk.json, which the consent chain keys off, and leave
      // the envelope exactly as stale as it was.
      await runAction("envelope");
      const e = await fetch("/api/envelope", { cache: "no-store" });
      if (!e.ok) {
        const doc = await e.json().catch(() => ({}));
        setEnvelopeError(doc.error || "The desk could not write a risk envelope just now.");
        setEnvelope(null);
      } else {
        setEnvelope(await e.json());
      }
    } catch (err) {
      setEnvelopeError(err.message);
      setEnvelope(null);
    } finally {
      setBusy("");
    }
  }
  // `flatten` proposes the close of one paper position and re-runs risk on it. It is
  // the only way to reach that command from this tab, so an open position is never a
  // terminal step.
  async function flattenPosition(symbol) {
    setBusy("flatten");
    setFlattenNote("The desk is sizing the close of " + symbol + ".");
    try {
      const event = await runAction("flatten", { symbol });
      const out = String(event.output || "").trim();
      setFlattenNote(
        out.slice(-700) ||
          `The desk answered nothing for ${symbol} (exit ${event.returncode}).`,
      );
      setData(await readJSON("/api/journal"));
    } catch (e) {
      setFlattenNote(e.message);
    } finally {
      setBusy("");
    }
  }
  async function refreshChain(symbol) {
    setBusy("chain");
    try {
      await runAction("options", { symbol });
      const o = await fetch("/api/options", { cache: "no-store" });
      setChain(o.ok ? await o.json() : null);
    } finally {
      setBusy("");
    }
  }
  return (
    <section className="journal-page">
      <div className="page-title">
        <div>
          <span className="eyebrow">RECORDED PAPER OUTCOMES</span>
          <h1>
            Trading journal<span>.</span>
          </h1>
          <p>
            Follow the account, actual simulated fills and results after
            recorded costs. Every ticket the desk sized today is listed above
            the fills, including the ones risk blocked before any fill existed.
          </p>
        </div>
      </div>
      {error && (
        <p className="notice error" role="alert">
          {error}
        </p>
      )}
      {data && (
        <TicketQueue
          data={data}
          busy={busy}
          onRefresh={recheck}
          query={query}
          onQuery={setQuery}
        />
      )}
      <RiskEnvelope
        envelope={envelope}
        busy={busy === "envelope" ? busy : ""}
        error={envelopeError}
        onRefresh={refreshEnvelope}
      />
      <OptionChain
        chain={chain}
        busy={busy === "chain" ? busy : ""}
        onRefresh={refreshChain}
      />
      {!data ? (
        <section className="panel empty">
          Reading the local paper ledger…
        </section>
      ) : data.status !== "RECORDED" ? (
        <section className="panel empty">
          <span className="empty-symbol">▤</span>
          <h2>
            {data.status === "NO_ACCOUNT"
              ? "No recorded paper trades yet."
              : "Paper ledger requires review."}
          </h2>
          <p>{data.note}</p>
          <p className="micro">
            Only approved, recorded simulated fills count as performance. A scan
            or specialist review creates no trade.
          </p>
        </section>
      ) : !(data.entries.length || data.exits.length || data.positions.length) ? (
        <section className="panel empty">
          <span className="empty-symbol">▤</span>
          <h2>No paper fill has been booked yet.</h2>
          <p>
            The ledger holds ₹{number(data.initial_capital)} of simulated capital
            and nothing else, so there is no profit, loss, win rate or position to
            show. That is the true state, not a missing number: every attempt
            above was blocked before a fill, or never brought to an approval box.
          </p>
          <p className="micro">
            A fill appears only when a ticket passes the risk check and you type
            your own reply starting with “yes”. Performance starts at the first
            recorded exit.
          </p>
        </section>
      ) : (
        <>
          <div className="op-summary-grid">
            {[
              ["Available paper cash", money(data.cash)],
              ["Realized net P&L", money(data.realized_pnl)],
              ["Recorded costs", money(data.total_costs)],
              ["Closed sell fills", number(data.closed_exits)],
            ].map(([label, value]) => (
              <div className="op-summary-card" key={label}>
                <span>{label}</span>
                <strong>{value}</strong>
              </div>
            ))}
          </div>
          <p className="journal-note">{data.note}</p>
          <section className="panel journal-chart">
            <div className="panel-head">
              <h2>Realized account P&amp;L</h2>
              <span className="micro">
                UNREALIZED P&amp;L REQUIRES CURRENT MARKS
              </span>
            </div>
            <EquityCurve points={data.equity_curve} />
            <div className="journal-stats">
              <span>
                Exit win rate{" "}
                <b>
                  {Number.isFinite(data.win_rate)
                    ? number(data.win_rate) + "%"
                    : "—"}
                </b>
              </span>
              <span>
                Profit factor <b>{number(data.profit_factor)}</b>
              </span>
              <span>
                Average net per exit <b>{money(data.average_exit_pnl)}</b>
              </span>
            </div>
          </section>
          <div className="research-layout">
            <section className="panel journal-table">
              <h2>Setup outcomes</h2>
              <p>
                FIFO allocations from recorded entries and exits. Market regime
                remains unclassified until recorded evidence is available.
              </p>
              <div className="op-table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Entry setup</th>
                      <th>Exit allocations</th>
                      <th>Net P&amp;L</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.groups.map((g) => (
                      <tr key={g.setup}>
                        <td>{g.setup}</td>
                        <td>{g.exit_allocations}</td>
                        <td
                          className={
                            g.net_pnl >= 0 ? "positive" : "op-negative"
                          }
                        >
                          {money(g.net_pnl)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {!data.groups.length && <p>No exits have been recorded.</p>}
            </section>
            <section className="panel journal-table">
              <h2>Open paper positions</h2>
              <div className="op-table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Symbol</th>
                      <th>Shares</th>
                      <th>Remaining lot cost</th>
                      <th>Close it on the desk</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.positions.map((p) => (
                      <tr key={p.ticker || p.symbol}>
                        <td>{p.ticker || p.symbol}</td>
                        <td>{p.shares || p.qty}</td>
                        <td>
                          {money(
                            (p.lots || []).reduce((sum, l) => sum + l.cost, 0),
                          )}
                        </td>
                        <td>
                          <button
                            className="sig-secondary"
                            disabled={!!busy}
                            onClick={() => flattenPosition(p.ticker || p.symbol)}
                          >
                            {busy === "flatten"
                              ? "Sizing the close…"
                              : "Propose closing"}
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {!data.positions.length && <p>No open paper positions.</p>}
              {flattenNote && <pre className="sig-out">{flattenNote}</pre>}
              <p className="micro">
                Proposing a close runs the desk’s own flatten command and re-runs
                risk on it. It changes nothing by itself — the exit is recorded
                only after you type your own reply starting with “yes”.
              </p>
            </section>
          </div>
          <section className="panel journal-table">
            <div className="panel-head">
              <h2>Recorded exits</h2>
              <input
                aria-label="Filter journal by symbol"
                placeholder="Filter symbol…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </div>
            <div className="op-table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Time · IST</th>
                    <th>Symbol</th>
                    <th>Shares</th>
                    <th>Fill price</th>
                    <th>Exit costs</th>
                    <th>Realized net P&amp;L</th>
                  </tr>
                </thead>
                <tbody>
                  {exits.map((e) => (
                    <tr key={e.id}>
                      <td>{when(e.at)}</td>
                      <td>
                        <button
                          className="journal-research-link"
                          onClick={() => onResearch(null, e.symbol)}
                        >
                          {e.symbol} ↗
                        </button>
                      </td>
                      <td>{e.qty}</td>
                      <td>{money(e.price)}</td>
                      <td>{money(e.fees)}</td>
                      <td
                        className={e.net_pnl >= 0 ? "positive" : "op-negative"}
                      >
                        {money(e.net_pnl)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
          <section className="panel journal-table journal-entries">
            <h2>Recorded entries and original plans</h2>
            <div className="op-table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Time · IST</th>
                    <th>Symbol / setup</th>
                    <th>Shares</th>
                    <th>Fill</th>
                    <th>Planned entry</th>
                    <th>Stop</th>
                    <th>Target</th>
                    <th>Entry costs</th>
                  </tr>
                </thead>
                <tbody>
                  {data.entries
                    .filter((e) =>
                      e.symbol.toLowerCase().includes(query.toLowerCase()),
                    )
                    .map((e) => (
                      <tr key={e.id}>
                        <td>{when(e.at)}</td>
                        <td>
                          <button
                            className="journal-research-link"
                            onClick={() => onResearch(null, e.symbol)}
                          >
                            {e.symbol} ↗
                          </button>
                          <small>{e.setup}</small>
                        </td>
                        <td>{e.qty}</td>
                        <td>{money(e.price)}</td>
                        <td>{money(e.planned_entry)}</td>
                        <td>{money(e.stop)}</td>
                        <td>{money(e.target)}</td>
                        <td>{money(e.fees)}</td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}
    </section>
  );
}
