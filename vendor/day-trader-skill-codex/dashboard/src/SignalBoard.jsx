import React, { useEffect, useMemo, useState } from "react";
import "./signals.css";
import {
  ageText,
  clock,
  num,
  readJSON,
  runAction,
  signed,
  stamp,
} from "./deskapi.js";

// One board for everything the desk can decide on: equities, indices and the
// expiry tape, each with the indicator numbers the verdict was built from.
const VERDICTS = ["all", "BUY", "HOLD", "SELL", "blocked"];
const KINDS = ["all", "equity", "index", "option"];
const SORTS = {
  score: (a, b) => b.score - a.score || a.symbol.localeCompare(b.symbol),
  symbol: (a, b) => a.symbol.localeCompare(b.symbol),
  adx: (a, b) => (b.indicators.adx ?? -1) - (a.indicators.adx ?? -1),
  rsi: (a, b) => (b.indicators.rsi_14 ?? -1) - (a.indicators.rsi_14 ?? -1),
  move: (a, b) =>
    (b.indicators.change_pct ?? -99) - (a.indicators.change_pct ?? -99),
};

export default function SignalBoard({ onResearch, refreshToken }) {
  const [doc, setDoc] = useState(null),
    [error, setError] = useState(""),
    [fix, setFix] = useState(null),
    [news, setNews] = useState(null),
    [busy, setBusy] = useState(""),
    [output, setOutput] = useState(null),
    [drawer, setDrawer] = useState(false);
  const [chain, setChain] = useState({ command: "", symbol: "" });
  const [query, setQuery] = useState(""),
    [verdict, setVerdict] = useState("all"),
    [kind, setKind] = useState("all"),
    [sort, setSort] = useState("score"),
    [open, setOpen] = useState("");
  // NSE publishes its own constituent file per Nifty index; the desk reads that file
  // and never a third-party list, so an index the exchange does not publish is
  // refused out loud instead of being guessed.
  const [indexes, setIndexes] = useState([]),
    [index, setIndex] = useState(""),
    [members, setMembers] = useState(null),
    [memberNote, setMemberNote] = useState(""),
    [restrict, setRestrict] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    let dead = false;
    async function load() {
      try {
        const next = await readJSON("/api/signals", controller.signal);
        if (!dead) {
          setDoc(next);
          setError("");
          setFix(null);
        }
      } catch (e) {
        if (e.name !== "AbortError" && !dead) {
          setError(e.message);
          setFix(e.fix || null);
        }
      }
      try {
        const feed = await readJSON("/api/news", controller.signal);
        if (!dead) setNews(feed);
      } catch {
        if (!dead) setNews(null);
      }
    }
    load();
    const timer = setInterval(load, 60000);
    return () => {
      dead = true;
      controller.abort();
      clearInterval(timer);
    };
  }, [refreshToken]);

  useEffect(() => {
    readJSON("/api/universe")
      .then((doc) => setIndexes(doc.indexes || []))
      .catch(() => setIndexes([]));
  }, []);

  async function run(command, body = {}) {
    const label = [
      command,
      body.symbol,
      body.reply ? '"' + body.reply + '"' : "",
      body.risk ? "₹" + body.risk + " budget" : "",
    ]
      .filter(Boolean)
      .join(" ");
    setBusy(label);
    try {
      const event = await runAction(command, body);
      setOutput({ ...event, command });
      setError("");
      if (command === "signals" || command === "news") {
        setDoc(await readJSON("/api/signals"));
        setNews(await readJSON("/api/news"));
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy("");
    }
  }

  // Membership first: one NSE constituent file, cached by the desk. This never
  // touches the market, so it answers in a second and reads nothing but the list.
  async function findStocks() {
    if (!index || busy) return;
    setBusy("universe " + index);
    setMemberNote("");
    try {
      const event = await runAction("universe", { index });
      try {
        setMembers(
          await readJSON("/api/universe?index=" + encodeURIComponent(index)),
        );
      } catch (e) {
        // The command ran but NSE's file did not come back, so the cache is still
        // empty. The desk's own stdout says why, and it is truer than the refusal.
        setMembers(null);
        setMemberNote(
          String(event.output || "")
            .split("\n")
            .filter(Boolean)
            .slice(-1)[0] || e.message,
        );
      }
    } catch (e) {
      setMembers(null);
      setMemberNote(e.message);
    } finally {
      setBusy("");
    }
  }
  // Verdicts for those stocks need a scan of them, and a scan of an index rewrites
  // the daily snapshot with exactly that index's symbols. The board is recalculated
  // in the same press because a scan alone leaves it describing the old universe.
  async function scanIndex() {
    if (!index || busy) return;
    setBusy("scan index " + index);
    setMemberNote("");
    try {
      const event = await runAction("scan index", { index });
      await runAction("signals");
      setDoc(await readJSON("/api/signals"));
      setMembers(await readJSON("/api/universe?index=" + encodeURIComponent(index)));
      setRestrict(true);
      const line = String(event.output || "")
        .split("\n")
        .filter(Boolean)
        .slice(-1)[0];
      setMemberNote(
        "Scanned " + index + " on the desk. " + (line || "") +
          " The board now holds this index only; the ＋ New request dialog’s universe scan puts the full list back."
      );
    } catch (e) {
      setMemberNote(e.message);
    } finally {
      setBusy("");
    }
  }

  const bySymbol = useMemo(() => {
    const map = {};
    [...(doc?.equity || []), ...(doc?.index || [])].forEach((row) => {
      map[String(row.symbol || "").toUpperCase()] = row;
    });
    return map;
  }, [doc]);

  const rows = useMemo(() => {
    if (!doc) return [];
    const all = [...(doc.equity || []), ...(doc.index || [])];
    const text = query.trim().toLowerCase();
    const only =
      restrict && members
        ? new Set((members.rows || []).map((r) => String(r.symbol).toUpperCase()))
        : null;
    return all
      .filter(
        (row) =>
          (kind === "all" || row.kind === kind) &&
          (!text || row.symbol.toLowerCase().includes(text)) &&
          (!only || only.has(String(row.symbol).toUpperCase())) &&
          (verdict === "all" ||
            (verdict === "blocked"
              ? !row.actionable
              : row.verdict === verdict && row.actionable)),
      )
      .sort(SORTS[sort]);
  }, [doc, query, kind, verdict, sort, members, restrict]);

  const counts = doc?.counts || {};
  return (
    <section className="sig-board" id="signals">
      <div className="sig-head">
        <div>
          <span className="sig-eyebrow">
            <i /> DAILY RULES · ADX · +DI / -DI · MACD · RSI · ATR · NSE CLOSES
          </span>
          <h2>
            Signal board<span>.</span>
          </h2>
          <p>
            Every symbol the desk can read, with the indicator numbers behind
            the BUY / SELL / HOLD call and the expiry tape next to it.
          </p>
        </div>
        <div className="sig-run">
          <button
            className="sig-primary"
            disabled={!!busy}
            onClick={() => run("signals", { limit: 30 })}
          >
            {busy ? "Desk is running…" : "⟳ Recalculate signals"}
          </button>
          <button
            className="sig-secondary"
            disabled={!!busy}
            onClick={() => run("news", {})}
          >
            Headlines
          </button>
          <button
            className="sig-secondary"
            disabled={!!busy}
            onClick={() => {
              setDrawer(true);
              run("report");
            }}
          >
            Session report
          </button>
        </div>
      </div>

      {error && (
        <p className="sig-notice">
          {error}{" "}
          {/* A refused snapshot names the one command that refreshes it; anything
              else — like a server process older than the command — has no fix to
              offer, so pressing a guessed button would only hide the reason. */}
          {fix && (
            <button onClick={() => run(fix.command, { limit: 30 })}>
              {fix.label || "Run the pass now"}
            </button>
          )}
        </p>
      )}
      {busy && (
        <p className="sig-busy">
          Running {busy} on this machine — it reads the NSE
          chart host, so a full pass takes minutes. The board keeps showing the
          last saved numbers while it works.
        </p>
      )}

      {doc && (
        <>
          <div className="sig-stats">
            {[
              ["BUY", counts.BUY ?? 0, "rules agree with a long entry", "buy"],
              ["HOLD", counts.HOLD ?? 0, "rules do not line up", "hold"],
              ["SELL", counts.SELL ?? 0, "exit or stay out — never a short", "sell"],
              [
                "Blocked",
                (doc.universe + doc.index.length) - doc.actionable,
                "a veto applies, so no paper entry",
                "blocked",
              ],
            ].map(([label, value, note, tone]) => (
              <div className={`sig-stat sig-tone-${tone}`} key={label}>
                <span>{label}</span>
                <strong>{num(value, 0)}</strong>
                <p>{note}</p>
              </div>
            ))}
          </div>
          <div className="sig-meta">
            <span>
              <i /> Board {clock(doc.as_of)} · {ageText(doc.age_seconds)}
            </span>
            <span>{doc.universe} equities + {doc.index.length} indices</span>
            <span>Entry window {doc.entry_window}</span>
            <span className="sig-paper">
              Paper only · live execution disabled
            </span>
            <span className="sig-source">{doc.source}</span>
          </div>

          <div className="sig-controls">
            <input
              className="sig-search"
              placeholder="Filter by symbol, e.g. RELIANCE or NIFTY"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              aria-label="Filter signals"
            />
            <div className="sig-chips">
              {VERDICTS.map((value) => (
                <button
                  key={value}
                  className={`sig-chip ${verdict === value ? "on" : ""}`}
                  onClick={() => setVerdict(value)}
                >
                  {value === "blocked" ? "Blocked" : value}
                </button>
              ))}
            </div>
            <div className="sig-chips">
              {KINDS.map((value) => (
                <button
                  key={value}
                  className={`sig-chip ${kind === value ? "on" : ""}`}
                  onClick={() => setKind(value)}
                >
                  {value}
                </button>
              ))}
            </div>
            <label className="sig-sort">
              Sort
              <select value={sort} onChange={(e) => setSort(e.target.value)}>
                <option value="score">score</option>
                <option value="adx">ADX</option>
                <option value="rsi">RSI</option>
                <option value="move">today %</option>
                <option value="symbol">symbol</option>
              </select>
            </label>
            <span className="sig-shown">{rows.length} shown</span>
          </div>

          {!!indexes.length && (
            <div className="sig-index">
              <label className="sig-index-pick">
                Nifty index
                <select
                  value={index}
                  aria-label="Nifty index"
                  onChange={(e) => {
                    setIndex(e.target.value);
                    setMembers(null);
                    setMemberNote("");
                    setRestrict(false);
                  }}
                >
                  <option value="">pick an index…</option>
                  {indexes.map((row) => (
                    <option key={row.label} value={row.label}>
                      {row.label}
                      {row.published ? "" : " — NSE publishes no list"}
                    </option>
                  ))}
                </select>
              </label>
              <button
                className="sig-primary"
                disabled={!index || !!busy}
                onClick={findStocks}
                title="Read the constituent file NSE itself publishes for this index"
              >
                {busy === "universe " + index
                  ? "Reading NSE’s list…"
                  : "Find these stocks"}
              </button>
              <button
                className="sig-secondary"
                disabled={!index || !!busy}
                onClick={scanIndex}
                title="Run the daily rules on exactly these stocks. Takes minutes and rewrites the board."
              >
                {busy === "scan index " + index
                  ? "Scanning this index…"
                  : "Scan these stocks for verdicts"}
              </button>
              {!!members?.rows?.length && (
                <label className="sig-index-only">
                  <input
                    type="checkbox"
                    checked={restrict}
                    onChange={(e) => setRestrict(e.target.checked)}
                  />
                  Show only these on the board
                </label>
              )}
            </div>
          )}
          {memberNote && <p className="sig-note">{memberNote}</p>}
          {members && members.status !== "OK" && (
            <p className="sig-note">
              {members.note} {indexes.filter((r) => r.published).length} of the{" "}
              {indexes.length} indices on this picker do have a published file.
            </p>
          )}
          {members?.status === "OK" && (
            <div className="sig-members">
              <p className="sig-note">
                <b>{members.index}</b> · {members.count} stocks ·{" "}
                {members.on_desk} of them already on the desk’s own scan lists ·
                list read {clock(members.as_of)} from{" "}
                <a href={members.source} target="_blank" rel="noreferrer">
                  NSE
                </a>
                . Membership only — that file holds no price, so the verdict and
                numbers below come from the desk scan, not from it. The board
                keeps the 30 best-scoring rows, so a member can still have no
                verdict until this index is scanned.
              </p>
              <div className="sig-members-scroll">
                <table className="sig-table">
                  <thead>
                    <tr>
                      <th>Symbol</th>
                      <th>Company</th>
                      <th>Sector</th>
                      <th>Series</th>
                      <th>On desk lists</th>
                      <th>Board verdict</th>
                      <th>Today</th>
                    </tr>
                  </thead>
                  <tbody>
                    {members.rows.map((row) => {
                      const live = bySymbol[row.symbol] || bySymbol[row.bare];
                      return (
                        <tr key={row.symbol}>
                          <td>
                            <b>{row.symbol}</b>
                          </td>
                          <td className="sig-cell-text">{row.name}</td>
                          <td className="sig-cell-text">{row.industry}</td>
                          <td>{row.series}</td>
                          <td>{row.on_desk ? "yes" : "—"}</td>
                          <td>
                            {live ? (
                              <VerdictChip
                                verdict={live.verdict}
                                actionable={live.actionable}
                              />
                            ) : (
                              <span
                                className="sig-cell-none"
                                title="The board keeps the 30 highest-scoring rows, so this stock has no verdict in it right now. Press Scan these stocks for verdicts to put this index in the board."
                              >
                                not on the board
                              </span>
                            )}
                          </td>
                          <td
                            className={`sig-num ${
                              live?.indicators?.change_pct >= 0
                                ? "sig-plus"
                                : "sig-minus"
                            }`}
                          >
                            {live ? signed(live.indicators.change_pct) + "%" : "—"}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {!rows.length ? (
            <p className="sig-empty">No row matches this filter.</p>
          ) : (
            <table className="sig-table">
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Verdict</th>
                  <th title="sum of the rule scores">Score</th>
                  <th>Close</th>
                  <th title="average directional movement, Wilder 14">ADX</th>
                  <th>+DI</th>
                  <th>-DI</th>
                  <th>RSI</th>
                  <th title="ATR as a share of price">ATR%</th>
                  <th>MACDh</th>
                  <th title="volume against the 20-session average">Vol×</th>
                  <th title="annualized realized volatility">RV%</th>
                  <th>Today</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <SignalRow
                    key={row.kind + row.symbol}
                    row={row}
                    expanded={open === row.symbol}
                    onToggle={() =>
                      setOpen(open === row.symbol ? "" : row.symbol)
                    }
                    onResearch={onResearch}
                    onDecision={() => {
                      setDrawer(true);
                      setOutput(null);
                      setChain({ command: "setup", symbol: row.symbol });
                      run("setup", { symbol: row.symbol });
                    }}
                    onWatch={() => {
                      setDrawer(true);
                      setOutput(null);
                      setChain({ command: "watch", symbol: row.symbol });
                      run("watch", { symbol: row.symbol });
                    }}
                    onTeam={() => {
                      setDrawer(true);
                      setOutput(null);
                      // Seal *and* review: a bare `team` seal leaves the room at
                      // 0/10 PENDING, which reads like the desk froze.
                      run("team review", { symbol: row.symbol });
                    }}
                    busy={busy}
                  />
                ))}
              </tbody>
            </table>
          )}

          <OptionTape
            options={doc.options || []}
            busy={busy}
            onRun={(symbol) =>
              run("options", { symbol })
            }
          />

          <div className="sig-grid sig-two">
            <Intraday doc={doc} />
            <VixBlock vix={doc.vix || {}} />
          </div>

          <NewsBlock news={news} onRun={() => run("news", {})} />

          {doc.failures?.length > 0 && (
            <details className="sig-failures">
              <summary>
                {doc.failures.length} symbol(s) the desk could not read
              </summary>
              {doc.failures.map((row) => (
                <p key={row.symbol}>
                  <b>{row.symbol}</b> — {row.error}
                </p>
              ))}
            </details>
          )}
          <ul className="sig-notes">
            {doc.notes.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        </>
      )}

      {drawer && (
        <DecisionDrawer
          output={output}
          busy={busy}
          onClose={() => setDrawer(false)}
          onRun={(command) => run(command, {})}
          onRetry={(risk) =>
            run(chain.command || "setup", { symbol: chain.symbol, risk })
          }
          onWatch={() => {
            setChain({ command: "watch", symbol: chain.symbol });
            run("watch", { symbol: chain.symbol });
          }}
          onReply={(command, reply) => run(command, { reply })}
        />
      )}
    </section>
  );
}

function VerdictChip({ verdict, actionable }) {
  const tone = !actionable
    ? "blocked"
    : verdict === "BUY"
      ? "buy"
      : verdict === "SELL"
        ? "sell"
        : "hold";
  return <span className={`sig-verdict sig-tone-${tone}`}>{verdict}</span>;
}

function SignalRow({
  row,
  expanded,
  onToggle,
  onResearch,
  onDecision,
  onWatch,
  onTeam,
  busy,
}) {
  const i = row.indicators || {};
  return (
    <>
      <tr className="sig-row" onClick={onToggle}>
        <td>
          <b>{row.symbol}</b>
          <em>{row.kind}</em>
        </td>
        <td>
          <VerdictChip verdict={row.verdict} actionable={row.actionable} />
        </td>
        <td className="sig-num">{signed(row.score, 0)}</td>
        <td className="sig-num">{num(i.close)}</td>
        <td className="sig-num">{num(i.adx, 1)}</td>
        <td className="sig-num sig-tone-buy">{num(i.di_plus, 1)}</td>
        <td className="sig-num sig-tone-sell">{num(i.di_minus, 1)}</td>
        <td className="sig-num">{num(i.rsi_14, 1)}</td>
        <td className="sig-num">{num(i.atr_pct)}</td>
        <td className="sig-num">{signed(i.macd?.histogram)}</td>
        <td className="sig-num">{num(i.volume_x)}×</td>
        <td className="sig-num">{num(row.realized_vol_pct, 1)}</td>
        <td className={`sig-num ${i.change_pct >= 0 ? "sig-plus" : "sig-minus"}`}>
          {signed(i.change_pct)}%
        </td>
        <td className="sig-caret">{expanded ? "▾" : "▸"}</td>
      </tr>
      {expanded && (
        <tr className="sig-detail" onClick={onToggle}>
          <td colSpan={14}>
            <div className="sig-detail-grid">
              <div>
                <h4>Why this verdict</h4>
                <ul>
                  {row.reasons.map((reason) => (
                    <li key={reason}>{reason}</li>
                  ))}
                </ul>
                {row.vetoes.length > 0 && (
                  <ul className="sig-vetoes">
                    {row.vetoes.map((veto) => (
                      <li key={veto}>! {veto}</li>
                    ))}
                  </ul>
                )}
              </div>
              <div>
                <h4>Levels and context</h4>
                <p className="sig-levels">
                  20-bar range {num(i.low_20)} – {num(i.high_20)} · EMA20{" "}
                  {num(i.ema20)} · EMA50 {num(i.ema50)} · ATR {num(i.atr_14)}
                </p>
                <p className="sig-levels">
                  5-session move {signed(row.indicators.run_5_pct)}% · volume
                  against 20-session average {num(i.volume_x)}× on the last
                  completed bar
                  {i.volume_spike ? " (spike ≥1.5×)" : ""}
                </p>
                {row.setups?.length > 0 && (
                  <p className="sig-levels">
                    Saved setup match: {row.setups.join(", ")} (scan{" "}
                    {clock(row.scan_as_of)})
                  </p>
                )}
                {row.bar_forming && (
                  <p className="sig-forming">
                    The {clock(row.bar_as_of)} daily bar is still forming, so the
                    close and today’s volume are not final.
                  </p>
                )}
                <div className="sig-row-actions">
                  <button
                    className="sig-secondary"
                    disabled={!!busy}
                    onClick={(e) => {
                      e.stopPropagation();
                      onDecision();
                    }}
                  >
                    Run the paper chain on {row.symbol}
                  </button>
                  {row.kind === "equity" && (
                    <button
                      className="sig-secondary"
                      disabled={!!busy}
                      onClick={(e) => {
                        e.stopPropagation();
                        onWatch();
                      }}
                    >
                      Watch the next bar and run the chain on {row.symbol}
                    </button>
                  )}
                  <button
                    className="sig-link"
                    onClick={(e) => {
                      e.stopPropagation();
                      onResearch("", row.symbol);
                    }}
                  >
                    Open research record
                  </button>
                  {row.kind === "equity" && (
                    <button
                      className="sig-link"
                      disabled={!!busy}
                      onClick={(e) => {
                        e.stopPropagation();
                        onTeam();
                      }}
                    >
                      Seal a 10-specialist evidence session
                    </button>
                  )}
                </div>
                <p className="sig-hint">
                  The chain sizes the position, checks the risk limits and prints
                  a ticket. Nothing is booked until you type your own approval.
                  <b> Run the paper chain</b> judges the bar that has already
                  closed. <b>Watch the next bar</b> polls for five minutes and runs
                  the chain on the bar that breaks, which is what the risk stage
                  wants: it only accepts a signal inside one bar step of its
                  close. Start the watch on a row that is still waiting — on a
                  symbol that has already broken, both buttons inherit that same
                  old bar and the desk will say so.
                </p>
              </div>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

function OptionTape({ options, busy, onRun }) {
  if (!options.length) return null;
  return (
    <div className="sig-options">
      <h3>
        Expiry tape <span>index options · open interest, not a trade call</span>
      </h3>
      <div className="sig-grid">
        {options.map((row) => (
          <article className="sig-card" key={row.symbol + row.expiry}>
            <header>
              <b>{row.symbol}</b>
              <VerdictChip verdict={row.verdict} actionable={row.actionable} />
              <span>{row.expiry}</span>
            </header>
            <div className="sig-card-grid">
              {[
                ["Underlying", num(row.underlying)],
                ["ATM strike", num(row.atm_strike, 0)],
                ["ATM straddle", num(row.atm_straddle)],
                [
                  "Expected move",
                  Number.isFinite(row.expected_move_pct)
                    ? `${row.expected_move_pct}% by ${row.expiry}`
                    : "—",
                ],
                ["PCR (OI)", num(row.pcr_oi)],
                [
                  "OI change",
                  `calls ${signed(row.call_oi_change, 0)} · puts ${signed(row.put_oi_change, 0)}`,
                ],
                ["Call wall", num(row.resistance_oi, 0)],
                ["Put wall", num(row.support_oi, 0)],
                ["ATM IV", `${num(row.atm_iv_call)}/${num(row.atm_iv_put)}`],
                [
                  "Index ADX / +DI / -DI",
                  `${num(row.index_indicators?.adx, 1)} / ${num(row.index_indicators?.di_plus, 1)} / ${num(row.index_indicators?.di_minus, 1)}`,
                ],
              ].map(([label, value]) => (
                <p key={label}>
                  <span>{label}</span>
                  <b>{value}</b>
                </p>
              ))}
            </div>
            <ul className="sig-reasons">
              {row.reasons.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
            {row.vetoes.map((veto) => (
              <p className="sig-veto" key={veto}>
                ! {veto}
              </p>
            ))}
            <footer>
              <span>Chain {clock(row.chain_as_of)}</span>
              <button
                className="sig-secondary"
                disabled={!!busy}
                onClick={() => onRun(row.symbol)}
              >
                Refresh chain
              </button>
            </footer>
          </article>
        ))}
      </div>
    </div>
  );
}

function Intraday({ doc }) {
  const orb = doc.orb_context || {};
  const indices = orb.indices || [];
  if (!indices.length)
    return (
      <section className="sig-block">
        <h3>Intraday context</h3>
        <p className="sig-muted">
          No saved index board — press <b>Benchmark indices</b> above for the
          opening range, futures premium and breadth.
        </p>
      </section>
    );
  const breadth = orb.breadth || {};
  const futures = orb.futures || {};
  return (
    <section className="sig-block">
      <h3>
        Intraday context <span>5-minute bars, {clock(orb.as_of)}</span>
      </h3>
      <table className="sig-mini">
        <thead>
          <tr>
            <th>Index</th>
            <th>Last</th>
            <th>vs ORB</th>
            <th>ORB high</th>
            <th>ORB low</th>
            <th>Gap%</th>
          </tr>
        </thead>
        <tbody>
          {indices.map((row) => (
            <tr key={row.symbol}>
              <td>{row.symbol}</td>
              <td>{num(row.last)}</td>
              <td className={`sig-state sig-state-${row.state}`}>{row.state}</td>
              <td>{num(row.orb?.high)}</td>
              <td>{num(row.orb?.low)}</td>
              <td>{signed(row.gap_pct)}%</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="sig-levels">
        {futures.symbol
          ? `Futures ${futures.symbol} ${num(futures.last)} vs spot ${num(futures.spot_last)} (${signed(futures.premium_points)} pts, ${signed(futures.premium_pct, 3)}%)`
          : "No saved futures read."}
      </p>
      <p className="sig-levels">
        Breadth {clock(breadth.as_of)}: {num(breadth.above_ma20, 0)} of{" "}
        {num(breadth.symbols, 0)} above MA20 ({num(breadth.above_ma20_pct, 1)}%),{" "}
        {num(breadth.above_20d_high, 0)} at a 20-day high, median RSI{" "}
        {num(breadth.median_rsi, 1)}.
      </p>
    </section>
  );
}

function VixBlock({ vix }) {
  return (
    <section className="sig-block">
      <h3>
        India VIX <span>deliberately not published</span>
      </h3>
      <p className="sig-vix">{vix.reason}</p>
      <ul className="sig-reasons">
        {(vix.substitutes || []).map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
      <p className="sig-muted">
        Every row above carries its own realized volatility, and the expiry tape
        carries the move option prices are actually charging.
      </p>
    </section>
  );
}

function NewsBlock({ news, onRun }) {
  const feed = news?.headlines || {};
  const items = [...(feed.items || []), ...(feed.market || [])].sort(
    (a, b) => new Date(b.published_at) - new Date(a.published_at),
  );
  const moves = news?.moves || [];
  return (
    <section className="sig-block sig-news">
      <h3>
        News &amp; catalysts{" "}
        <span>
          {news
            ? `${clock(news.as_of)} · ${feed.provider || "vendor feed only"}`
            : "no saved news run"}
        </span>
        <button className="sig-link" onClick={onRun}>
          Refresh
        </button>
      </h3>
      {!items.length ? (
        <p className="sig-muted">
          <button className="sig-link" onClick={onRun}>
            Collect dated headlines
          </button>{" "}
          for your saved universe; the desk keeps each one with its own
          publication stamp.
        </p>
      ) : (
        <ul className="sig-headlines">
          {items.slice(0, 12).map((row) => (
            <li key={row.link || row.title}>
              <span className="sig-when">{stamp(row.published_at)}</span>
              <b className="sig-who">{row.symbol || "market"}</b>
              <a href={row.link} target="_blank" rel="noreferrer">
                {row.title}
              </a>
              <em>{row.publisher}</em>
            </li>
          ))}
        </ul>
      )}
      {!!feed.errors?.length && (
        <p className="sig-veto">
          {feed.errors.length} feed(s) did not answer:{" "}
          {feed.errors.map((row) => row.query + " (" + row.error + ")").join(", ")}
        </p>
      )}
      {!!moves.length && (
        <p className="sig-levels">
          Completed-candle moves:{" "}
          {moves
            .slice(0, 6)
            .map((row) => `${row.symbol} ${signed(row.pct)}%`)
            .join(" · ")}
        </p>
      )}
      {(feed.notes || []).slice(0, 1).map((note) => (
        <p className="sig-muted" key={note}>
          {note}
        </p>
      ))}
    </section>
  );
}

function DecisionDrawer({ output, busy, onClose, onReply, onRetry, onWatch, onRun }) {
  const [reply, setReply] = useState("");
  // The engine only accepts an approval for a ticket it printed as READY, so the
  // consent box stays closed unless that exact line is in the real stdout.
  const ready = /READY:|waiting for your yes/i.test(output?.output || "");
  return (
    <div className="sig-drawer" role="dialog" aria-label="Desk command output">
      <div className="sig-drawer-head">
        <h3>
          Desk output
          <span>real stdout from the desk process — nothing simulated</span>
        </h3>
        <button className="sig-close" onClick={onClose} aria-label="Close">
          ✕
        </button>
      </div>
      <div className="sig-drawer-run">
        {[
          ["ticket", "Pending tickets"],
          ["team report", "Team roll-up"],
          ["journal", "Paper journal"],
          ["envelope", "Risk envelope"],
          ["status", "Desk status"],
        ].map(([command, label]) => (
          <button
            className="sig-secondary"
            disabled={!!busy}
            key={command}
            onClick={() => onRun(command)}
          >
            {label}
          </button>
        ))}
      </div>
      {busy ? (
        <p className="sig-busy">
          Running {busy}… this panel waits for the process, it does
          not guess progress.
        </p>
      ) : !output ? (
        <p className="sig-muted">
          Pick a row and press <b>Run the paper chain</b>. It sizes the position
          from your ₹1,00,000 capital and ₹500 risk cap, checks the limits and
          prints a ticket. <b>Seal a 10-specialist evidence session</b> freezes
          one symbol&apos;s candles and every stage verdict into an immutable
          team directory that the Observatory then reads; it starts no agents
          and clears no trade.
        </p>
      ) : (
        <>
          <p className="sig-runmeta">
            The desk ran its {output.command} pass · exit{" "}
            {output.returncode} · {output.seconds}s
          </p>
          <pre className="sig-out">{output.output}</pre>
          {["setup", "watch"].includes(output.command) &&
            output.risk_cap > 0 &&
            /exceeds per-trade limit/i.test(output.output || "") && (
              <RiskBudget
                busy={busy}
                cap={output.risk_cap}
                used={output.risk_used}
                onRun={onRetry}
              />
            )}
          {["setup", "watch"].includes(output.command) &&
            /No triggered entry/i.test(output.output || "") && (
              <div className="sig-risk">
                <h4>No completed bar has broken the range yet</h4>
                <p>
                  This symbol is still sitting under its opening-range high, so
                  there is nothing to size and the desk refuses to invent a
                  signal. Watching is what this row is for: it polls every 20
                  seconds for five minutes and runs the whole chain on the bar
                  that breaks, inside the window the risk stage allows.
                </p>
                <div className="sig-risk-row">
                  <button
                    className="sig-primary"
                    disabled={!!busy}
                    onClick={onWatch}
                  >
                    Watch the next bar and run the chain
                  </button>
                </div>
              </div>
            )}
          {["setup", "watch"].includes(output.command) &&
            /Entry signal is forming, expired/i.test(output.output || "") && (
              <div className="sig-risk">
                <h4>The break already happened, and the window closed with it</h4>
                <p>
                  The risk stage only accepts a signal within one bar step of its
                  close. The levels above are real, the timing is not, and no
                  re-run changes that: on a symbol that has already broken, a
                  watch pass returns on its very first check and inherits the same
                  old bar. The desk will not dress an old bar up as a fresh
                  signal — watch a row that is still <b>waiting</b> instead, and it
                  will catch that break as it happens.
                </p>
              </div>
            )}
          <div className="sig-consent">
            <h4>Your own reply, typed by you</h4>
            <p>
              Approval is only accepted when the reply starts with{" "}
              <code>yes</code> and names the instrument, exactly as the engine
              requires. The desk never writes this text for you, and a booked
              ticket is a <b>local simulated fill</b> — not a broker
              acknowledgement. Live execution stays disabled.
            </p>
            <input
              value={reply}
              onChange={(e) => setReply(e.target.value)}
              placeholder={'e.g. yes buy RELIANCE'}
              aria-label="Your approval reply"
            />
            <div className="sig-consent-actions">
              <button
                className="sig-primary"
                disabled={!reply.trim() || !!busy || !ready}
                onClick={() => {
                  onReply("approve", reply.trim());
                  setReply("");
                }}
              >
                Approve with this reply
              </button>
              <button
                className="sig-secondary"
                disabled={!reply.trim() || !!busy}
                onClick={() => {
                  onReply("decline", reply.trim());
                  setReply("");
                }}
              >
                Decline
              </button>
            </div>
            {!ready && (
              <p className="sig-muted">
                The output above has no READY ticket, so there is nothing to
                consent to. A BLOCK reason (risk cap, reward:risk below 1, an
                expired or forming entry signal) stops the order before approval.
                A trade that only just overran its loss limit can be re-run with
                the risk-budget control above; a reward:risk refusal is about the
                setup itself, so no budget will clear it.
              </p>
            )}
          </div>
        </>
      )}
    </div>
  );
}

function RiskBudget({ busy, cap, used, onRun }) {
  const suggested = Math.max(1, Math.floor(cap * 0.9));
  const [value, setValue] = useState(used || suggested);
  const amount = Number(value);
  const ok = Number.isFinite(amount) && amount > 0 && amount <= cap;
  return (
    <div className="sig-risk">
      <h4>Risk budget for this run</h4>
      <p>
        The desk sized the position from the stop distance, then the risk stage
        added the costs on top of it — brokerage, STT, exchange turnover and
        slippage. Those costs do not drop away with the last share, which is how a
        trade that looks comfortably inside your ₹{num(cap, 0)} limit still
        breaches it. Re-running the chain with a smaller budget buys fewer shares
        and brings the maximum loss back under the cap. Your configured cap stays a
        ceiling: this field can only go below it, and the ticket still waits for
        your own approval reply before anything is booked.
      </p>
      <div className="sig-risk-row">
        <label>
          ₹ risk per trade
          <input
            type="number"
            min="1"
            max={cap}
            step="1"
            value={value}
            disabled={!!busy}
            onChange={(e) => setValue(e.target.value)}
            aria-label="Risk budget in rupees for this run"
          />
        </label>
        <button
          className="sig-primary"
          disabled={!ok || !!busy}
          onClick={() => onRun(amount)}
        >
          Re-run the paper chain with this budget
        </button>
      </div>
      {!ok && (
        <p className="sig-muted">
          Enter between ₹1 and ₹{num(cap, 0)} — anything higher is refused by the
          desk before it reaches the engine.
        </p>
      )}
    </div>
  );
}
