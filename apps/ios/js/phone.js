const ACTION_LINE = /\s*<phone-action>([\s\S]*?)<\/phone-action>\s*/;
const KINDS = new Set(["call", "text"]);
const CAMERA_KIND = "camera";
const DEFAULT_PHOTO_QUESTION = "What is in this photo?";
const MAX_QUESTION_CHARS = 300;
const NUMBER_CHARACTERS = /^\+?[\d\s().-]+$/;
const MIN_DIGITS = 3;
const MAX_DIGITS = 15;

function readAction(json) {
  try {
    const action = JSON.parse(json);
    if (action?.kind === CAMERA_KIND) {
      return { kind: CAMERA_KIND, question: typeof action.question === "string" ? action.question.slice(0, MAX_QUESTION_CHARS) : "" };
    }
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
  if (action.kind === "call") {
    const number = dialableNumber(String(action.who ?? ""));
    return number ? `tel:${number}` : null;
  }
  const body = action.body ? `&body=${encodeURIComponent(action.body)}` : "";
  return `sms:${body}`;
}

export function allowCard(action) {
  if (!action || action.kind !== "text") return null;
  const lines = [action.body ? `Text: ${action.body}` : "Text: (you write it in Messages)",
    `To: you pick ${action.who} from your contacts in Messages`];
  return { title: "Arty asks to open Messages with this text", lines, button: "Open Messages" };
}

export function isCameraAction(action) {
  return action?.kind === CAMERA_KIND;
}

export function photoQuestion(action, typedText = "") {
  if (typedText.trim()) return typedText;
  return action?.question?.trim() || DEFAULT_PHOTO_QUESTION;
}
