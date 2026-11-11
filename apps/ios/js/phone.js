const ACTION_LINE = /\s*<phone-action>([\s\S]*?)<\/phone-action>\s*/;
const KINDS = new Set(["call", "text"]);
const NUMBER_CHARACTERS = /^\+?[\d\s().-]+$/;
const MIN_DIGITS = 3;
const MAX_DIGITS = 15;

function readAction(json) {
  try {
    const action = JSON.parse(json);
    if (!action || !KINDS.has(action.kind) || typeof action.who !== "string") return null;
    return { kind: action.kind, who: action.who, body: typeof action.body === "string" ? action.body : "" };
  } catch {
    return null;
  }
}

export function splitPhoneAction(reply) {
  const text = String(reply ?? "");
  const found = text.match(ACTION_LINE);
  if (!found) return { text: text.trim(), action: null };
  return { text: text.replace(ACTION_LINE, "\n").trim(), action: readAction(found[1]) };
}

export function dialableNumber(who) {
  if (!NUMBER_CHARACTERS.test(who.trim())) return null;
  const plus = who.trim().startsWith("+") ? "+" : "";
  const digits = who.replace(/\D/g, "");
  if (digits.length < MIN_DIGITS || digits.length > MAX_DIGITS) return null;
  return plus + digits;
}

export function phoneLink(action) {
  if (!action || !KINDS.has(action.kind)) return null;
  const number = dialableNumber(String(action.who ?? ""));
  if (action.kind === "call") return number ? `tel:${number}` : null;
  const body = action.body ? `&body=${encodeURIComponent(action.body)}` : "";
  return `sms:${number ?? ""}${body}`;
}
