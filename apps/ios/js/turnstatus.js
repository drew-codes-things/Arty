export const AUTO_MODEL_ID = "auto";
export const AUTO_MODEL_NAME = "Arty Auto";
export const AUTO_MODEL_HINT = "Picks the best model for each message";
const THOUSAND = 1000;
const MILLION = 1000 * 1000;
const WHOLE = 100;
const ONE_DECIMAL = 10;

export const MAIN_MODEL_ID = "Arty";
const NAMED_BY_MODEL = ["arty/", "solo/"];
const OFFLINE_SUFFIX = " (offline)";

function ownLabel(model) {
  return NAMED_BY_MODEL.some((prefix) => model.id.startsWith(prefix)) ? model.model || model.name : model.name;
}

function pickerLabel(model) {
  const label = ownLabel(model);
  return model.available === false && !label.endsWith(OFFLINE_SUFFIX) ? label + OFFLINE_SUFFIX : label;
}

export function pickerModels(models) {
  const listed = models.find((model) => model.id === AUTO_MODEL_ID);
  const auto = { id: AUTO_MODEL_ID, name: AUTO_MODEL_NAME, model: "", description: listed?.description || AUTO_MODEL_HINT };
  const seen = new Set();
  const singles = [];
  for (const model of models) {
    if (model.id === AUTO_MODEL_ID || model.id === MAIN_MODEL_ID) continue;
    const name = pickerLabel(model);
    if (seen.has(name) || seen.has(model.model)) continue;
    seen.add(name);
    if (model.model) seen.add(model.model);
    singles.push({ ...model, name });
  }
  return [auto, ...singles];
}

export function modelLabel(models, id) {
  if (id === AUTO_MODEL_ID) return AUTO_MODEL_NAME;
  const found = models.find((model) => model.id === id);
  if (!found) return id;
  return id === MAIN_MODEL_ID ? found.model || found.name : ownLabel(found);
}

export function replyModelLabel(arty) {
  if (!arty?.model) return "";
  return arty.auto && arty.role ? `${arty.model} · ${AUTO_MODEL_NAME}: ${arty.role}` : arty.model;
}

export function shortCount(count) {
  if (count >= MILLION) return `${trimmed(count / MILLION)}M`;
  if (count >= THOUSAND) return `${trimmed(count / THOUSAND)}K`;
  return String(count);
}

function trimmed(value) {
  return value >= WHOLE ? String(Math.round(value)) : String(Math.round(value * ONE_DECIMAL) / ONE_DECIMAL);
}

export function wholePercent(part, whole) {
  if (!(whole > 0) || !(part >= 0)) return null;
  const percent = Math.floor((part / whole) * WHOLE);
  return part < whole ? Math.min(percent, WHOLE - 1) : WHOLE;
}

function plural(count, word) {
  return `${count} ${word}${count === 1 ? "" : "s"}`;
}

export function chatStats(messages) {
  const replies = messages.filter((message) => message.role === "assistant" && message.arty);
  const latest = [...replies].reverse().find((message) => message.arty.usage)?.arty;
  const usage = latest?.usage;
  const stats = { turns: messages.filter((message) => message.role === "user").length };
  if (replies.some((message) => Number.isInteger(message.arty.steps))) {
    stats.steps = replies.reduce((total, message) => total + (message.arty.steps ?? 0), 0);
  }
  if (!usage) return stats;
  if (usage.tokens_per_second > 0) stats.tokensPerSecond = usage.tokens_per_second;
  if (usage.context_tokens > 0) stats.tokens = usage.context_tokens;
  if (usage.cached_tokens !== undefined) stats.cacheHit = wholePercent(usage.cached_tokens, usage.prompt_tokens);
  if (stats.tokens && latest.context_window > 0) stats.contextUsed = wholePercent(stats.tokens, latest.context_window);
  return stats;
}

export function statusLine(stats) {
  const counts = [plural(stats.turns, "turn"), stats.steps !== undefined ? plural(stats.steps, "step") : null].filter(Boolean).join(" ");
  const speed = stats.tokensPerSecond ? `${Math.round(stats.tokensPerSecond)} tok/s` : null;
  const tokens = stats.tokens ? `${shortCount(stats.tokens)} tok` : null;
  const cache = stats.cacheHit !== undefined && stats.cacheHit !== null ? `Cache hit ${stats.cacheHit}%` : null;
  return [[counts, speed].filter(Boolean).join(" · "), [tokens, cache].filter(Boolean).join(" · ")].filter(Boolean);
}
