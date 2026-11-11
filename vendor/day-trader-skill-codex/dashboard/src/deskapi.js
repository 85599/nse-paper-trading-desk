// Shared helpers for the desk-owned dashboard panels (signals, research, journal).
// Reads go to /api/*, writes go to POST /api/action, which only runs the desk's own
// allowlisted commands on loopback. Nothing here can place a live order.

export async function readJSON(path, signal) {
  const response = await fetch(path, { cache: "no-store", signal });
  const doc = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(doc.error || `${path} is unavailable.`);
    // A refused snapshot carries the allowlisted command that would refresh it.
    error.fix = doc.fix || null;
    throw error;
  }
  return doc;
}

export async function runAction(command, body = {}) {
  const response = await fetch("/api/action", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ command, ...body }),
  });
  const doc = await response.json().catch(() => ({}));
  // A refused trade is still a completed desk run: the engine exits non-zero on
  // purpose and its stdout carries the reasons. Only a body with no output at all
  // (bad input, a stale snapshot, another command holding the lock) is an error.
  if (!response.ok && doc.output === undefined) throw new Error(actionError(command, doc));
  return doc;
}

// A server process started before a command joined the allowlist refuses it by
// printing the entire list, which reads like a broken desk and hides the one thing
// worth knowing. Say what is missing and where to go instead of dumping the list.
function actionError(command, doc) {
  const text = String(doc.error || "");
  if (/^Unknown command/.test(text))
    return (
      '"' +
      command +
      '" is not allowed by this server process, which started before that command ' +
      "existed. The page you have open is current; the desk behind it is not. Start " +
      "the desk dashboard again and open the address it prints."
    );
  return text || `desk ${command} failed (exit ${doc.returncode}).`;
}

export const clock = (value) =>
  value && Number.isFinite(new Date(value).getTime())
    ? new Intl.DateTimeFormat("en-IN", {
        timeZone: "Asia/Kolkata",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      }).format(new Date(value)) + " IST"
    : "—";

export const stamp = (value) =>
  value && Number.isFinite(new Date(value).getTime())
    ? new Intl.DateTimeFormat("en-IN", {
        timeZone: "Asia/Kolkata",
        day: "2-digit",
        month: "short",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      }).format(new Date(value)) + " IST"
    : "—";

export const num = (value, dp = 2) =>
  Number.isFinite(value)
    ? new Intl.NumberFormat("en-IN", {
        minimumFractionDigits: dp,
        maximumFractionDigits: dp,
      }).format(value)
    : "—";

export const signed = (value, dp = 2) =>
  Number.isFinite(value) ? `${value >= 0 ? "+" : ""}${value.toFixed(dp)}` : "—";

export const ageText = (seconds) =>
  !Number.isFinite(seconds)
    ? "—"
    : seconds < 90
      ? "just now"
      : seconds < 5400
        ? `${Math.round(seconds / 60)} min ago`
        : `${(seconds / 3600).toFixed(1)} h ago`;
