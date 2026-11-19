'use strict';

const PRESENTING_STORAGE_KEY = 'presenting';
const PRESENTING_CLASS = 'presenting';
const PRESENT_SHORTCUT_LABEL = 'Ctrl+Shift+P';
const PRESENT_SHORTCUT_KEY = 'p';
const PRESENTING_HIDDEN_VIEWS = ['memory', 'updates'];
const PRIVATE_MODEL_LABEL = /\(private\)\s*$/i;
const AUDIENCE_MODEL_NAME = 'Arty Auto';
const ADDRESS_PLACEHOLDER = 'the server';
const KNOWN_DOMAIN_ENDINGS = 'com|net|org|io|xyz|dev|app|uk|co|local|lan|home|internal';
const ADDRESS_PATTERNS = [
  /\bhttps?:\/\/[^\s"')]*[^\s"')\],.:;!?]/gi,
  /\bhost=(['"])[^'"]*\1/gi,
  /\b\d{1,3}(?:\.\d{1,3}){3}(?::\d{1,5})?\b/g,
  /\b(?:[a-z0-9-]+\.)+[a-z0-9-]+:\d{2,5}\b/gi,
  new RegExp(`\\b(?:[a-z0-9-]+\\.)+(?:${KNOWN_DOMAIN_ENDINGS})\\b`, 'gi'),
];

function isPrivateModel(model) {
  if (!model) return false;
  return PRIVATE_MODEL_LABEL.test(String(model.name || ''));
}

function modelsForAudience(models, presenting) {
  return presenting ? models.filter((model) => !isPrivateModel(model)) : models;
}

function modelNameForAudience(model, presenting) {
  if (!model) return null;
  return presenting && isPrivateModel(model) ? AUDIENCE_MODEL_NAME : model.name;
}

function scrubAddresses(text) {
  return ADDRESS_PATTERNS.reduce((scrubbed, pattern) => scrubbed.replace(pattern, ADDRESS_PLACEHOLDER), String(text ?? ''));
}

function isPresentShortcut(event) {
  const onlyControlAndShift = event.ctrlKey && event.shiftKey && !event.altKey && !event.metaKey;
  return onlyControlAndShift && !event.repeat && String(event.key).toLowerCase() === PRESENT_SHORTCUT_KEY;
}

function audienceMaySee(view, presenting) {
  return !(presenting && PRESENTING_HIDDEN_VIEWS.includes(view));
}

function presentTooltip(presenting) {
  const hides = 'hides memory, past chats, notifications, addresses and private models, and makes the text bigger';
  return presenting
    ? `Presentation mode is on (${PRESENT_SHORTCUT_LABEL} to turn it off)`
    : `Presentation mode: ${hides} (${PRESENT_SHORTCUT_LABEL})`;
}

function createPresentation({ initial, save, root, changed }) {
  let on = Boolean(initial);
  const apply = () => {
    root.classList.toggle(PRESENTING_CLASS, on);
    changed(on);
  };
  return {
    get on() { return on; },
    start: apply,
    set(value) {
      on = Boolean(value);
      save(on);
      apply();
    },
    toggle() { this.set(!on); },
  };
}
