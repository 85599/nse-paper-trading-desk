import React, { useEffect, useLayoutEffect, useState } from "react";
import { runAction, readJSON } from "./deskapi";

// The desk's own limits live in config/desk.json and the engine enforces them, so
// this panel reads them instead of asking you to retype numbers it would ignore.
const money = (value) =>
  Number.isFinite(value) ? "₹" + Number(value).toLocaleString("en-IN") : "—";

export default function RequestDialog({ dialog, mode, setMode, seed, onRan }) {
  const [symbol, setSymbol] = useState(""),
    [limits, setLimits] = useState(null),
    [running, setRunning] = useState(""),
    [ran, setRan] = useState(""),
    [result, setResult] = useState(null),
    [error, setError] = useState("");
  useLayoutEffect(() => {
    setSymbol(seed?.symbol || "");
    setResult(null);
    setError("");
  }, [seed]);
  useEffect(() => {
    readJSON("/api/limits")
      .then(setLimits)
      .catch(() => setLimits(null));
  }, []);
  // The engine asks for an exchange-suffixed symbol; the desk's own registry
  // resolves a bare NSE name the same way, so either spelling is accepted here.
  const typed = symbol.trim().toUpperCase();
  const ready = /^[A-Z0-9&.-]+$/.test(typed);
  const full = /\.NS$|\.BO$/.test(typed) ? typed : typed + ".NS";

  // Each choice maps to exactly one allowlisted desk command; nothing here builds an
  // argv, so a browser-side string can never become a shell argument.
  const CHOICE = {
    review: { command: "team review", label: "Specialist review of " + full },
    scan: { command: "scan daily", label: "Universe scan" },
    orb: { command: "scan orb", label: "Opening-range scan of the saved list" },
  };

  async function run() {
    const { command, label } = CHOICE[mode] || CHOICE.scan;
    setRunning(command);
    setRan(label);
    setError("");
    setResult(null);
    try {
      const event =
        mode === "review"
          ? await runAction(command, { symbol: full })
          : await runAction(command);
      setResult(event);
      // Sealing a room is only useful if the panel that reads it moves to it.
      if (mode === "review" && onRan) onRan(full, event);
    } catch (e) {
      setError(e.message);
    } finally {
      setRunning("");
    }
  }

  const verdict = (event) => {
    const out = String(event?.output || "");
    if (!out) return "The desk run returned no output.";
    if (/stderr:/.test(out))
      return out
        .slice(out.indexOf("stderr:") + 7)
        .trim()
        .split("\n")[0];
    // teamflow.prepare prints the seal as "preflight  : READY" and the engine's
    // own refusal as "Engine refused the session -- ...", so those two lines are
    // the answer to read, not the artifact legend printed under them.
    const line = out
      .split("\n")
      .map((text) => text.trim())
      .find(
        (text) =>
          /^(Engine refused|READY|BLOCKED|NOT_READY|VERDICT|preflight|scanned|No verified tick)/i.test(
            text,
          ),
      );
    return line || out.split("\n").filter(Boolean).slice(-1)[0] || "Run complete.";
  };

  return (
    <dialog ref={dialog} className="request-dialog" aria-labelledby="request-title">
      <div className="modal-head">
        <div>
          <span className="eyebrow">DESK ACTION</span>
          <h2 id="request-title">Run it on the desk.</h2>
        </div>
        <button
          className="icon-button"
          onClick={() => dialog.current.close()}
          aria-label="Close desk action"
        >
          ×
        </button>
      </div>
      <p>
        One click runs the desk command behind this panel and shows what the engine
        answered. Nothing here approves or fills anything: a ticket is booked only
        after you type your own reply starting with “yes”.
      </p>
      <label className="request-type">
        What to run
        <select value={mode} onChange={(e) => setMode(e.target.value)}>
          <option value="review">Review one stock with the 10 specialists</option>
          <option value="scan">Scan the saved NSE universe for setups</option>
          <option value="orb">Scan the opening ranges of the intraday list</option>
        </select>
      </label>
      {mode === "review" ? (
        <>
          <label>
            Symbol
            <input
              value={symbol}
              placeholder="RELIANCE or RELIANCE.NS"
              onChange={(e) => setSymbol(e.target.value.toUpperCase())}
            />
          </label>
          <p className="micro">
            One click seals a fresh evidence room for {ready ? full : "your symbol"}{" "}
            and puts all ten specialists on it — each role’s own checker reads only
            its sealed artifact and records a verdict, then the engine finalizes. It
            never books a ticket; that still needs your own “yes”. The combined
            decision shows on the stock’s research page.
          </p>
        </>
      ) : mode === "orb" ? (
        <p className="micro">
          The opening-range pass over the desk’s shorter intraday list: which
          ranges are set, which are still forming and how far each price sits from
          its range high. It takes about a minute and sizes or books nothing. Its
          result is saved as its own opening-range file, so the Opportunities
          board keeps showing the daily universe instead of going stale.
        </p>
      ) : (
        <p className="micro">
          A full daily pass over the saved universe. It takes several minutes and
          reports how many symbols were scanned and how many failed.
        </p>
      )}
      <div className="request-divider">THE LIMITS THIS DESK ENFORCES</div>
      {limits ? (
        <div className="form-grid">
          {[
            ["Paper capital", money(limits.capital_inr)],
            ["Max loss a trade", money(limits.max_loss_per_trade_inr)],
            ["Max daily loss", money(limits.max_daily_loss_inr)],
            ["Max order value", money(limits.max_order_value_inr)],
            ["Entries a day", limits.max_trades_per_day],
            ["Entry window", limits.entry_window + ", flat " + limits.flat_by],
          ].map(([label, value]) => (
            <p className="micro" key={label}>
              {label}: <b>{value}</b>
            </p>
          ))}
        </div>
      ) : (
        <p className="micro">
          The desk settings could not be read, so no numbers are shown.
        </p>
      )}
      {error && (
        <p className="notice error" role="alert">
          {error}
        </p>
      )}
      {result && (
        <p className="notice" role="status">
          <b>{ran || "Desk run"}:</b> {verdict(result)}
          {result.returncode ? " (exit " + result.returncode + ")" : ""} ·{" "}
          {result.seconds}s
        </p>
      )}
      <button
        className="primary"
        disabled={!!running || (mode === "review" && !ready)}
        onClick={run}
      >
        {running
          ? "Running on the desk…"
          : mode === "review"
            ? "Review " + (symbol.trim().toUpperCase() || "the stock") + " now ↗"
            : mode === "orb"
              ? "Scan the opening ranges now ↗"
              : "Scan the universe now ↗"}
      </button>
      <p className="micro" role="status">
        {mode === "review" && !ready
          ? "Type a symbol first."
          : running
            ? "The desk runs one command at a time, so other buttons wait for it."
            : ""}
      </p>
    </dialog>
  );
}
