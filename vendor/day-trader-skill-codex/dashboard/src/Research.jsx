import React, { useEffect, useState } from "react";
import LookupAnswer from "./LookupAnswer";
import { readJSON, runAction } from "./deskapi.js";
import {
  KIND_ORDER,
  groupRoles,
  kindOf,
  kindWords,
  roleNames,
  roomConclusion,
} from "./verdictKinds.js";

const date = (value) =>
  value
    ? new Intl.DateTimeFormat("en-IN", {
        timeZone: "Asia/Kolkata",
        dateStyle: "medium",
        timeStyle: "short",
      }).format(new Date(value)) + " IST"
    : "—";
const safeUrl = (value) => {
  try {
    const u = new URL(value);
    return u.protocol === "https:" ? u.href : null;
  } catch {
    return null;
  }
};
const money = (value) =>
  Number.isFinite(value)
    ? "₹" +
      new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 }).format(value)
    : "—";
async function read(path, signal) {
  const r = await fetch(path, { cache: "no-store", signal });
  const d = await r.json();
  if (!r.ok) throw new Error(d.error || "Research is unavailable.");
  return d;
}
export default function Research({
  target,
  symbol,
  onOpenTeam,
  onRoomChange,
  onPrepare,
  refreshToken,
}) {
  const [list, setList] = useState(null),
    [selected, setSelected] = useState(target || ""),
    [detail, setDetail] = useState(null),
    [error, setError] = useState(""),
    [query, setQuery] = useState(""),
    [answer, setAnswer] = useState(null),
    [answerError, setAnswerError] = useState(""),
    [busy, setBusy] = useState(""),
    [revision, setRevision] = useState(0),
    [raw, setRaw] = useState(null),
    [rooms, setRooms] = useState([]),
    [room, setRoom] = useState(null),
    [roomError, setRoomError] = useState(""),
    [roomRevision, setRoomRevision] = useState(0);
  // Saved desk files answer first, then one live NSE pass adds the detail card.
  // The wait is long because that pass costs a few seconds per new query and the
  // desk caches it for 15 minutes, so a pause between keystrokes is cheap.
  useEffect(() => {
    const text = query.trim();
    if (!text) {
      setAnswer(null);
      setAnswerError("");
      return;
    }
    const controller = new AbortController();
    const timer = setTimeout(() => {
      readJSON("/api/lookup?q=" + encodeURIComponent(text), controller.signal)
        .then((doc) => {
          setAnswer(doc);
          setAnswerError("");
        })
        .catch((e) => {
          if (e.name !== "AbortError") {
            setAnswer(null);
            setAnswerError(e.message);
          }
        });
    }, 1200);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [query, refreshToken]);
  async function collect() {
    setBusy("signal pass");
    try {
      await runAction("signals", { limit: 30 });
      await runAction("news", {});
      setAnswer(await readJSON("/api/lookup?q=" + encodeURIComponent(query.trim())));
      setAnswerError("");
    } catch (e) {
      setAnswerError(e.message);
    } finally {
      setBusy("");
    }
  }
  // One live NSE pass for the typed query, then the report the same query wrote.
  // Both are desk commands run by the server's allowlist -- the browser never
  // builds a command line, and neither one can place an order.
  async function runLive(command, label) {
    const text = query.trim();
    if (!text) {
      setAnswerError("Type a symbol, an index or a strike first.");
      return;
    }
    setBusy(label);
    setRaw(null);
    try {
      const event = await runAction(command, { symbol: text });
      setRaw(event);
      if (command === "detail")
        setAnswer(await read("/api/lookup?q=" + encodeURIComponent(text)));
      setAnswerError("");
    } catch (e) {
      setAnswerError(e.message);
    } finally {
      setBusy("");
    }
  }
  // A next-step control from the answer card. The browser sends only the command
  // name and the symbol; the server matches the name against its frozen allowlist
  // and builds the argv itself, so nothing typed here becomes a shell line.
  async function runStep(step) {
    if (step.search) {
      setQuery(step.search);
      return;
    }
    setBusy(step.label);
    setRaw(null);
    try {
      const event = await runAction(step.command, step.symbol ? { symbol: step.symbol } : {});
      setRaw(event);
      setAnswer(await read("/api/lookup?q=" + encodeURIComponent(query.trim())));
      setAnswerError("");
    } catch (e) {
      setAnswerError(e.message);
    } finally {
      setBusy("");
    }
  }
  // The searched symbol's own sealed rooms, newest first, read straight from the
  // observability API the Observatory uses. Nothing here runs a desk command: a
  // room that does not exist is shown as not existing.
  useEffect(() => {
    const wanted = (answer?.parsed?.symbol || "").toUpperCase();
    if (!wanted || answer?.parsed?.kind !== "equity") {
      setRooms([]);
      setRoom(null);
      return;
    }
    const controller = new AbortController();
    read("/api/sessions", controller.signal)
      .then(async (doc) => {
        const mine = (doc.sessions || [])
          .filter((row) => String(row.symbol || "").toUpperCase() === wanted)
          .sort((x, y) => String(y.as_of).localeCompare(String(x.as_of)));
        if (controller.signal.aborted) return;
        setRooms(mine);
        if (mine.length) onRoomChange?.(mine[0].id);
        setRoom(
          mine.length
            ? await read(
                "/api/sessions/" + encodeURIComponent(mine[0].id),
                controller.signal,
              )
            : null,
        );
        setRoomError("");
      })
      .catch((e) => {
        if (e.name !== "AbortError") {
          setRooms([]);
          setRoom(null);
          setRoomError(e.message);
        }
      });
    return () => controller.abort();
  }, [answer?.parsed?.symbol, roomRevision]);
  async function sealRoom() {
    const wanted = answer?.parsed?.symbol;
    if (!wanted) {
      setRoomError("Type a stock symbol first.");
      return;
    }
    setBusy("sealing the room and running the ten specialists");
    try {
      // One command now seals the evidence AND puts every role's own checker on
      // it, so a searched stock stops sitting at 0/10 PENDING. It records verdicts
      // the engine re-derives at finalize; it never books a ticket -- that still
      // needs your own "yes".
      setRaw(await runAction("team review", { symbol: wanted }));
      setRoomError("");
      setRoomRevision((n) => n + 1);
    } catch (e) {
      setRoomError(e.message);
    } finally {
      setBusy("");
    }
  }
  async function rollRoomUp() {
    setBusy("rolling the recorded reviews up");
    try {
      setRaw(await runAction("team report", {}));
      setRoomRevision((n) => n + 1);
      setRoomError("");
    } catch (e) {
      setRoomError(e.message);
    } finally {
      setBusy("");
    }
  }
  useEffect(() => {
    if (target) setSelected(target);
    setQuery(symbol || "");
  }, [target, symbol]);
  useEffect(() => {
    const controller = new AbortController();
    let busy = false;
    async function refresh() {
      if (busy) return;
      busy = true;
      try {
        const d = await read("/api/research", controller.signal);
        setList(d);
        setRevision((n) => n + 1);
        setSelected(
          (id) =>
            target ||
            id ||
            d.items.find((r) => !symbol || r.symbol === symbol)?.id ||
            "",
        );
        setError("");
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
  useEffect(() => {
    if (!selected) return;
    const controller = new AbortController();
    setDetail(null);
    read("/api/research/" + encodeURIComponent(selected), controller.signal)
      .then(setDetail)
      .catch((e) => {
        if (e.name !== "AbortError") setError(e.message);
      });
    return () => controller.abort();
  }, [selected, revision]);
  const items = (list?.items || []).filter((r) =>
    (r.symbol + " " + r.name + " " + r.label)
      .toLowerCase()
      .includes(query.toLowerCase()),
  );
  // The record pane holds whatever was opened last, which reads like a search
  // result even while the list beside it says nothing matched. So the page names
  // the mismatch instead of letting two panels contradict each other.
  const searched = query.trim();
  const unmatched =
    !!searched &&
    !!detail &&
    !(detail.symbol + " " + (detail.name || "") + " " + (detail.label || ""))
      .toLowerCase()
      .includes(searched.toLowerCase());
  const reviewTarget =
    answer?.parsed?.kind === "equity" ? answer.parsed.symbol : "";
  const noMatchHint = reviewTarget
    ? " Press Seal and run the 10 specialists to create one for " +
      reviewTarget +
      "."
    : " The desk only lists reviews it has actually written, and it does not know " +
      searched +
      " as a stock, index or strike.";
  return (
    <section className="research-page">
      <div className="page-title">
        <div>
          <span className="eyebrow">ONE RECORD FOR EVERY REVIEW STAGE</span>
          <h1>
            Individual research<span>.</span>
          </h1>
          <p>
            Type any stock, index or option strike: the desk answers from the
            files it already wrote — verdict, chain, ledger, headlines — and one
            live NSE pass adds bars, indicators, the exact strike with its lot
            and tick, and a written report you can open.
          </p>
        </div>
        <button className="op-secondary" onClick={() => onPrepare()}>
          ＋ Prepare a review
        </button>
      </div>
      {error && (
        <p className="notice error" role="alert">
          {error}
        </p>
      )}
      {!!list?.rejected && (
        <p className="notice">
          {list.rejected} saved assessment(s) failed their source or identity
          binding and are excluded.
        </p>
      )}
      <LookupAnswer
        doc={answer}
        error={answerError}
        busy={busy}
        onRefresh={collect}
        onDetail={() => runLive("detail", "live NSE detail")}
        onReport={() => runLive("symbol report", "symbol report")}
        onSearch={(text) => setQuery(text)}
        onStep={runStep}
        raw={raw}
      />
      <TeamDecision
        symbol={reviewTarget}
        room={room}
        rooms={rooms}
        busy={busy}
        error={roomError}
        onSeal={sealRoom}
        onRollUp={rollRoomUp}
        onOpen={(id) => onOpenTeam(id)}
      />
      <div className="research-layout">
        <aside className="panel research-directory">
          <label>
            Search a stock, index or strike
            <input
              aria-label="Search individual research"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="RELIANCE · NIFTY 50 · NIFTY 23900 CE · BANKNIFTY 55800 PUT…"
            />
          </label>
          {items.map((r) => (
            <button
              key={r.id}
              className={selected === r.id ? "selected" : ""}
              onClick={() => {
                setSelected(r.id);
                setDetail(null);
              }}
            >
              <span>
                <b>{r.symbol}</b>
                <small>
                  {r.kind === "ASSESSMENT"
                    ? "PREREQUISITES"
                    : "INDEPENDENT TEAM"}
                </small>
              </span>
              {r.name !== r.symbol && <p>{r.name}</p>}
              <p>{date(r.as_of)}</p>
              <em>{r.label}</em>
            </button>
          ))}
          {!items.length && (
            <div className="empty small">
              {list
                ? searched
                  ? "Nothing saved matches “" + searched + "”." + noMatchHint
                  : "Type a stock, index or strike to filter the saved records."
                : "Reading saved research…"}
            </div>
          )}
        </aside>
        <div className="research-content">
          {unmatched && (
            <p className="notice">
              The record below is the <b>last one opened</b> —{" "}
              <b>{detail.symbol}</b>, {date(detail.as_of)} — not a result for
              “{searched}” and not a review of it.{noMatchHint}
            </p>
          )}
          {detail ? (
            <>
              <section className="panel research-summary">
                <span className="eyebrow">{detail.label.toUpperCase()}</span>
                <div>
                  <h2>{detail.symbol}</h2>
                  <span className="decision-pill caution">
                    {detail.status.replaceAll("_", " ")}
                  </span>
                </div>
                <p>Decision cutoff: {date(detail.as_of)}</p>
                {detail.source_scan_cutoff && (
                  <p>
                    Original daily source cutoff:{" "}
                    {date(detail.source_scan_cutoff)}
                  </p>
                )}
                {detail.name !== detail.symbol && <p>{detail.name}</p>}
                <p className="research-boundary">
                  {detail.kind === "ASSESSMENT"
                    ? "These are coordinator prerequisite findings. Independent specialists have not issued clearance for this assessment."
                    : "These decisions belong to this saved snapshot. A current trade requires fresh evidence and a new exact-order risk check."}
                </p>
                <div className="research-actions">
                  {detail.session_id && (
                    <button
                      className="op-secondary"
                      onClick={() => onOpenTeam(detail.session_id)}
                    >
                      Open specialist desks ↗
                    </button>
                  )}
                  <button
                    className="op-secondary"
                    onClick={() =>
                      onPrepare({
                        symbol: detail.symbol,
                        as_of: detail.as_of,
                        origin_cutoff: detail.source_scan_cutoff,
                      })
                    }
                  >
                    Prepare follow-up review ↗
                  </button>
                </div>
              </section>
              {detail.integrity_errors?.map((e, i) => (
                <p className="notice error" key={i}>
                  {e}
                </p>
              ))}
              {detail.evidence_status === "UNAVAILABLE" && (
                <p className="notice">
                  Setup evidence is unavailable for this prerequisite note. No
                  daily setup or independent clearance is confirmed.
                </p>
              )}
              {detail.proposal && (
                <section className="panel research-evidence">
                  <span className="eyebrow">
                    ARCHIVED NUMERICAL PLAN · SAVED CUTOFF ONLY
                  </span>
                  <p>
                    This draft retains the sealed engine's levels and cost
                    assumptions. Independent decisions above determine its saved
                    readiness; a new order requires a fresh check.
                  </p>
                  <div className="execution-metrics">
                    {[
                      ["Entry", money(detail.proposal.entry)],
                      ["Stop", money(detail.proposal.stop)],
                      ["Target", money(detail.proposal.target)],
                      ["Shares", detail.proposal.qty],
                      [
                        "Estimated max loss",
                        money(detail.proposal.max_loss_estimate),
                      ],
                      [
                        "Net reward / risk",
                        Number.isFinite(detail.proposal.net_reward_risk)
                          ? detail.proposal.net_reward_risk.toFixed(2)
                          : "—",
                      ],
                    ].map(([label, value]) => (
                      <div key={label}>
                        <span>{label}</span>
                        <b>{value}</b>
                      </div>
                    ))}
                  </div>
                </section>
              )}
              {detail.tick && (
                <section className="panel research-evidence">
                  <span className="eyebrow">
                    DATED TICK EVIDENCE AT SAVED CUTOFF
                  </span>
                  <h3>
                    {detail.tick.status === "VERIFIED"
                      ? `₹${detail.tick.supplied} · verified dated record`
                      : "Tick evidence needs verification"}
                  </h3>
                  <p>
                    {detail.tick.published_at
                      ? `Published / snapshot available: ${date(detail.tick.published_at)}.`
                      : ""}{" "}
                    Later sessions require applicable metadata.
                  </p>
                  {safeUrl(detail.tick.source) && (
                    <a
                      href={safeUrl(detail.tick.source)}
                      target="_blank"
                      rel="noreferrer"
                    >
                      Open recorded source ↗
                    </a>
                  )}
                </section>
              )}
              <section className="research-findings">
                <div className="section-title">
                  <h2>Decisions and prerequisites</h2>
                  <span className="micro">
                    {detail.kind === "ASSESSMENT"
                      ? "COORDINATOR FINDINGS"
                      : "VALIDATED SAVED REVIEWS"}
                  </span>
                </div>
                {detail.findings.map((r, i) => (
                  <article className="panel" key={r.role + "-" + i}>
                    <div>
                      <h3>{r.role.replaceAll("_", " ")}</h3>
                      <span>
                        {String(r.status || "PENDING").replaceAll("_", " ")}
                      </span>
                    </div>
                    <p>{r.decision}</p>
                  </article>
                ))}
              </section>
              {detail.sources?.length > 0 && (
                <section className="panel research-evidence">
                  <span className="eyebrow">RECORDED ORIGINAL SOURCES</span>
                  {detail.sources
                    .filter((s) => safeUrl(s.url))
                    .map((s, i) => (
                      <a
                        href={safeUrl(s.url)}
                        target="_blank"
                        rel="noreferrer"
                        key={i}
                      >
                        {new URL(s.url).hostname} ↗
                        <small>
                          Published {s.published_date || date(s.published_at)} ·{" "}
                          {s.publication_timezone || "See recorded timestamp"}
                        </small>
                      </a>
                    ))}
                </section>
              )}
            </>
          ) : (
            <section className="panel empty">
              <h2>
                {selected
                  ? "Reading the saved findings…"
                  : "Research begins with evidence."}
              </h2>
              <p>Select a saved record or prepare a review for a company.</p>
            </section>
          )}
        </div>
      </div>
    </section>
  );
}

// The searched stock's own team room, read from the same observability API the
// Observatory uses. Every number here is what the engine validated: a room with
// no reviews says so, because the desk must never write a specialist verdict.
const VERDICT_TONE = {
  PASS: "green",
  WARN: "amber",
  FAIL: "red",
  VETO: "red",
  // BLOCK is a reviewer veto, not a finished step. Without this row the badge
  // fell through to the phase tone below and printed a veto in PASS green.
  BLOCK: "red",
  // The same value has to read the same colour in every panel, so these follow
  // main.jsx's tone(): an evidence gap is amber, not a silent grey.
  UNAVAILABLE: "amber",
  NOT_APPLICABLE: "muted",
  BLOCKED: "red",
  REVIEW_INCOMPLETE: "amber",
  PENDING_AGENT_REVIEWS: "amber",
  READY_PAPER_TICKET: "green",
  AWAITING_FINALIZATION: "amber",
  INTEGRITY_ERROR: "red",
  completed: "green",
  running: "amber",
  dispatched: "amber",
  queued: "muted",
  failed: "red",
  invalid: "red",
};

function TeamDecision({ symbol, room, rooms, busy, error, onSeal, onRollUp, onOpen }) {
  if (!symbol) return null;
  const roles = room?.roles || [];
  const report = room?.report || {};
  const done = roles.filter((row) => row.phase === "completed").length;
  const groups = groupRoles(roles);
  const ticket = report.paper_ticket;
  const label = (row) => roleNames[row.role] || row.role;
  const bucketLines = KIND_ORDER.filter((k) => (groups[k] || []).length).map(
    (k) => `${kindWords[k].head}: ${groups[k].map(label).join(", ")}`
  );
  return (
    <section className="lk-panel">
      <header className="lk-head">
        <div>
          <span className="sig-eyebrow">THE SPECIALIST TEAM · {symbol}</span>
          <h3>
            {room ? (
              <>
                <span className={`badge ${VERDICT_TONE[room.status] || "muted"}`}>
                  {room.status}
                </span>{" "}
                <span className="lk-kind">
                  {done} of {roles.length || 10} independent reviews ·{" "}
                  {rooms.length} sealed room{rooms.length === 1 ? "" : "s"}
                </span>
              </>
            ) : (
              <>
                No sealed room yet{" "}
                <span className="lk-kind">
                  press the button to seal {symbol} and put all ten specialists on it
                </span>
              </>
            )}
          </h3>
        </div>
        <div className="lk-head-right">
          <button className="sig-primary" disabled={!!busy} onClick={onSeal}>
            {room
              ? "Re-seal and run the 10 specialists"
              : "Seal and run the 10 specialists"}
          </button>
          {room && (
            <>
              <button className="sig-secondary" disabled={!!busy} onClick={onRollUp}>
                Roll up what is recorded
              </button>
              <button className="sig-secondary" onClick={() => onOpen(room.id)}>
                Open in Observatory
              </button>
            </>
          )}
        </div>
      </header>
      {error && <p className="lk-error">{error}</p>}
      {room && roles.length > 0 && (
        <>
          <p className="team-conclusion">{roomConclusion(roles, ticket, label)}</p>
          <ol className="team-roles">
            {roles.map((row) => {
              const kind = kindOf(row);
              return (
                <li key={row.role}>
                  <span
                    className={`badge ${
                      VERDICT_TONE[row.review?.verdict] ||
                      VERDICT_TONE[row.phase] ||
                      "muted"
                    }`}
                  >
                    {row.review?.verdict || row.phase}
                  </span>
                  <b>{label(row)}</b>
                  <span className={`kind-chip kind-${kind}`}>
                    {kindWords[kind].short}
                  </span>
                  <p>
                    {row.review?.summary ||
                      "No review recorded for this role yet — it is missing, not silent by choice."}
                  </p>
                  {row.agent_id && <small>agent {row.agent_id}</small>}
                </li>
              );
            })}
          </ol>
        </>
      )}
      <p className="sig-muted">
        {room
          ? `Sealed ${date(room.context?.settings?.as_of)} · ${done} of ${
              roles.length || 10
            } reviews recorded · room status ${room.status}. ${
              bucketLines.length ? bucketLines.join("; ") + ". " : ""
            }${
              ticket
                ? `Paper ticket ${ticket.side || ""} ${ticket.symbol || ""} ${
                    ticket.qty ?? ""
                  } — it stays unbooked until you type your own reply.`
                : "No paper ticket was released."
            } Distinct recorded agent ids: ${
              report.orchestration?.distinct_recorded_agent_ids ?? "—"
            } of ${report.orchestration?.required_agents ?? 10}.`
          : `Sealing freezes ${symbol}'s completed candles, the saved indicators, the risk limits and the portfolio snapshot into an immutable room, then the ten roles are dispatched and each records its own verdict. The roll-up is the combined decision — the desk starts no agent and invents no review.`}
      </p>
    </section>
  );
}
