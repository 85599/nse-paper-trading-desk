import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import "./style.css";
import "./desks.css";
import AgentDesk from "./AgentDesk";
import AgentDialog from "./AgentDialog";
import Opportunities from "./Opportunities";
import "./theme.css";
import "./workflow.css";
import RequestDialog from "./RequestDialog";
import Research from "./Research";
import Journal from "./Journal";
import { runAction } from "./deskapi.js";
import {
  KIND_ORDER,
  groupRoles,
  kindOf,
  kindWords,
  roomConclusion,
  roleNames as names,
} from "./verdictKinds.js";

const descriptions = {
  scanner: "Coverage · indicators · tick",
  news: "Original announcements",
  analyst: "Financial statements",
  entry: "Completed-bar confirmation",
  exit: "Stops · targets · reward/risk",
  timemachine: "Historical replay · scenarios",
  budget: "Capital · costs · exposure",
  risk: "Independent veto authority",
  broker: "Paper tickets only",
  alerts: "Order-bound definitions",
};
// What a role's phase means in plain words, for a room nobody has finished.
const phaseWords = {
  queued: "not dispatched yet",
  dispatched: "dispatched, no review recorded",
  running: "reviewing now",
  completed: "recorded",
  invalid: "review rejected by the engine",
  failed: "run failed",
};
const money = (n) =>
  Number.isFinite(n)
    ? new Intl.NumberFormat("en-IN", {
        style: "currency",
        currency: "INR",
        maximumFractionDigits: 2,
      }).format(n)
    : "—";
const number = (n) => (Number.isFinite(n) ? n.toFixed(2) : "—");
const time = (s) =>
  s && Number.isFinite(new Date(s).getTime())
    ? new Intl.DateTimeFormat("en-IN", {
        timeZone: "Asia/Kolkata",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false,
      }).format(new Date(s))
    : "—";
const date = (s) =>
  s && Number.isFinite(new Date(s).getTime())
    ? new Intl.DateTimeFormat("en-IN", {
        timeZone: "Asia/Kolkata",
        day: "2-digit",
        month: "short",
        year: "numeric",
      }).format(new Date(s))
    : "—";
const tone = (v) =>
  ["BLOCK", "BLOCKED", "failed", "invalid", "INTEGRITY_ERROR", "UNREADABLE"].includes(v)
    ? "red"
    : ["WARN", "UNAVAILABLE", "REVIEW_INCOMPLETE", "AWAITING_FINALIZATION",
       "PENDING_AGENT_REVIEWS"].includes(v)
      ? "amber"
      : ["PASS", "READY_PAPER_TICKET", "completed"].includes(v)
        ? "green"
        : "muted";
function Badge({ value }) {
  return (
    <span className={`badge ${tone(value)}`}>
      <i />
      {String(value || "PENDING").replaceAll("_", " ")}
    </span>
  );
}
async function api(path) {
  const r = await fetch(path, { cache: "no-store" });
  const d = await r.json();
  if (!r.ok) throw new Error(d.error || "Connection failed");
  return d;
}
// Ten badges is not a conclusion. This says the one thing the room decided, then
// groups the desks by the kind of answer they each recorded, so a wall of
// UNAVAILABLE stops reading as ten separate failures.
function RoomConclusion({ roles, report, onOpen }) {
  const groups = groupRoles(roles);
  const head = roomConclusion(roles, report?.paper_ticket, (role) => names[role.role]);
  const order = KIND_ORDER.filter((kind) => (groups[kind] || []).length);
  return (
    <section className="room-conclusion" aria-label="What this room concludes">
      <span className="eyebrow">WHAT THIS ROOM CONCLUDES</span>
      <p className="rc-head">{head}</p>
      <div className="rc-groups">
        {order.map((kind) => (
          <div className={`rc-group rc-${kind}`} key={kind}>
            <div className="rc-title">
              <span className="rc-count">{groups[kind].length}</span>
              <b>{kindWords[kind].head}</b>
            </div>
            <div className="rc-roles">
              {groups[kind].map((r) => (
                <button key={r.role} onClick={() => onOpen(r.role)}>
                  {names[r.role]}
                  <em className={`rc-verdict ${tone(r.review?.verdict || "PENDING")}`}>
                    {r.review?.verdict || "PENDING"}
                  </em>
                </button>
              ))}
            </div>
            <p className="rc-note">{kindWords[kind].note}</p>
          </div>
        ))}
      </div>
    </section>
  );
}

function Chart({ candles, vwap }) {
  const [hover, setHover] = useState(null);
  if (!candles.length)
    return (
      <div className="empty small">
        No completed strategy candles in this session.
      </div>
    );
  const w = 760,
    h = 200,
    p = 20,
    prices = candles.map((c) => c.close),
    low = Math.min(...prices),
    high = Math.max(...prices),
    span = high - low || 1;
  const x = (i) => p + (i * (w - p * 2)) / Math.max(1, candles.length - 1),
    y = (v) => p + ((high - v) * (h - p * 2)) / span;
  const line = prices.map((v, i) => `${x(i)},${y(v)}`).join(" "),
    maxVolume = Math.max(1, ...candles.map((c) => c.volume));
  const selected = candles[hover ?? candles.length - 1];
  return (
    <>
      <div className="chart-readout">
        <strong>{money(selected.close)}</strong>
        <span>
          {time(selected.time).slice(0, 5)} IST <b>·</b>{" "}
          {hover === null ? "Last strategy close" : "Candle close"}
        </span>
      </div>
      <svg
        className="chart"
        viewBox={`0 0 ${w} ${h + 50}`}
        role="img"
        aria-label="Historical five-minute strategy closing prices and volume"
        onMouseLeave={() => setHover(null)}
      >
        <defs>
          <linearGradient id="fill" x1="0" y1="0" x2="0" y2="1">
            <stop stopColor="#34d399" stopOpacity=".20" />
            <stop offset="1" stopColor="#34d399" stopOpacity="0" />
          </linearGradient>
        </defs>
        {[0, 0.5, 1].map((f) => (
          <g key={f}>
            <line
              x1={p}
              y1={p + f * (h - p * 2)}
              x2={w - p}
              y2={p + f * (h - p * 2)}
              className="grid-line"
            />
            <text
              x={w - p}
              y={p + f * (h - p * 2) - 5}
              textAnchor="end"
              className="axis"
            >
              {number(high - f * span)}
            </text>
          </g>
        ))}
        <polygon
          points={`${p},${h} ${line} ${x(candles.length - 1)},${h}`}
          fill="url(#fill)"
        />
        <polyline points={line} className="price-line" />
        {candles.map((c, i) => (
          <g key={c.time}>
            <rect
              x={x(i) - 2}
              y={h + 30 - (c.volume / maxVolume) * 25}
              width="4"
              height={(c.volume / maxVolume) * 25}
              className="volume-bar"
            />
            <rect
              x={x(i) - 5}
              y="0"
              width={Math.max(10, (w - p * 2) / candles.length)}
              height={h + 40}
              fill="transparent"
              onMouseEnter={() => setHover(i)}
            />
          </g>
        ))}
        {hover !== null && (
          <g>
            <line
              x1={x(hover)}
              y1="10"
              x2={x(hover)}
              y2={h}
              className="crosshair"
            />
            <circle cx={x(hover)} cy={y(prices[hover])} r="4" fill="#34d399" />
          </g>
        )}
        {[0, Math.floor(candles.length / 2), candles.length - 1].map((i) => (
          <text
            key={i}
            x={x(i)}
            y={h + 49}
            textAnchor={
              i === 0 ? "start" : i === candles.length - 1 ? "end" : "middle"
            }
            className="axis"
          >
            {time(candles[i].time).slice(0, 5)}
          </text>
        ))}
      </svg>
      <div className="chart-foot">
        <span>
          <i className="legend-dot" /> Candle close{" "}
          <i className="legend-dot volume" /> Volume
        </span>
        <span>Session VWAP {money(vwap)}</span>
      </div>
    </>
  );
}

function App() {
  const [reviewSeed, setReviewSeed] = useState(null),
    [researchTarget, setResearchTarget] = useState(""),
    [researchSymbol, setResearchSymbol] = useState("");
  const viewNames = {
    opportunities: "Market opportunities",
    research: "Individual research",
    journal: "Trading journal",
    team: "Agent observatory",
  };
  const [view, setView] = useState("opportunities"),
    [opportunityRefresh, setOpportunityRefresh] = useState(0),
    [opportunityCount, setOpportunityCount] = useState(null),
    [requestMode, setRequestMode] = useState("review");
  const [list, setList] = useState([]),
    [sessionQuery, setSessionQuery] = useState(""),
    [selected, setSelected] = useState(""),
    [follow, setFollow] = useState(true),
    [data, setData] = useState(null),
    [error, setError] = useState(""),
    [connected, setConnected] = useState(null),
    [loading, setLoading] = useState(true),
    [staleServer, setStaleServer] = useState(""),
    [finishing, setFinishing] = useState(""),
    [finishError, setFinishError] = useState(""),
    [active, setActive] = useState("scanner"),
    [filter, setFilter] = useState("all"),
    [query, setQuery] = useState(""),
    [tab, setTab] = useState("review");
  useEffect(() => {
    document.title = "Trade Desk — " + viewNames[view];
  }, [view]);
  // A tab left open against a desk server that started before these commands
  // existed refuses every specialist button, which looked like a frozen desk. The
  // running server publishes its own allowlist, so the page names what it is
  // missing instead of letting the click fail as a mystery.
  useEffect(() => {
    api("/api/actions")
      .then((doc) => {
        const have = doc.commands || [];
        const missing = ["team review", "team finish"].filter((c) => !have.includes(c));
        setStaleServer(
          missing.length
            ? "This tab is talking to an older desk server: it does not allow " +
                missing.join(" or ") +
                ", so the specialist buttons here are refused. Start the desk dashboard again and open the address it prints."
            : ""
        );
      })
      .catch(() => {});
  }, []);
  const agentDialog = useRef(null),
    dialog = useRef(null),
    choice = useRef(""),
    auto = useRef(true),
    busy = useRef(false),
    mounted = useRef(true);
  choice.current = selected;
  auto.current = follow;
  async function refresh() {
    if (busy.current) return;
    busy.current = true;
    try {
      const result = await api("/api/sessions");
      if (!mounted.current) return;
      setList(result.sessions);
      const id = auto.current
        ? result.sessions[0]?.id
        : choice.current || result.sessions[0]?.id;
      if (id) {
        const value = await api("/api/sessions/" + encodeURIComponent(id));
        if (
          !mounted.current ||
          (!auto.current && choice.current && choice.current !== id)
        )
          return;
        setData(value);
        setSelected(id);
      } else {
        setData(null);
        setSelected("");
      }
      setError("");
      setConnected(new Date().toISOString());
    } catch (e) {
      if (mounted.current) setError(e.message);
    } finally {
      busy.current = false;
      if (mounted.current) setLoading(false);
    }
  }
  useEffect(() => {
    mounted.current = true;
    refresh();
    const interval = setInterval(refresh, 2000);
    return () => {
      mounted.current = false;
      clearInterval(interval);
    };
  }, []);
  // A room that was sealed but never reviewed sits at 0/10 until somebody
  // dispatches the roles. This runs the desk's own per-role checkers against the
  // artifacts ALREADY sealed in this room -- no second seal, no invented verdict,
  // and finalize still re-derives every number from the sealed evidence.
  async function finishRoom() {
    if (!selected || finishing) return;
    setFinishing("running the ten specialists on this room");
    setFinishError("");
    try {
      await runAction("team finish", { session: selected });
    } catch (e) {
      setFinishError(e.message);
    } finally {
      setFinishing("");
      await refresh();
    }
  }
  // A searched symbol with no room on disk used to end at a dead dropdown line.
  // This is the same one-command pass the request dialog runs: the desk seals the
  // room from live evidence, then its per-role checkers answer against only that
  // sealed artifact and finalize re-derives every number.
  async function reviewSearched() {
    const symbol = sessionQuery.trim();
    if (!symbol || finishing) return;
    setFinishing("sealing a room for " + symbol.toUpperCase() + " and running the ten specialists");
    setFinishError("");
    try {
      await runAction("team review", { symbol });
      setFollow(true);
      auto.current = true;
    } catch (e) {
      setFinishError(e.message);
    } finally {
      setFinishing("");
      await refresh();
    }
  }
  function select(id) {
    setFollow(false);
    auto.current = false;
    setSelected(id);
    choice.current = id;
    setData(null);
    setLoading(true);
    refresh();
  }
  // Searching a stock moves the Observatory to that stock's newest room instead of
  // leaving it pinned to whatever was opened last. A symbol with no sealed room
  // changes nothing -- there is nothing honest to show for it yet.
  function followRoom(id) {
    if (id && id !== choice.current && list.some((item) => item.id === id)) select(id);
  }
  // The dropdown holds every sealed room on this machine; a searched symbol has
  // to narrow it instead of making you hunt through the list for it.
  const rooms = list.filter((item) =>
    `${item.symbol} ${item.id}`
      .toLowerCase()
      .includes(sessionQuery.trim().toLowerCase()),
  );
  useEffect(() => {
    if (
      sessionQuery.trim() &&
      rooms.length &&
      !rooms.some((item) => item.id === choice.current)
    )
      select(rooms[0].id);
  }, [sessionQuery]);
  const roles = data?.roles || [],
    done = roles.filter((r) => r.phase === "completed").length,
    running = roles.filter((r) => r.phase === "running").length;
  const a = data?.analysis || {},
    s = data?.context.settings || {},
    ind = a.indicators || {},
    quality = a.data_quality || {},
    report = data?.report;
  const agent = roles.find((r) => r.role === active),
    review = agent?.review;
  // "Needs attention" used to mean "not a PASS", which swept up every WARN and
  // every desk that simply had nothing to certify -- the same confusion the badges
  // made. It now means a refusal, a missing source, or no answer at all.
  const needsAttention = (r) =>
    ["invalid", "failed"].includes(r.phase) ||
    ["veto", "evidence-gap", "pending"].includes(kindOf(r));
  // A BLOCK badge is not always a refusal: one entry veto leaves six desks with
  // nothing to certify. This line counts only the desks that actually refused.
  const realVetoes = (groupRoles(roles).veto || []).length;
  const shown = roles.filter(
    (r) =>
      (filter === "all" || needsAttention(r)) &&
      (names[r.role] + " " + r.role)
        .toLowerCase()
        .includes(query.toLowerCase()),
  );
  // One line per specialist: what it answered, and what it was asked to judge. The
  // raw lifecycle log repeats one role as dispatched, running, completed and review
  // saved, and the desk's own command history has its panel in the journal, so
  // neither belongs here.
  const events = roles
    .map((r) => ({
      role: r.role,
      time: r.saved_at || (r.activity || []).slice(-1)[0]?.time,
      state: r.phase,
      verdict: r.review?.verdict,
      note: descriptions[r.role],
    }))
    .filter((e) => e.time)
    .sort((a, b) => new Date(b.time) - new Date(a.time));
  function scroll(id) {
    document
      .getElementById(id)
      ?.scrollIntoView({ behavior: "smooth", block: "start" });
  }
  function openTeam(id = "overview") {
    setView("team");
    requestAnimationFrame(() => scroll(id));
  }
  function openRequest(mode = "review", seed = null) {
    setReviewSeed(seed);
    setRequestMode(mode);
    requestAnimationFrame(() => dialog.current.showModal());
  }
  function prepareReview(seed = null) {
    openRequest("review", seed);
  }
  function openResearch(id = null, symbol = "") {
    setResearchTarget(id || "");
    setResearchSymbol(symbol);
    setView("research");
    window.scrollTo({ top: 0 });
  }
  function openTeamSession(id) {
    setView("team");
    select(id);
    window.scrollTo({ top: 0 });
  }
  return (
    <div className="shell">
      <aside className="sidebar">
        <a className="brand" href="/" aria-label="Trade Desk home">
          <span className="brand-icon">↗</span>
          <span>
            trade<span className="brand-light">desk</span>
            <small>INDIA / RESEARCH</small>
          </span>
        </a>
        <div className="workspace">
          <i className="workspace-icon">N</i>
          <div>
            Trading workspace<small>NSE / BSE · Paper only</small>
          </div>
        </div>
        <p className="nav-label">WORKSPACE</p>
        <nav>
          <button
            className={`nav-item ${view === "opportunities" ? "selected" : ""}`}
            onClick={() => {
              setView("opportunities");
              window.scrollTo({ top: 0, behavior: "smooth" });
            }}
          >
            <span>⌕</span> Opportunities{" "}
            <span className="nav-count">{opportunityCount ?? "—"}</span>
          </button>
          <button
            className={`nav-item ${view === "research" ? "selected" : ""}`}
            onClick={() => openResearch()}
          >
            <span>◎</span> Individual research
          </button>
          <button
            className={`nav-item ${view === "journal" ? "selected" : ""}`}
            onClick={() => setView("journal")}
          >
            <span>▤</span> Trading journal
          </button>
          <button
            className={`nav-item ${view === "team" ? "selected" : ""}`}
            onClick={() => openTeam()}
          >
            <span>◫</span> Observatory{" "}
            <span
              className="nav-count"
              title="Sealed 10-specialist evidence rooms on this machine"
            >
              {list.length}
            </span>
          </button>
          <button className="nav-item" onClick={() => openTeam("agents")}>
            <span>◇</span> Specialist team
          </button>
          <button className="nav-item" onClick={() => openTeam("evidence")}>
            <span>▤</span> Review evidence
          </button>
          <button className="nav-item" onClick={() => openTeam("activity")}>
            <span>◷</span> Activity
          </button>
        </nav>
        <div className="sidebar-bottom">
          <div className="safety-icon">◇</div>
          <b>Research with guardrails.</b>
          <p>
            Independent judgement.
            <br />
            One shared snapshot.
            <br />
            Zero live orders.
          </p>
          <span className="paper-tag">PAPER ENVIRONMENT</span>
        </div>
        <div className="local-label">
          <i /> Local workspace <span>v1.0</span>
        </div>
      </aside>
      <main>
        <header className="topbar">
          <select
            className="dashboard-mobile-view"
            aria-label="Dashboard view"
            value={view}
            onChange={(e) => setView(e.target.value)}
          >
            <option value="opportunities">Opportunities</option>
            <option value="research">Individual research</option>
            <option value="journal">Trading journal</option>
            <option value="team">Agent observatory</option>
          </select>
          <span className="breadcrumb">
            Workspace <b>/</b> {viewNames[view]}
          </span>
          <div className="top-actions">
            {view === "team" && (
              <span className={`connection ${error ? "disconnected" : ""}`}>
                <i />
                {error
                  ? "Disconnected"
                  : connected
                    ? "Auto-refresh · 2s"
                    : "Connecting…"}
              </span>
            )}
            <button
              className="icon-button"
              onClick={() => {
                refresh();
                setOpportunityRefresh((n) => n + 1);
              }}
              aria-label="Refresh dashboard"
            >
              ↻
            </button>
            <button
              className="primary"
              onClick={() =>
                openRequest(view === "opportunities" ? "scan" : "review")
              }
            >
              ＋ New request
            </button>
          </div>
        </header>
        {staleServer && (
          <p className="notice error" role="alert">
            {staleServer}
          </p>
        )}
        <div className="content" id="overview">
          {view === "opportunities" ? (
            <Opportunities
              refreshToken={opportunityRefresh}
              onCreateRequest={() => openRequest("scan")}
              onCount={setOpportunityCount}
              onPrepare={prepareReview}
              onResearch={openResearch}
            />
          ) : view === "research" ? (
            <Research
              target={researchTarget}
              symbol={researchSymbol}
              onOpenTeam={openTeamSession}
              onRoomChange={followRoom}
              onPrepare={prepareReview}
              refreshToken={opportunityRefresh}
            />
          ) : view === "journal" ? (
            <Journal
              refreshToken={opportunityRefresh}
              onResearch={openResearch}
            />
          ) : (
            <>
              <div className="page-title">
                <div>
                  <div className="eyebrow">
                    <span className="tiny-dot" /> YOUR INDEPENDENT TRADING TEAM
                  </div>
                  <h1>
                    Agent observatory<span>.</span>
                  </h1>
                  <p>
                    Ten perspectives. Shared evidence. A decision you can
                    inspect.
                  </p>
                </div>
                <div className="session-control">
                  <label className="session-label" htmlFor="session-filter">
                    FIND A SYMBOL&apos;S ROOM
                  </label>
                  <input
                    id="session-filter"
                    className="session-filter"
                    placeholder="e.g. RELIANCE or reliance-20261104"
                    value={sessionQuery}
                    onChange={(e) => setSessionQuery(e.target.value)}
                  />
                  <label className="session-label" htmlFor="session">
                    SESSION
                  </label>
                  <select
                    id="session"
                    value={selected}
                    onChange={(e) => select(e.target.value)}
                    disabled={!rooms.length}
                  >
                    {!rooms.length && (
                      <option>
                        {list.length
                          ? "No sealed room matches that search"
                          : "No sessions yet"}
                      </option>
                    )}
                    {rooms.map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.symbol} · {date(item.as_of)} · {time(item.as_of)}
                      </option>
                    ))}
                  </select>
                  <label className="follow">
                    <input
                      type="checkbox"
                      checked={follow}
                      onChange={(e) => {
                        setFollow(e.target.checked);
                        auto.current = e.target.checked;
                        refresh();
                      }}
                    />{" "}
                    Follow latest session
                  </label>
                  {sessionQuery.trim() && !rooms.length && (
                    <div className="session-review">
                      <p className="micro">
                        {list.length
                          ? "No room has been sealed for " +
                            sessionQuery.trim().toUpperCase() +
                            " yet, so there is nothing to show for it."
                          : "Nothing has been reviewed on this machine yet."}{" "}
                        The desk can seal the evidence and put all ten specialists
                        on it now.
                      </p>
                      <button
                        className="sig-secondary"
                        disabled={!!finishing}
                        onClick={reviewSearched}
                      >
                        {finishing
                          ? "Working on " + sessionQuery.trim().toUpperCase() + "…"
                          : "Review " + sessionQuery.trim().toUpperCase() +
                            " with the ten specialists"}
                      </button>
                      <p className="micro">
                        A fresh seal reads the market again, so this takes a
                        minute or two. It books no ticket — that still waits for
                        your own &ldquo;yes&rdquo;.
                      </p>
                      {finishError && (
                        <p className="notice error" role="alert">
                          {finishError}
                        </p>
                      )}
                    </div>
                  )}
                </div>
              </div>
              {error && (
                <div className="notice error" role="alert">
                  Connection interrupted. {error}{" "}
                  {data &&
                    `Showing cached data last received at ${time(connected)} IST.`}
                </div>
              )}
              {data?.integrity_errors.map((e, i) => (
                <div className="notice error" key={i}>
                  {e}
                </div>
              ))}
              {data?.context?.provenance?.historical_replay && (
                <div className="notice">
                  Labelled replay room — the engine sealed only the bars that had already
                  completed by {time(data?.context?.settings?.as_of)} IST, so this is evidence
                  about a past session. It can never become an order.
                </div>
              )}
              {!data ? (
                <section className="panel empty">
                  <span className="empty-symbol">⌖</span>
                  <h2>
                    {loading
                      ? "Connecting to your workspace…"
                      : "Your team’s workspace is ready."}
                  </h2>
                  <p>
                    {loading
                      ? "Reading recorded session evidence."
                      : "Search a symbol on the Individual research page and press Seal a 10-specialist room for it. Its sealed evidence and the reviews recorded against it appear here."}
                  </p>
                  <button
                    className="primary"
                    onClick={() => openRequest("review")}
                  >
                    Copy a review request ↗
                  </button>
                  <p className="micro">
                    Watching local team sessions. No simulated agents or prices.
                  </p>
                </section>
              ) : (
                <>
                  <div className="stats">
                    <div className="stat">
                      <span>
                        SPECIALISTS REVIEWED <span>◇</span>
                      </span>
                      <strong>
                        {done}
                        <small>/ 10</small>
                      </strong>
                      <div className="mini-track">
                        <div style={{ width: `${done * 10}%` }} />
                      </div>
                      <p>
                        {running
                          ? `${running} recorded running`
                          : done === 10
                            ? "Independent reviews collected"
                            : "Waiting for agent reviews"}
                      </p>
                    </div>
                    <div className="stat">
                      <span>
                        STRATEGY DATA <span>▥</span>
                      </span>
                      <strong>
                        {quality.received_bars ?? "—"}
                        <small>/ {quality.expected_bars ?? "—"}</small>
                      </strong>
                      <p className={quality.complete ? "positive" : "warning"}>
                        {quality.complete
                          ? "● Complete strategy coverage"
                          : "● Coverage requires review"}
                      </p>
                      <small>5-minute bars · 09:15–15:15 IST</small>
                    </div>
                    <div className="stat">
                      <span>
                        RISK BUDGET <span>₹</span>
                      </span>
                      <strong>{money(s.risk)}</strong>
                      <p>Capital {money(s.capital)}</p>
                      <small>Supplied paper inputs</small>
                    </div>
                    <div className="stat">
                      <span>
                        ORDER EXECUTION <span>⊘</span>
                      </span>
                      <strong>
                        Disabled<span className="lock-mark">◇</span>
                      </strong>
                      <p className="positive">● Paper-only workspace</p>
                      <small>No execution transport</small>
                    </div>
                  </div>
                  <div className="market-row">
                    <section className="panel market-panel">
                      <div className="panel-head">
                        <div>
                          <h2>
                            {s.symbol}
                            <span className="exchange">NSE / EQUITY</span>
                          </h2>
                          <p>
                            {date(a.last_bar)} · historical strategy session
                          </p>
                        </div>
                        <span className="market-state">
                          <i />
                          {a.strategy_open
                            ? "Strategy window open at cutoff"
                            : "Outside strategy window"}
                        </span>
                      </div>
                      <Chart candles={data.candles} vwap={ind.vwap} />
                      <div className="indicators">
                        {[
                          ["RSI 14", number(ind.rsi14)],
                          ["EMA 9", number(ind.ema9)],
                          ["EMA 21", number(ind.ema21)],
                          [
                            "REL. VOLUME",
                            Number.isFinite(ind.volume_spike)
                              ? number(ind.volume_spike) + "×"
                              : "—",
                          ],
                        ].map(([k, v]) => (
                          <div key={k}>
                            <span>{k}</span>
                            <b>{v}</b>
                          </div>
                        ))}
                      </div>
                    </section>
                    <section className="panel decision-panel">
                      <span className="eyebrow">COMBINED PAPER DECISION</span>
                      <Badge value={data.status} />
                      <h2>
                        {data.status === "BLOCKED"
                          ? "Guardrails are holding."
                          : data.status === "READY_PAPER_TICKET"
                            ? "Paper review cleared."
                            : "Every perspective matters."}
                      </h2>
                      <p>
                        {data.status === "BLOCKED"
                          ? "One or more independent checks vetoed this setup. No paper ticket is released."
                          : data.status === "READY_PAPER_TICKET"
                            ? "Required reviews and checks passed for a paper ticket. Live execution remains disabled."
                            : "The team needs valid reviews and finalization before a paper decision can clear."}
                      </p>
                      <dl>
                        <div>
                          <dt>Analysis cutoff</dt>
                          <dd>{time(s.as_of)} IST</dd>
                        </div>
                        <div>
                          <dt>Market at cutoff</dt>
                          <dd>{a.market_open ? "Open" : "Closed"}</dd>
                        </div>
                        <div>
                          <dt>Latest strategy bar</dt>
                          <dd>{time(a.last_bar).slice(0, 5)} IST</dd>
                        </div>
                        <div>
                          <dt>Supplied tick</dt>
                          <dd>
                            ₹{s.tick} <span className="micro">· verify</span>
                          </dd>
                        </div>
                        <div>
                          <dt>Real vetoes</dt>
                          <dd>
                            {roles.length
                              ? realVetoes
                              : (report?.vetoes?.length ?? "—")}{" "}
                            <span className="micro">
                              of {(report?.vetoes || []).length} BLOCK badge(s)
                            </span>
                          </dd>
                        </div>
                      </dl>
                      {["AWAITING_FINALIZATION", "REVIEW_INCOMPLETE", "PENDING_AGENT_REVIEWS"].includes(
                        data.status
                      ) && (
                        <div className="decision-foot">
                          <div>
                            <button
                              className="sig-primary"
                              disabled={!!finishing || !selected}
                              onClick={finishRoom}
                            >
                              {finishing
                                ? "Running the ten specialists…"
                                : "Run the ten specialists on this room"}
                            </button>
                            <p className="micro">
                              {
                                "Each role's own checker reads only this room's sealed artifact, then the engine finalizes. Nothing is booked — a ticket still needs your own reply."
                              }
                            </p>
                            {finishError && (
                              <p className="notice error">{finishError}</p>
                            )}
                          </div>
                        </div>
                      )}
                      <div className="decision-foot">
                        <span>◇</span>
                        <p>Risk must pass. Any specialist can veto.</p>
                      </div>
                    </section>
                  </div>
                  <section id="agents" className="agent-section">
                    <div className="section-title">
                      <div>
                        <span className="eyebrow">THE SPECIALIST NETWORK</span>
                        <h2>Your trading floor.</h2>
                      </div>
                      <div className="filters">
                        <input
                          aria-label="Search agents"
                          placeholder="Find a specialist…"
                          value={query}
                          onChange={(e) => setQuery(e.target.value)}
                        />
                        <button
                          className={filter === "all" ? "active" : ""}
                          onClick={() => setFilter("all")}
                        >
                          All agents
                        </button>
                        <button
                          className={filter === "issues" ? "active" : ""}
                          onClick={() => setFilter("issues")}
                        >
                          Needs attention
                        </button>
                      </div>
                    </div>
                    {!!roles.length && (
                      <RoomConclusion
                        roles={roles}
                        report={report}
                        onOpen={(role) => {
                          setActive(role);
                          setTab("review");
                          agentDialog.current.showModal();
                        }}
                      />
                    )}
                    <div className="agent-grid">
                      {shown.map((r, i) => (
                        <button
                          key={r.role}
                          className={`agent-card ${active === r.role ? "focused" : ""}`}
                          onClick={() => {
                            setActive(r.role);
                            setTab("review");
                            agentDialog.current.showModal();
                          }}
                          aria-label={`Open ${names[r.role]} workspace`}
                          aria-pressed={active === r.role}
                        >
                          <div className="card-top">
                            <span className="desk-number">
                              DESK{" "}
                              {String(
                                roles.findIndex(
                                  (item) => item.role === r.role,
                                ) + 1,
                              ).padStart(2, "0")}
                            </span>
                            <span className={`phase ${r.phase}`}>
                              <i />
                              {r.phase}
                            </span>
                          </div>
                          <AgentDesk
                            phase={r.phase}
                            index={roles.findIndex(
                              (item) => item.role === r.role,
                            )}
                            verdict={r.review?.verdict}
                          />
                          <h3>{names[r.role]}</h3>
                          <p className="card-reason" title={r.review?.summary}>
                            {r.review?.summary ||
                              `No review recorded yet -- ${phaseWords[r.phase] || r.phase}.`}
                          </p>
                          <span className={`kind-chip kind-${kindOf(r)}`}>
                            {kindWords[kindOf(r)].short}
                          </span>
                          <div className="card-bottom">
                            <Badge value={r.review?.verdict || "PENDING"} />
                            <span className="open-workspace">Open ↗</span>
                          </div>
                        </button>
                      ))}
                    </div>
                    {!shown.length && (
                      <p className="empty small">
                        {filter === "issues"
                          ? "No desk is refusing this room, missing a dated source, or still waiting to answer. Switch to All agents to read the ten reasons."
                          : "No agents match this filter."}
                      </p>
                    )}
                    <p className="micro network-note">
                      Sleeping desks are waiting; working desks have a recorded
                      running event; a report means the review is complete,
                      including BLOCK or UNAVAILABLE. Scanner, news and analyst
                      begin independently; entry → exit → history → sizing →
                      risk → broker → alerts follow their prerequisites.
                      “Dispatched” alone does not prove an agent is currently
                      running.
                    </p>
                  </section>
                  <div className="detail-row">
                    <section id="activity" className="panel activity-panel">
                      <div className="panel-head">
                        <h2>Activity journal</h2>
                        <span className="micro">RECORDED EVENTS</span>
                      </div>
                      <p className="panel-subtitle">
                        One line per specialist: what it answered, and what it was
                        asked to judge.
                      </p>
                      <div className="timeline">
                        {events.length ? (
                          events.map((e, i) => (
                            <button
                              key={i}
                              onClick={() => {
                                setActive(e.role);
                                scroll("evidence");
                              }}
                            >
                              <span
                                className={`event-dot ${tone(e.verdict || e.state)}`}
                              />
                              <div>
                                <b>{names[e.role] || e.role}</b>
                                <p>{e.verdict || phaseWords[e.state] || e.state}</p>
                                {e.note && <small>{e.note}</small>}
                              </div>
                              <time>{time(e.time)}</time>
                            </button>
                          ))
                        ) : (
                          <div className="empty small">
                            Nothing has been dispatched in this room yet, so there
                            is no specialist to report. Search the symbol on the
                            Individual research page and press{" "}
                            <b>Seal the 10-specialist room</b> to record real
                            reviews against it.
                          </div>
                        )}
                      </div>
                    </section>
                    <section id="evidence" className="panel evidence-panel">
                      <div className="panel-head">
                        <div>
                          <span className="eyebrow">INSPECT THE DECISION</span>
                          <h2>{names[active]}</h2>
                        </div>
                        <Badge value={review?.verdict || "PENDING"} />
                      </div>
                      <div className="tabs" role="tablist">
                        {[
                          ["review", "Agent review"],
                          ["engine", "Engine evidence"],
                          ["raw", "Raw JSON"],
                        ].map(([id, title]) => (
                          <button
                            role="tab"
                            aria-selected={tab === id}
                            key={id}
                            className={tab === id ? "active" : ""}
                            onClick={() => setTab(id)}
                          >
                            {title}
                          </button>
                        ))}
                      </div>
                      {tab === "raw" ? (
                        <pre className="json-view">
                          {JSON.stringify(
                            review || {
                              status: "No independent review recorded",
                            },
                            null,
                            2,
                          )}
                        </pre>
                      ) : tab === "engine" ? (
                        <div>
                          <p className="notice">
                            Deterministic evidence prepared by Python. This is
                            separate from the agent’s independent verdict.
                          </p>
                          <pre className="json-view">
                            {JSON.stringify(
                              agent?.artifact?.data || {},
                              null,
                              2,
                            )}
                          </pre>
                        </div>
                      ) : (
                        <div className="review-body">
                          {agent?.errors.map((e) => (
                            <p className="notice error" key={e}>
                              {e}
                            </p>
                          ))}
                          <p className="review-summary">
                            {typeof review?.summary === "string"
                              ? review.summary
                              : "This specialist has not saved an independent review yet. A prepared numerical artifact does not count as an agent completion."}
                          </p>
                          <div className="review-meta">
                            <span>NATIVE ID</span>
                            <code>{agent?.agent_id || "Not delegated"}</code>
                          </div>
                          {Array.isArray(review?.evidence) && (
                            <>
                              <h4>Decisive evidence</h4>
                              <ul className="evidence-list">
                                {review.evidence.map((e, i) => (
                                  <li key={i}>
                                    {typeof e === "string"
                                      ? e
                                      : JSON.stringify(e)}
                                  </li>
                                ))}
                              </ul>
                            </>
                          )}
                          {Array.isArray(review?.sources) &&
                            review.sources.length > 0 && (
                              <>
                                <h4>Research sources</h4>
                                <div className="source-list">
                                  {review.sources
                                    .filter((src) => {
                                      try {
                                        return (
                                          new URL(src.url).protocol === "https:"
                                        );
                                      } catch {
                                        return false;
                                      }
                                    })
                                    .map((src, i) => (
                                      <a
                                        href={src.url}
                                        target="_blank"
                                        rel="noreferrer"
                                        key={i}
                                      >
                                        {new URL(src.url).hostname}{" "}
                                        <span>↗</span>
                                        <small>
                                          {src.published_at
                                            ? "Published " +
                                              date(src.published_at)
                                            : src.published_date
                                              ? `Published ${src.published_date} (${src.publication_timezone}, date only)`
                                              : "Publication time unverified"}
                                        </small>
                                      </a>
                                    ))}
                                </div>
                              </>
                            )}
                        </div>
                      )}
                    </section>
                  </div>
                  <footer>
                    <span>
                      <i className="tiny-dot" /> LOCAL OBSERVABILITY · PAPER
                      ONLY
                    </span>
                    <span>
                      As of {date(s.as_of)}, {time(s.as_of)} IST · Last received{" "}
                      {time(connected)} IST
                    </span>
                  </footer>
                </>
              )}
            </>
          )}
        </div>
      </main>
      <AgentDialog
        dialog={agentDialog}
        agent={agent}
        name={names[active]}
        index={roles.findIndex((r) => r.role === active)}
        asOf={s.as_of}
      />
      <RequestDialog
        dialog={dialog}
        mode={requestMode}
        setMode={setRequestMode}
        seed={reviewSeed}
        onRan={(symbol) => openResearch(null, symbol)}
      />
    </div>
  );
}

createRoot(document.getElementById("root")).render(<App />);
