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
  return NAMED_BY_MODEL.some((prefix) => model.id.startsWith(prefix)) ? model.label || model.model || model.name : model.name;
}

function pickerLabel(model) {
  const label = ownLabel(model);
  return model.available === false && !label.endsWith(OFFLINE_SUFFIX) ? label + OFFLINE_SUFFIX : label;
}

export const MAIN_MODEL_NAME = "Arty";
export const REMOTE_SECTION = "Remote";
export const ARTY_TAB = "Arty";
const ARTY_TAB_INDEX = 0;
export const CHOOSE_TAB = "Choose a model";
const REMOTE_PREFIXES = ["remote/", "arty/remote/"];
const HELPER_MODELS = ["qwen2.5vl"];

function isHelper(model) {
  return HELPER_MODELS.some((helper) => (model.model || "").startsWith(helper) || model.id.includes(`/${helper}`));
}

function isRemote(model) {
  return REMOTE_PREFIXES.some((prefix) => model.id.startsWith(prefix) || (model.model || "").startsWith(prefix));
}

function isLabelled(model) {
  return model.id.startsWith("arty/") && Boolean(model.label) && model.label !== model.model;
}

function autoEntry(models) {
  const listed = models.find((model) => model.id === AUTO_MODEL_ID);
  return { id: AUTO_MODEL_ID, name: AUTO_MODEL_NAME, model: "", description: listed?.description || AUTO_MODEL_HINT };
}

function onceEach(models) {
  const seen = new Set();
  const kept = [];
  for (const model of models) {
    const name = pickerLabel(model);
    if (seen.has(name) || seen.has(model.model)) continue;
    seen.add(name);
    if (model.model) seen.add(model.model);
    kept.push({ ...model, name });
  }
  return kept;
}

export function pickerTabs(models) {
  const main = models.find((model) => model.id === MAIN_MODEL_ID);
  const singles = models.filter((model) => model.id !== AUTO_MODEL_ID && model.id !== MAIN_MODEL_ID && !isHelper(model));
  const local = singles.filter((model) => !isRemote(model));
  const ordered = [...local.filter(isLabelled), ...local.filter((model) => !isLabelled(model))];
  const offersArty = Boolean(main) || local.length > 0;
  const artyTab = offersArty ? [...(main ? [{ ...main, name: MAIN_MODEL_NAME }] : []), autoEntry(models)] : [];
  return {
    tabs: [{ name: ARTY_TAB, models: artyTab }, { name: CHOOSE_TAB, models: onceEach(ordered) }],
    remote: onceEach(singles.filter(isRemote)),
  };
}

export function pickerModels(models) {
  const { tabs, remote } = pickerTabs(models);
  return [...tabs.flatMap((tab) => tab.models), ...remote];
}

export function drawnTabs(models) {
  return pickerTabs(models).tabs.map((tab, index) => ({ ...tab, index }))
    .filter((tab) => tab.index !== ARTY_TAB_INDEX || tab.models.length);
}

export function pickerTab(models, id) {
  const { tabs } = pickerTabs(models);
  return !tabs[0].models.length || tabs[1].models.some((model) => model.id === id) ? 1 : 0;
}

export function defaultModel(models) {
  if (models.some((model) => model.id === MAIN_MODEL_ID)) return MAIN_MODEL_ID;
  const { tabs, remote } = pickerTabs(models);
  return (tabs[1].models[0] || remote[0] || autoEntry(models)).id;
}

export function modelLabel(models, id) {
  if (id === AUTO_MODEL_ID) return AUTO_MODEL_NAME;
  const found = models.find((model) => model.id === id);
  if (!found) return id;
  return id === MAIN_MODEL_ID ? MAIN_MODEL_NAME : ownLabel(found);
}

const ENGINE_ID_PREFIX = "hf.co/";
const ENGINE_FILE_SUFFIX = /-GGUF$/i;

function readableModelName(id) {
  if (!id.startsWith(ENGINE_ID_PREFIX)) return id;
  return id.split("/").pop().split(":")[0].replace(ENGINE_FILE_SUFFIX, "");
}

function answeringModelName(id, models) {
  const listed = models.find((model) => model.model === id || model.id === id || model.id === `arty/${id}`);
  const label = listed?.label && listed.label !== id ? listed.label : "";
  return label || readableModelName(id);
}

export function persistedPick(arty) {
  return arty?.persist === true && typeof arty.pick === "string" && arty.pick !== "" ? arty.pick : null;
}

export function replyModelLabel(arty, models = []) {
  if (!arty?.model) return "";
  const name = answeringModelName(arty.model, models);
  return arty.auto && arty.role ? `${name} · ${AUTO_MODEL_NAME}: ${arty.role}` : name;
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
