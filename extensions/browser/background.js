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
const PRIVATE_FIELD_HINTS = 'pass|pwd|\\bpin\\b|one-time|otp|2fa|mfa|verif|security.?code|cc-|card|cvv|cvc|iban|sort.?code|account.?n|ssn|secret|token';
const PRIVATE_FIELD_REFUSAL = 'a password or payment field on this page has something typed in it, so Arty will not take a picture of it. Ask the user to look, or read the page instead';
const UNPAIRED_TITLE = 'Arty for Chrome: not paired yet. Click here and paste the pairing code from Arty Desktop (tray menu, Chrome sites).';
const PAIRED_TITLE = 'Arty for Chrome';

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
        const response = await fetch(`${SERVICE}/browser/next?timeout=${POLL_SECONDS}`, { method: 'POST', headers: { 'X-Arty-Pairing': pairing } });
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

async function screenshot(tab) {
  if (await inPage(tab, privateFieldFilled, [PRIVATE_FIELD_HINTS])) return { error: PRIVATE_FIELD_REFUSAL };
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
      const clicked = await inPage(tab, clickItem, [command.item ?? null, command.text || '']);
      if (clicked.error) return clicked;
      await sleep(AFTER_CLICK_MS);
      const after = await chrome.tabs.get(tab.id);
      return { clicked: clicked.label, title: after.title, url: after.url, ...(await playersAfterClick(after)) };
    }
    case 'type': {
      const tab = await targetTab(command.tab);
      refuseIfMoved(command, tab);
      return inPage(tab, typeInto, [command.item ?? null, command.label || '', command.text || '', !!command.submit]);
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
  const visible = (el) => {
    const box = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return box.width > 0 && box.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
  };
  const clean = (text) => String(text || '').trim().replace(/\s+/g, ' ').slice(0, 80);
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
  document.querySelectorAll('[data-arty-n]').forEach((el) => el.removeAttribute('data-arty-n'));
  const items = [];
  const selector = 'a[href], button, input, textarea, select, [role=button], [role=link], [contenteditable=true]';
  for (const el of document.querySelectorAll(selector)) {
    if (items.length >= MAX_ITEMS || !visible(el)) continue;
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    if (type === 'hidden') continue;
    const isField = ['input', 'textarea', 'select'].includes(tag) && !['button', 'submit', 'reset', 'checkbox', 'radio'].includes(type) || el.isContentEditable;
    const hints = `${el.getAttribute('autocomplete') || ''} ${el.name || ''} ${el.id || ''} ${el.getAttribute('aria-label') || ''} ${el.placeholder || ''}`.toLowerCase();
    const isPrivate = type === 'password' || new RegExp(privateHints).test(hints);
    const kind = tag === 'a' || el.getAttribute('role') === 'link' ? 'link' : isField ? (isPrivate ? 'private field' : 'field') : 'button';
    let label;
    if (isField) {
      label = clean(el.labels?.[0]?.innerText || el.getAttribute('aria-label') || el.placeholder || el.name || el.id);
      if (el.value && !isPrivate) label += ` (now: "${String(el.value).slice(0, 40)}")`;
    } else {
      label = clean(el.getAttribute('aria-label') || el.innerText || el.value || el.title);
    }
    if (!label && kind === 'link') continue;
    const n = firstNumber + items.length;
    el.setAttribute('data-arty-n', String(n));
    const to = kind === 'link' ? destinationOf(el.getAttribute('href') || '') : '';
    items.push({ n, kind, label: label || '(unlabelled)', group: groupOf(el, kind), to });
  }
  const main = document.querySelector('main, [role=main]');
  const mainText = main && visible(main) ? main.innerText || '' : '';
  const text = mainText.trim().length >= MIN_MAIN_TEXT ? mainText : document.body?.innerText || '';
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
  const privateHint = new RegExp(privateHints);
  for (const el of document.querySelectorAll('input, textarea')) {
    if (!el.value) continue;
    const type = (el.getAttribute('type') || '').toLowerCase();
    const hints = `${el.getAttribute('autocomplete') || ''} ${el.name || ''} ${el.id || ''} ${el.getAttribute('aria-label') || ''} ${el.placeholder || ''}`.toLowerCase();
    if (type === 'password' || privateHint.test(hints)) return true;
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
  const walker = document.createTreeWalker(document.body || document.documentElement, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node && length < textLimit; node = walker.nextNode()) {
    const text = node.textContent.trim().replace(/\s+/g, ' ');
    const box = text ? node.parentElement?.getBoundingClientRect() : null;
    if (!box || box.width === 0 || box.bottom < 0 || box.top > window.innerHeight) continue;
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

function clickItem(n, text) {
  let el = n !== null ? document.querySelector(`[data-arty-n="${n}"]`) : null;
  if (el) {
    const box = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    const shown = box.width > 0 && box.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
    if (!shown) return { error: `item ${n} is not on the page any more (the page changed since it was read); read it again` };
  }
  if (!el && text) {
    const wanted = text.trim().toLowerCase();
    const candidates = [...document.querySelectorAll('a[href], button, [role=button], [role=link], input[type=submit], input[type=button], summary, label')];
    const labelOf = (c) => (c.getAttribute('aria-label') || c.innerText || c.value || '').trim().toLowerCase();
    el = candidates.find((c) => labelOf(c) === wanted) || candidates.find((c) => labelOf(c).includes(wanted));
  }
  if (!el) return { error: `nothing to click matching ${n !== null ? `item ${n}` : `"${text}"`} (read the page again)` };
  el.scrollIntoView({ block: 'center' });
  el.click();
  return { label: (el.getAttribute('aria-label') || el.innerText || el.value || 'it').trim().slice(0, 80) };
}

function typeInto(n, label, text, submit) {
  let el = n !== null ? document.querySelector(`[data-arty-n="${n}"]`) : null;
  if (!el && label) {
    const wanted = label.trim().toLowerCase();
    const fields = [...document.querySelectorAll('input, textarea, select, [contenteditable=true]')];
    const labelOf = (f) => (f.labels?.[0]?.innerText || f.getAttribute('aria-label') || f.placeholder || f.name || '').trim().toLowerCase();
    el = fields.find((f) => labelOf(f) === wanted) || fields.find((f) => labelOf(f).includes(wanted));
  }
  if (!el) el = document.activeElement !== document.body ? document.activeElement : null;
  if (!el) return { error: 'no field to type into (read the page and give its number)' };
  const type = (el.getAttribute('type') || '').toLowerCase();
  const hints = `${el.getAttribute('autocomplete') || ''} ${el.name || ''} ${el.id || ''}`.toLowerCase();
  if (type === 'password' || /cc-|card|cvv|cvc|iban|sort.?code/.test(hints)) {
    return { error: 'that is a password or payment field; Arty never types into those. Ask the user to fill it in' };
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
  return { field: (el.labels?.[0]?.innerText || el.getAttribute('aria-label') || el.placeholder || el.name || 'the field').trim().slice(0, 60) };
}
