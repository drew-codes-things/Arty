'use strict';

const AUTO_MODEL_NAME = 'Arty Auto';
const THOUSAND = 1000;
const MILLION = 1000 * 1000;
const WHOLE = 100;
const ONE_DECIMAL = 10;
const CONTEXT_WARN_PERCENT = 75;
const CONTEXT_FULL_PERCENT = 90;

const PICKER_TAB_NAMES = ['Arty', 'Choose a model'];
const ARTY_TAB_INDEX = 0;
const PICKER_REMOTE_NAME = 'Remote';
const PICKER_EMPTY_NOTE = 'No models here for this key.';
const ARTY_GROUP_IDS = ['Arty', 'auto'];

function pickerGroup(model) {
  return model.group || (ARTY_GROUP_IDS.includes(model.id) ? 'arty' : 'choose');
}

function pickerSections(models) {
  const inGroup = (group) => models.filter((model) => pickerGroup(model) === group);
  return {
    tabs: [{ name: PICKER_TAB_NAMES[0], models: inGroup('arty') }, { name: PICKER_TAB_NAMES[1], models: inGroup('choose') }],
    remote: inGroup('remote'),
    remoteName: PICKER_REMOTE_NAME,
  };
}

function drawnPickerTabs(tabs) {
  return tabs.map((tab, index) => ({ ...tab, index })).filter((tab) => tab.index !== ARTY_TAB_INDEX || tab.models.length);
}

function pickerTabFor(models, current) {
  if (!models.some((model) => pickerGroup(model) === 'arty')) return 1;
  const found = models.find((model) => model.id === current);
  return found && pickerGroup(found) === 'choose' ? 1 : 0;
}

function pickerEmptyNote() {
  return PICKER_EMPTY_NOTE;
}

const ENGINE_ID_PREFIX = 'hf.co/';
const ENGINE_FILE_SUFFIX = /-GGUF$/i;

function readableModelName(id) {
  if (!id.startsWith(ENGINE_ID_PREFIX)) return id;
  return id.split('/').pop().split(':')[0].replace(ENGINE_FILE_SUFFIX, '');
}

function answeringModelName(id, models) {
  const listed = models.find((model) => model.model === id || model.id === id || model.id === `arty/${id}`);
  const label = listed?.label && listed.label !== id ? listed.label : '';
  return label || readableModelName(id);
}

function replyModelLabel(arty, models = []) {
  if (!arty?.model) return '';
  const name = answeringModelName(arty.model, models);
  return arty.auto && arty.role ? `${name} · ${AUTO_MODEL_NAME}: ${arty.role}` : name;
}

function shortCount(count) {
  const trimmed = (value) => (value >= WHOLE ? String(Math.round(value)) : String(Math.round(value * ONE_DECIMAL) / ONE_DECIMAL));
  if (count >= MILLION) return `${trimmed(count / MILLION)}M`;
  if (count >= THOUSAND) return `${trimmed(count / THOUSAND)}K`;
  return String(count);
}

function wholePercent(part, whole) {
  if (!(whole > 0) || !(part >= 0)) return null;
  const percent = Math.floor((part / whole) * WHOLE);
  return part < whole ? Math.min(percent, WHOLE - 1) : WHOLE;
}

function chatStats(messages) {
  const replies = messages.filter((message) => message.role === 'assistant' && message.arty);
  const latest = [...replies].reverse().find((message) => message.arty.usage)?.arty;
  const usage = latest?.usage;
  const stats = { turns: messages.filter((message) => message.role === 'user').length };
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

function statusLine(stats) {
  const plural = (count, word) => `${count} ${word}${count === 1 ? '' : 's'}`;
  const counts = [plural(stats.turns, 'turn'), stats.steps !== undefined ? plural(stats.steps, 'step') : null].filter(Boolean).join(' ');
  const speed = stats.tokensPerSecond ? `${Math.round(stats.tokensPerSecond)} tok/s` : null;
  const tokens = stats.tokens ? `${shortCount(stats.tokens)} tok` : null;
  const cache = stats.cacheHit !== undefined && stats.cacheHit !== null ? `Cache hit ${stats.cacheHit}%` : null;
  return [counts, speed, tokens, cache].filter(Boolean).join(' · ');
}

function contextLevel(used) {
  if (used >= CONTEXT_FULL_PERCENT) return 'full';
  if (used >= CONTEXT_WARN_PERCENT) return 'warn';
  return 'ok';
}

function matchingEntities(entities, query) {
  const wanted = query.trim().toLowerCase();
  if (!wanted) return entities;
  return entities.filter((entity) => [entity.name, entity.entityType, ...(entity.observations ?? [])]
    .some((text) => String(text ?? '').toLowerCase().includes(wanted)));
}

function likelyOwner(entities) {
  const people = entities.filter((entity) => entity.entityType === 'person');
  return [...people].sort((a, b) => (b.observations?.length ?? 0) - (a.observations?.length ?? 0))[0]?.name ?? '';
}

function movesThePicker(arty) {
  return arty?.persist === true && typeof arty.pick === 'string' && arty.pick !== '';
}
