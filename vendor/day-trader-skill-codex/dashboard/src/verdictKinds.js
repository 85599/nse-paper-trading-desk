// One verdict is only half an answer. The same BLOCK badge used to cover "this role
// read its evidence and refuses" and "there was nothing here to refuse about", which
// made every quiet room read as ten separate failures. Each checker records which of
// those it means, so the desk can group by that word instead of by the badge.
export const roleNames = {
  scanner: "Market scanner",
  news: "News & catalysts",
  analyst: "Financial analyst",
  entry: "Entry specialist",
  exit: "Exit specialist",
  timemachine: "Time machine",
  budget: "Position sizing",
  risk: "Risk manager",
  broker: "Paper broker",
  alerts: "Alerts",
};
export const kindWords = {
  veto: {
    short: "says no",
    head: "Real vetoes",
    note: "these roles read their own evidence and refused the bar.",
  },
  "nothing-to-check": {
    short: "nothing yet",
    head: "Nothing to certify yet",
    note: "an earlier stage of this room produced no plan, order or ticket, so these roles had no evidence to answer about. This is the entry stage's decision travelling down the room, not a defect.",
  },
  "evidence-gap": {
    short: "no source",
    head: "No admissible evidence to read",
    note: "the desk looked for dated exchange or issuer evidence for these roles and found none published before this room's cutoff. It reports the gap instead of inventing a citation.",
  },
  confirmed: {
    short: "checked",
    head: "Answered from its own evidence",
    note: "these roles opened their sealed artifact and what they found is in the reason line.",
  },
  pending: {
    short: "not answered",
    head: "Has not answered yet",
    note: "no review is recorded for these desks, so they hold no opinion at all.",
  },
};
export const KIND_ORDER = [
  "veto",
  "evidence-gap",
  "nothing-to-check",
  "confirmed",
  "pending",
];
export const kindOf = (role) => {
  const review = role.review;
  if (!review) return "pending";
  if (review.reason_kind) return review.reason_kind;
  return review.verdict === "BLOCK"
    ? "veto"
    : ["PASS", "WARN"].includes(review.verdict)
      ? "confirmed"
      : "evidence-gap";
};
export function groupRoles(roles) {
  const groups = {};
  roles.forEach((role) => {
    const kind = kindOf(role);
    groups[kind] = groups[kind] || [];
    groups[kind].push(role);
  });
  return groups;
}
// One veto in the entry stage empties every desk downstream of it, so the room has a
// single answer rather than nine.
export function roomConclusion(roles, ticket, label = (role) => role.role) {
  const groups = groupRoles(roles);
  const vetoed = groups.veto || [];
  const gaps = groups["evidence-gap"] || [];
  const waiting = groups["nothing-to-check"] || [];
  const pending = groups.pending || [];
  const entryAlone =
    vetoed.length === 1 &&
    vetoed[0].role === "entry" &&
    String(vetoed[0].review?.reason_code || "").startsWith("NO_COMPLETED_BAR");
  if (ticket)
    return "A draft paper ticket exists for this bar. Nothing is booked until you reply yes.";
  if (pending.length)
    return `${pending.length} of the ten desks have not answered yet, so this room has no conclusion.`;
  if (entryAlone)
    return "No trade at this bar. The completed-bar trigger never printed in the sealed window, and that one fact is what the desks below it are reporting.";
  if (vetoed.length)
    return `Stopped by ${vetoed.map(label).join(", ")}. Open a desk to read the reason it gave.`;
  if (gaps.length)
    return `Nothing was vetoed. ${gaps.length} desk(s) found no dated exchange or issuer evidence to read before this cutoff.`;
  return waiting.length
    ? `Nothing was vetoed, and ${waiting.length} desk(s) had no evidence to certify yet.`
    : "Nothing was vetoed and every desk answered from its own evidence.";
}
