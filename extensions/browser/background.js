'use strict';
// Arty for Chrome: takes browser commands from Arty Desktop's local service and runs them here.
// The service only listens on this computer; see server/process/browser.py for the commands.

const SERVICE = 'http://127.0.0.1:8765';
const POLL_SECONDS = 25;
const OFFLINE_RETRY_MS = 5000;
const LOAD_WAIT_MS = 15000;
const AFTER_CLICK_MS = 1500;
const RESULT_SEND_TRIES = 3;
const RESULT_RETRY_MS = 1000;
const SCROLL_TEXT_CHARS = 4000;
const SCREENSHOT_QUALITY = 70;
const QUIET_MS = 400;
const SETTLE_LIMIT_MS = 3000;
const PRIVATE_FIELD_HINTS = 'pass|pwd|\\bpin\\b|pin.?code|one.?time|\\botp\\b|2fa|mfa|verif\\w*.?(code|number)|auth\\w*.?code|security.?(code|answer|question)|memorable|\\bcards?\\b|card.?(num|no\\b|holder|code|cvc|cvv|exp)|cc-|\\bcc.?(num|csc|cvc|exp)|cvv|cvc|\\bcsc\\b|iban|sort.?code|account.?(num|no\\b)|routing|\\bssn\\b|social.?security|national.?insurance|\\bnino?\\b|tax.?id|secret|token|api.?key|private.?key';
const PRIVATE_FIELD_REFUSAL = 'a password or payment field on this page has something typed in it, so Arty will not take a picture of it. Ask the user to look, or read the page instead';
const UNPAIRED_TITLE = 'Arty for Chrome: not paired yet. Click here and paste the pairing code from Arty Desktop (tray menu, Chrome sites).';
const PAIRED_TITLE = 'Arty for Chrome';

const SERVICE_PROOF_CONTEXT = 'arty-browser-service:';
const RANDOM_ID_BYTES = 16;
const BROWSER_ID = /^[0-9a-f]{32}$/;
const OPENABLE_SCHEMES = ['http:', 'https:'];

const FIRST_ITEM_NUMBER = 1;
const ITEM_NUMBER_LIMIT = 100000;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
let polling = false;
let nextItemNumber = FIRST_ITEM_NUMBER;

async function poll() {
  if (polling) return;
  polling = true;
  try {
    for (;;) {
      // Any extension API call keeps this service worker alive between long polls.
      await chrome.runtime.getPlatformInfo();
      const pairing = await pairingCode();
      if (!pairing) {
        await showPaired(false);
        await sleep(OFFLINE_RETRY_MS);
        continue;
      }
      let command;
      try {
        if (!(await serviceHoldsCode(pairing))) {
          await showPaired(false);
          await sleep(OFFLINE_RETRY_MS);
          continue;
        }
        const browser = await browserId();
        const response = await fetch(`${SERVICE}/browser/next?timeout=${POLL_SECONDS}&browser_id=${browser}`, { method: 'POST', headers: { 'X-Arty-Pairing': pairing } });
        await showPaired(response.status !== 403);
        if (!response.ok) throw new Error(`HTTP ${response.status}`); // refused or failing: don't spin
        command = await response.json();
      } catch {
        await sleep(OFFLINE_RETRY_MS); // Arty Desktop isn't running, or isn't letting us in
        continue;
      }
      if (!command.id) continue;
      let result;
      try {
        result = await run(command);
      } catch (error) {
        result = { error: String(error?.message || error) };
      }
      await sendResult(pairing, command.id, result);
    }
  } finally {
    polling = false;
  }
}

async function sendResult(pairing, id, result) {
  const request = {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Arty-Pairing': pairing }, body: JSON.stringify({ id, result }),
  };
  for (let attempt = 1; attempt <= RESULT_SEND_TRIES; attempt += 1) {
    try {
      const response = await fetch(`${SERVICE}/browser/result`, request);
      if (response.ok) return;
    } catch {
      if (attempt === RESULT_SEND_TRIES) return;
    }
    await sleep(RESULT_RETRY_MS);
  }
}

const hex = (bytes) => [...bytes].map((byte) => byte.toString(16).padStart(2, '0')).join('');

function randomId() {
  return hex(crypto.getRandomValues(new Uint8Array(RANDOM_ID_BYTES)));
}

async function proofFor(pairing, nonce) {
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey('raw', encoder.encode(pairing), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  return hex(new Uint8Array(await crypto.subtle.sign('HMAC', key, encoder.encode(`${SERVICE_PROOF_CONTEXT}${nonce}`))));
}

async function serviceHoldsCode(pairing) {
  const nonce = randomId();
  const response = await fetch(`${SERVICE}/browser/hello?nonce=${nonce}`, { method: 'POST' });
  if (!response.ok) return false;
  const { proof } = await response.json();
  return typeof proof === 'string' && proof === await proofFor(pairing, nonce);
}

async function browserId() {
  const stored = await chrome.storage.local.get('browserId');
  if (typeof stored.browserId === 'string' && BROWSER_ID.test(stored.browserId)) return stored.browserId;
  const made = randomId();
  await chrome.storage.local.set({ browserId: made });
  return made;
}

async function pairingCode() {
  const stored = await chrome.storage.local.get('pairing');
  return typeof stored.pairing === 'string' ? stored.pairing.trim() : '';
}

async function showPaired(paired) {
  await chrome.action.setBadgeText({ text: paired ? '' : '!' });
  await chrome.action.setTitle({ title: paired ? PAIRED_TITLE : UNPAIRED_TITLE });
}

chrome.alarms.create('arty-keepalive', { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener(poll);
chrome.runtime.onStartup.addListener(poll);
chrome.runtime.onInstalled.addListener(poll);
poll();

// ---------- commands ----------

function siteOf(address) {
  let parsed;
  try {
    parsed = new URL(address);
  } catch {
    return '';
  }
  if (!parsed.hostname) return '';
  const site = parsed.port ? `${parsed.hostname}:${parsed.port}` : parsed.hostname;
  return parsed.protocol === 'https:' ? site : `${parsed.protocol}//${site}`;
}

function refuseIfMoved(command, tab) {
  if (command.site === undefined) return;
  const now = siteOf(tab.url || '');
  if (now !== command.site) throw new Error(`that tab is on ${now || 'another page'} now, not ${command.site || 'the page Arty checked'}`);
}

async function frontTab() {
  const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  if (!tab) throw new Error('no browser tab is open');
  return tab;
}

async function openTab(tabId) {
  return chrome.tabs.get(tabId).catch(() => null);
}

async function targetTab(tabId) {
  if (!tabId) return frontTab();
  const tab = await openTab(tabId);
  if (!tab) throw new Error(`there is no tab ${tabId} open; leave the tab out for the one in front`);
  return tab;
}

async function tabToLookAt(tabId) {
  const tab = tabId ? await openTab(tabId) : null;
  if (tab || !tabId) return { tab: tab || await frontTab() };
  return { tab: await frontTab(), missingTab: tabId };
}

function waitForLoad(tabId) {
  return new Promise((resolve) => {
    const done = () => { chrome.tabs.onUpdated.removeListener(listener); clearTimeout(timer); resolve(); };
    const listener = (id, change) => { if (id === tabId && change.status === 'complete') done(); };
    const timer = setTimeout(done, LOAD_WAIT_MS);
    chrome.tabs.onUpdated.addListener(listener);
  });
}

async function inPage(tab, func, args = []) {
  const where = (tab.url || '').split('/')[2] || tab.url || 'this tab';
  let injection;
  try {
    [injection] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func, args });
  } catch (error) {
    throw new Error(`can't work in this page (${where}): ${error.message}`);
  }
  if (!injection) throw new Error(`can't work in this page (${where}): the browser ran nothing there`);
  if (injection.error) throw new Error(`can't work in this page (${where}): ${injection.error.message || injection.error}`);
  return injection.result;
}

async function privateFieldFilledInAnyFrame(tab) {
  let frames;
  try {
    frames = await chrome.scripting.executeScript({ target: { tabId: tab.id, allFrames: true }, func: privateFieldFilled, args: [PRIVATE_FIELD_HINTS] });
  } catch (error) {
    throw new Error(`can't work in this page (${(tab.url || '').split('/')[2] || 'this tab'}): ${error.message}`);
  }
  if (!frames?.length) throw new Error("can't work in this page: the browser ran nothing there");
  return frames.some((frame) => frame.result === true);
}

function refuseUnlessWebAddress(address) {
  let parsed = null;
  try {
    parsed = new URL(address);
  } catch {
    parsed = null;
  }
  if (!parsed || !OPENABLE_SCHEMES.includes(parsed.protocol)) throw new Error('Arty only opens http and https addresses');
}

async function screenshot(tab) {
  if (await privateFieldFilledInAnyFrame(tab)) return { error: PRIVATE_FIELD_REFUSAL };
  await chrome.tabs.update(tab.id, { active: true });
  const image = await chrome.tabs.captureVisibleTab(tab.windowId, { format: 'jpeg', quality: SCREENSHOT_QUALITY });
  return { tab: tab.id, title: tab.title || '', url: tab.url || '', image };
}

function numberAfter(page) {
  const last = Math.max(nextItemNumber - 1, ...(page?.items || []).map((item) => item.n));
  return last + 1 >= ITEM_NUMBER_LIMIT ? FIRST_ITEM_NUMBER : last + 1;
}

async function playersAfterClick(tab) {
  try {
    return { media: await inPage(tab, playersOnPage) };
  } catch (error) {
    return { mediaError: String(error?.message || error) };
  }
}

async function run(command) {
  switch (command.action) {
    case 'tabs': {
      const [front] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
      const tabs = await chrome.tabs.query({});
      return { tabs: tabs.map((t) => ({ id: t.id, title: t.title || '', url: t.url || '', active: t.id === front?.id })) };
    }
    case 'open': {
      refuseUnlessWebAddress(command.url);
      let tab;
      if (command.newTab) tab = await chrome.tabs.create({ url: command.url, active: true });
      else tab = await chrome.tabs.update((await targetTab()).id, { url: command.url, active: true });
      await chrome.windows.update(tab.windowId, { focused: true });
      await waitForLoad(tab.id);
      tab = await chrome.tabs.get(tab.id);
      return { tab: tab.id, title: tab.title, url: tab.url };
    }
    case 'read': {
      const { tab, missingTab } = await tabToLookAt(command.tab);
      await inPage(tab, waitForQuiet, [QUIET_MS, SETTLE_LIMIT_MS]);
      const page = await inPage(tab, readPage, [PRIVATE_FIELD_HINTS, nextItemNumber]);
      nextItemNumber = numberAfter(page);
      return { tab: tab.id, missingTab, ...page, media: await inPage(tab, playersOnPage) };
    }
    case 'click': {
      const tab = await targetTab(command.tab);
      refuseIfMoved(command, tab);
      const clicked = await inPage(tab, clickItem, [command.item ?? null, command.text || '', command.expected ?? null, command.acts ?? null]);
      if (clicked.error) return clicked;
      await sleep(AFTER_CLICK_MS);
      const after = await chrome.tabs.get(tab.id);
      return { clicked: clicked.label, title: after.title, url: after.url, ...(await playersAfterClick(after)) };
    }
    case 'type': {
      const tab = await targetTab(command.tab);
      refuseIfMoved(command, tab);
      return inPage(tab, typeInto, [command.item ?? null, command.label || '', command.text || '', !!command.submit, command.expected ?? null, PRIVATE_FIELD_HINTS]);
    }
    case 'scroll': {
      const { tab, missingTab } = await tabToLookAt(command.tab);
      refuseIfMoved(command, tab);
      const view = await inPage(tab, scrollPage, [command.direction || 'down', SCROLL_TEXT_CHARS]);
      return { tab: tab.id, missingTab, title: tab.title || '', url: tab.url || '', ...view };
    }
    case 'screenshot': {
      const { tab, missingTab } = await tabToLookAt(command.tab);
      refuseIfMoved(command, tab);
      return { missingTab, ...(await screenshot(tab)) };
    }
    case 'tab': {
      const tab = await targetTab(command.tab);
      refuseIfMoved(command, tab);
      if (command.tabAction === 'switch') {
        await chrome.tabs.update(tab.id, { active: true });
        await chrome.windows.update(tab.windowId, { focused: true });
      } else if (command.tabAction === 'close') await chrome.tabs.remove(tab.id);
      else if (command.tabAction === 'back') await chrome.tabs.goBack(tab.id);
      else if (command.tabAction === 'forward') await chrome.tabs.goForward(tab.id);
      else if (command.tabAction === 'reload') await chrome.tabs.reload(tab.id);
      if (command.tabAction === 'close') return { title: '', url: '' };
      const after = await chrome.tabs.get(tab.id);
      return { title: after.title || '', url: after.url || '' };
    }
    default:
      throw new Error(`unknown command ${command.action}`);
  }
}

// ---------- these run inside the page ----------

function readPage(privateHints, firstNumber = 1) {
  const MIN_TEXT_PX = 4;
  const LABEL_CHARS = 400;
  const hiddenBox = (el) => {
    if (el.closest?.('[aria-hidden="true"]')) return true;
    if (el.checkVisibility && !el.checkVisibility({ opacityProperty: true, visibilityProperty: true })) return true;
    const box = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    if (!(box.width > 1 && box.height > 1)) return true;
    if (box.right + (globalThis.scrollX || 0) <= 0 || box.bottom + (globalThis.scrollY || 0) <= 0) return true;
    return style.visibility === 'hidden' || style.display === 'none';
  };
  const visible = (el) => !hiddenBox(el);
  const rendered = (el) => !el.checkVisibility || el.checkVisibility({ visibilityProperty: true });
  const textHidden = (el) => rendered(el) && (hiddenBox(el) || parseFloat(getComputedStyle(el).fontSize) < MIN_TEXT_PX);
  const clean = (text) => String(text || '').trim().replace(/\s+/g, ' ').slice(0, LABEL_CHARS);
  const labelText = (el) => [...(el.labels || [])].map((label) => label.innerText).join(' ');
  const fieldName = (el) => clean(labelText(el) || el.getAttribute('aria-label') || el.placeholder || el.name || el.id);
  const shownWords = (el) => clean(el.innerText) || clean(labelText(el)) || clean(el.value) || clean(el.title);
  const privateHint = new RegExp(privateHints, 'i');
  const isPrivateField = (el) => {
    const labelledBy = (el.getAttribute('aria-labelledby') || '').split(/\s+/).map((id) => (id && document.getElementById?.(id)?.innerText) || '');
    const names = [el.getAttribute('autocomplete'), el.name, el.id, el.getAttribute('aria-label'), el.placeholder, labelText(el), ...labelledBy];
    return (el.getAttribute('type') || '').toLowerCase() === 'password' || privateHint.test(names.join(' '));
  };
  const DOWNLOAD_FILE = /\.(exe|msi|msix|dmg|pkg|deb|rpm|apk|appimage|run|sh|bat|cmd|ps1|vbs|jar|zip|tar|gz|tgz|xz|bz2|7z|rar|iso|img)$/i;
  const ACCOUNT_OR_PAYMENT = /(^|[^a-z])(pay|payments?|checkout|billing|purchase|orders?|basket|cart|authori[sz]e|oauth2?|consent|permissions?|grant|account|subscri\w*|unsubscri\w*|delete|remove|log-?out|sign-?out|transfer|donate)([^a-z]|$)/i;
  const addressOf = (target) => {
    try {
      return target ? new URL(target, location.href) : null;
    } catch {
      return null;
    }
  };
  const actsOf = (el) => {
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    const submits = el.getAttribute('formaction') !== null || (tag === 'button' && !!el.form && (type || 'submit') === 'submit')
      || (tag === 'input' && (type === 'submit' || type === 'image'));
    const target = addressOf(submits ? el.getAttribute('formaction') || el.form?.getAttribute('action') : tag === 'a' ? el.getAttribute('href') : '');
    if (tag === 'a' && (el.getAttribute('download') !== null || DOWNLOAD_FILE.test(target?.pathname || ''))) return { acts: 'downloads a file' };
    if (target && ACCOUNT_OR_PAYMENT.test(`${target.pathname}${target.search}`)) return { acts: 'payment or account' };
    if (submits) return { acts: 'submits a form' };
    const leaves = tag === 'a' && !!target && ['http:', 'https:'].includes(target.protocol) && target.host !== location.host;
    return leaves ? { acts: 'leaves the site', goes: target.href.slice(0, LABEL_CHARS) } : { acts: '' };
  };
  const MAX_ITEMS = 250;
  const MIN_MAIN_TEXT = 200;
  const MENU_TAGS = ['nav', 'aside'];
  const MENU_ROLES = ['navigation', 'banner', 'contentinfo', 'complementary', 'menubar'];
  const CONTENT_TAGS = ['article', 'main', 'section'];
  const CONTENT_ROLES = ['main', 'article'];
  const DIALOG_ROLES = ['dialog', 'alertdialog'];
  const AD_MARKER = /(^|[-_\s])(ad|ads|advert|advertisement|sponsored|promoted)([-_\s]|$)/i;
  const AD_LABEL = /^(ad|ads|advertisement|sponsored)$/i;
  const LINK_DESTINATIONS = [
    [/[/](shorts|reels?)[/]/, 'short video'], [/[/]watch\b|[/]videos?[/]\w/, 'video'],
    [/[/]playlist\b/, 'playlist'], [/[/](channel|c|user)[/]|[/]@/, 'channel'],
  ];
  const HEADING_TAG = /^h[1-3]$/;
  const SKIPPED_TEXT_TAGS = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE']);
  const destinationOf = (href) => (LINK_DESTINATIONS.find(([pattern]) => pattern.test(href)) || [null, ''])[1];
  const looksLikeAd = (el) => AD_MARKER.test(el.tagName) || AD_MARKER.test(el.id || '')
    || AD_MARKER.test(typeof el.className === 'string' ? el.className : '') || AD_LABEL.test(el.getAttribute('aria-label') || '');
  const surroundings = (el) => {
    const found = { dialog: false, ad: false, menu: false, heading: false };
    let pageEdge = false;
    for (let around = el; around && around.tagName; around = around.parentElement) {
      const tag = around.tagName.toLowerCase();
      const role = around.getAttribute('role') || '';
      if (tag === 'dialog' || DIALOG_ROLES.includes(role) || around.getAttribute('aria-modal') === 'true') found.dialog = true;
      if (looksLikeAd(around)) found.ad = true;
      if (MENU_TAGS.includes(tag) || MENU_ROLES.includes(role)) found.menu = true;
      if (HEADING_TAG.test(tag) || role === 'heading') found.heading = true;
      if (tag === 'header' || tag === 'footer') pageEdge = true;
      else if (CONTENT_TAGS.includes(tag) || CONTENT_ROLES.includes(role)) pageEdge = false;
    }
    found.menu = found.menu || pageEdge;
    return found;
  };
  const resultLinks = new Set();
  const groupOf = (el, kind) => {
    const found = surroundings(el);
    if (found.dialog) return 'dialog';
    if (found.ad) return 'ad';
    if (found.menu) return 'menu';
    const href = kind === 'link' ? el.getAttribute('href') || '' : '';
    if (destinationOf(href) === 'short video') return 'short';
    const isHeadingLink = kind === 'link' && (found.heading || !!el.querySelector('h1, h2, h3, [role=heading]'));
    if (!isHeadingLink || resultLinks.has(href)) return 'main';
    resultLinks.add(href);
    return 'result';
  };
  const hiddenWords = (root) => {
    if (!root || typeof document.createTreeWalker !== 'function') return [];
    const hidden = [];
    const checked = new Map();
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const parent = node.parentElement;
      if (!parent || SKIPPED_TEXT_TAGS.has(parent.tagName) || !String(node.textContent || '').trim()) continue;
      if (!checked.has(parent)) checked.set(parent, textHidden(parent));
      if (checked.get(parent) && !hidden.includes(parent)) hidden.push(parent);
    }
    const outermost = hidden.filter((el) => !hidden.some((other) => other !== el && other.contains(el)));
    return outermost.map((el) => String(el.innerText || '').trim()).filter(Boolean);
  };
  const withoutHiddenWords = (text, root) => hiddenWords(root).reduce((kept, words) => {
    const at = kept.indexOf(words);
    return at < 0 ? kept : kept.slice(0, at) + kept.slice(at + words.length);
  }, text);
  document.querySelectorAll('[data-arty-n]').forEach((el) => el.removeAttribute('data-arty-n'));
  const items = [];
  const selector = 'a[href], button, input, textarea, select, [role=button], [role=link], [contenteditable=true]';
  for (const el of document.querySelectorAll(selector)) {
    if (items.length >= MAX_ITEMS || !visible(el)) continue;
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    if (type === 'hidden') continue;
    const isField = ['input', 'textarea', 'select'].includes(tag) && !['button', 'submit', 'reset', 'checkbox', 'radio', 'image'].includes(type) || el.isContentEditable;
    const isPrivate = isField && isPrivateField(el);
    const kind = tag === 'a' || el.getAttribute('role') === 'link' ? 'link' : isField ? (isPrivate ? 'private field' : 'field') : 'button';
    const words = isField ? fieldName(el) : shownWords(el);
    const aria = clean(el.getAttribute('aria-label'));
    const label = words || aria;
    const also = !isField && words && aria && aria.toLowerCase() !== words.toLowerCase() ? aria : '';
    if (!label && kind === 'link') continue;
    const n = firstNumber + items.length;
    el.setAttribute('data-arty-n', String(n));
    const to = kind === 'link' ? destinationOf(el.getAttribute('href') || '') : '';
    const value = isField && el.value && !isPrivate ? String(el.value).slice(0, 40) : '';
    items.push({ n, kind, label: label || '(unlabelled)', also, value, group: groupOf(el, kind), to, ...(isField ? { acts: '' } : actsOf(el)) });
  }
  const main = document.querySelector('main, [role=main]');
  const mainText = main && visible(main) ? main.innerText || '' : '';
  const root = mainText.trim().length >= MIN_MAIN_TEXT ? main : document.body;
  const text = withoutHiddenWords(root === main ? mainText : document.body?.innerText || '', root);
  return { title: document.title, url: location.href, text: text.replace(/\n{3,}/g, '\n\n').slice(0, 8000), items };
}

function playersOnPage() {
  return [...document.querySelectorAll('video, audio')]
    .filter((player) => player.tagName === 'VIDEO' || player.tagName === 'AUDIO')
    .slice(0, 3)
    .map((player) => ({
      kind: player.tagName.toLowerCase(),
      playing: !player.paused && !player.ended && player.readyState > 2,
      starting: !player.paused && !player.ended && player.readyState <= 2,
      muted: !!player.muted,
      at: Math.round(player.currentTime || 0),
      length: Number.isFinite(player.duration) ? Math.round(player.duration) : null,
    }));
}

function waitForQuiet(quietMs, limitMs) {
  return new Promise((resolve) => {
    let quietTimer = null;
    let limitTimer = null;
    let observer = null;
    const finish = () => {
      observer.disconnect();
      clearTimeout(quietTimer);
      clearTimeout(limitTimer);
      resolve(true);
    };
    observer = new MutationObserver(() => {
      clearTimeout(quietTimer);
      quietTimer = setTimeout(finish, quietMs);
    });
    observer.observe(document.documentElement, { childList: true, subtree: true });
    quietTimer = setTimeout(finish, quietMs);
    limitTimer = setTimeout(finish, limitMs);
  });
}

function privateFieldFilled(privateHints) {
  const hiddenBox = (el) => {
    if (el.checkVisibility && !el.checkVisibility({ opacityProperty: true, visibilityProperty: true })) return true;
    const box = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return !(box.width > 1 && box.height > 1) || style.visibility === 'hidden' || style.display === 'none';
  };
  const labelText = (el) => [...(el.labels || [])].map((label) => label.innerText).join(' ');
  const privateHint = new RegExp(privateHints, 'i');
  const isPrivateField = (el) => {
    const labelledBy = (el.getAttribute('aria-labelledby') || '').split(/\s+/).map((id) => (id && document.getElementById?.(id)?.innerText) || '');
    const names = [el.getAttribute('autocomplete'), el.name, el.id, el.getAttribute('aria-label'), el.placeholder, labelText(el), ...labelledBy];
    return (el.getAttribute('type') || '').toLowerCase() === 'password' || privateHint.test(names.join(' '));
  };
  for (const el of document.querySelectorAll('input, textarea')) {
    if (!el.value || (el.getAttribute('type') || '').toLowerCase() === 'hidden' || hiddenBox(el)) continue;
    if (isPrivateField(el)) return true;
  }
  return false;
}

function scrollPage(direction, textLimit) {
  const before = window.scrollY;
  const step = Math.round(window.innerHeight * 0.85);
  if (direction === 'top') window.scrollTo(0, 0);
  else if (direction === 'bottom') window.scrollTo(0, document.documentElement.scrollHeight);
  else window.scrollBy(0, direction === 'up' ? -step : step);
  const height = Math.max(document.documentElement.scrollHeight, document.body?.scrollHeight || 0);
  const blockTags = 'p, li, h1, h2, h3, h4, h5, h6, td, th, dt, dd, blockquote, pre, figcaption, caption, div';
  const lines = [];
  let length = 0;
  let lastBlock = null;
  const MIN_TEXT_PX = 4;
  const unseen = (el) => !!el.closest?.('[aria-hidden="true"]')
    || (el.checkVisibility ? !el.checkVisibility({ opacityProperty: true, visibilityProperty: true }) : false)
    || parseFloat(getComputedStyle(el).fontSize) < MIN_TEXT_PX;
  const walker = document.createTreeWalker(document.body || document.documentElement, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node && length < textLimit; node = walker.nextNode()) {
    const text = node.textContent.trim().replace(/\s+/g, ' ');
    const box = text ? node.parentElement?.getBoundingClientRect() : null;
    if (!box || box.width === 0 || box.bottom < 0 || box.top > window.innerHeight || box.right <= 0) continue;
    if (unseen(node.parentElement)) continue;
    const block = node.parentElement.closest(blockTags);
    if (lines.length && block === lastBlock) lines[lines.length - 1] += ` ${text}`;
    else lines.push(text);
    lastBlock = block;
    length += text.length + 1;
  }
  return {
    moved: window.scrollY !== before,
    top: window.scrollY <= 0,
    bottom: window.scrollY + window.innerHeight >= height - 2,
    text: lines.join('\n').slice(0, textLimit),
  };
}

function clickItem(n, text, expected = null, expectedActs = null) {
  const LABEL_CHARS = 400;
  const STALE = `item ${n} is not on the page any more (the page changed since it was read); read it again`;
  const hiddenBox = (el) => {
    if (el.closest?.('[aria-hidden="true"]')) return true;
    if (el.checkVisibility && !el.checkVisibility({ opacityProperty: true, visibilityProperty: true })) return true;
    const box = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    if (!(box.width > 1 && box.height > 1)) return true;
    if (box.right + (globalThis.scrollX || 0) <= 0 || box.bottom + (globalThis.scrollY || 0) <= 0) return true;
    return style.visibility === 'hidden' || style.display === 'none';
  };
  const clean = (words) => String(words || '').trim().replace(/\s+/g, ' ').slice(0, LABEL_CHARS);
  const same = (words) => clean(words).toLowerCase();
  const labelText = (el) => [...(el.labels || [])].map((label) => label.innerText).join(' ');
  const fieldName = (el) => clean(labelText(el) || el.getAttribute('aria-label') || el.placeholder || el.name || el.id);
  const shownWords = (el) => clean(el.innerText) || clean(labelText(el)) || clean(el.value) || clean(el.title);
  const isField = (el) => {
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    return ['input', 'textarea', 'select'].includes(tag) && !['button', 'submit', 'reset', 'checkbox', 'radio', 'image'].includes(type) || el.isContentEditable;
  };
  const labelOf = (el) => (isField(el) ? fieldName(el) : shownWords(el) || clean(el.getAttribute('aria-label')));
  const namesOf = (el) => [labelOf(el), clean(el.getAttribute('aria-label'))].filter(Boolean).map(same);
  const DOWNLOAD_FILE = /\.(exe|msi|msix|dmg|pkg|deb|rpm|apk|appimage|run|sh|bat|cmd|ps1|vbs|jar|zip|tar|gz|tgz|xz|bz2|7z|rar|iso|img)$/i;
  const ACCOUNT_OR_PAYMENT = /(^|[^a-z])(pay|payments?|checkout|billing|purchase|orders?|basket|cart|authori[sz]e|oauth2?|consent|permissions?|grant|account|subscri\w*|unsubscri\w*|delete|remove|log-?out|sign-?out|transfer|donate)([^a-z]|$)/i;
  const addressOf = (target) => {
    try {
      return target ? new URL(target, location.href) : null;
    } catch {
      return null;
    }
  };
  const actsOf = (el) => {
    if (isField(el)) return '';
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    const submits = el.getAttribute('formaction') !== null || (tag === 'button' && !!el.form && (type || 'submit') === 'submit')
      || (tag === 'input' && (type === 'submit' || type === 'image'));
    const target = addressOf(submits ? el.getAttribute('formaction') || el.form?.getAttribute('action') : tag === 'a' ? el.getAttribute('href') : '');
    if (tag === 'a' && (el.getAttribute('download') !== null || DOWNLOAD_FILE.test(target?.pathname || ''))) return 'downloads a file';
    if (target && ACCOUNT_OR_PAYMENT.test(`${target.pathname}${target.search}`)) return 'payment or account';
    if (submits) return 'submits a form';
    const leaves = tag === 'a' && !!target && ['http:', 'https:'].includes(target.protocol) && target.host !== location.host;
    return leaves ? 'leaves the site' : '';
  };
  let el;
  if (n !== null) {
    el = document.querySelector(`[data-arty-n="${n}"]`);
    if (!el || hiddenBox(el)) return { error: STALE };
    if (expected !== null && same(labelOf(el)) !== same(expected)) {
      return { error: `item ${n} no longer has the label Arty read (the page changed since it was read); read it again` };
    }
  } else {
    const wanted = same(text);
    if (!wanted) return { error: 'say what to click: its number from chrome_read, or its whole label' };
    const selector = 'a[href], button, input, textarea, select, [role=button], [role=link], [contenteditable=true]';
    const candidates = [...document.querySelectorAll(selector)].filter((candidate) => !isField(candidate) && !hiddenBox(candidate));
    el = candidates.find((candidate) => namesOf(candidate).includes(wanted));
    if (!el) return { error: 'nothing on the page has exactly that label; read the page and click by number' };
  }
  if (expectedActs !== null && actsOf(el) !== expectedActs) {
    return { error: 'that item does something different from when the page was read; read it again' };
  }
  el.scrollIntoView({ block: 'center' });
  el.click();
  return { label: labelOf(el).slice(0, 80) || 'it' };
}

function typeInto(n, label, text, submit, expected, privateHints) {
  const hiddenBox = (el) => {
    if (el.checkVisibility && !el.checkVisibility({ opacityProperty: true, visibilityProperty: true })) return true;
    const box = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return !(box.width > 1 && box.height > 1) || style.visibility === 'hidden' || style.display === 'none';
  };
  const clean = (words) => String(words || '').trim().replace(/\s+/g, ' ');
  const same = (words) => clean(words).toLowerCase();
  const labelText = (el) => [...(el.labels || [])].map((labelled) => labelled.innerText).join(' ');
  const fieldName = (el) => clean(labelText(el) || el.getAttribute('aria-label') || el.placeholder || el.name || el.id);
  const privateHint = new RegExp(privateHints, 'i');
  const isPrivateField = (el) => {
    const labelledBy = (el.getAttribute('aria-labelledby') || '').split(/\s+/).map((id) => (id && document.getElementById?.(id)?.innerText) || '');
    const names = [el.getAttribute('autocomplete'), el.name, el.id, el.getAttribute('aria-label'), el.placeholder, labelText(el), ...labelledBy];
    return (el.getAttribute('type') || '').toLowerCase() === 'password' || privateHint.test(names.join(' '));
  };
  let el = null;
  if (n !== null) {
    el = document.querySelector(`[data-arty-n="${n}"]`);
    if (!el || hiddenBox(el)) return { error: `item ${n} is not on the page any more (the page changed since it was read); read it again` };
    if (expected !== null && same(fieldName(el)).slice(0, 400) !== same(expected).slice(0, 400)) {
      return { error: `item ${n} no longer has the label Arty read (the page changed since it was read); read it again` };
    }
  } else if (label) {
    const wanted = same(label);
    const fields = [...document.querySelectorAll('input, textarea, select, [contenteditable=true]')]
      .filter((field) => (field.getAttribute('type') || '').toLowerCase() !== 'hidden' && !hiddenBox(field));
    el = fields.find((field) => same(fieldName(field)) === wanted) || fields.find((field) => same(fieldName(field)).includes(wanted)) || null;
    if (!el) return { error: 'no field has that label (read the page and give its number)' };
  } else {
    el = document.activeElement !== document.body ? document.activeElement : null;
  }
  if (!el) return { error: 'no field to type into (read the page and give its number)' };
  if (isPrivateField(el)) {
    return { error: 'that is a password, code or payment field; Arty never types into those. Ask the user to fill it in' };
  }
  el.focus();
  if (el.isContentEditable) {
    document.execCommand('insertText', false, text);
  } else {
    // Through the native setter, so pages built with React and the like notice the change.
    const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : el.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, text);
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  }
  if (submit) {
    if (el.form) el.form.requestSubmit();
    else for (const type of ['keydown', 'keypress', 'keyup']) el.dispatchEvent(new KeyboardEvent(type, { key: 'Enter', code: 'Enter', keyCode: 13, bubbles: true }));
  }
  return { field: fieldName(el).slice(0, 60) || 'the field' };
}
