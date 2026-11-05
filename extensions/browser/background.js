'use strict';
// Arty for Chrome: takes browser commands from Arty Desktop's local service and runs them here.
// The service only listens on this computer; see server/process/browser.py for the commands.

const SERVICE = 'http://127.0.0.1:8765';
const POLL_SECONDS = 25;
const OFFLINE_RETRY_MS = 5000;
const LOAD_WAIT_MS = 15000;
const AFTER_CLICK_MS = 1500;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
let polling = false;

async function poll() {
  if (polling) return;
  polling = true;
  try {
    for (;;) {
      // Any extension API call keeps this service worker alive between long polls.
      await chrome.runtime.getPlatformInfo();
      let command;
      try {
        const response = await fetch(`${SERVICE}/browser/next?timeout=${POLL_SECONDS}`);
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
      await fetch(`${SERVICE}/browser/result`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: command.id, result }),
      }).catch(() => {});
    }
  } finally {
    polling = false;
  }
}

chrome.alarms.create('arty-keepalive', { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener(poll);
chrome.runtime.onStartup.addListener(poll);
chrome.runtime.onInstalled.addListener(poll);
poll();

// ---------- commands ----------

async function targetTab(tabId) {
  if (tabId) return chrome.tabs.get(tabId);
  const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  if (!tab) throw new Error('no Chrome tab is open');
  return tab;
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
  try {
    const [{ result }] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func, args });
    return result;
  } catch (error) {
    throw new Error(`can't work in this page (${tab.url.split('/')[2] || tab.url}): ${error.message}`);
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
      const tab = await targetTab(command.tab);
      return { tab: tab.id, ...(await inPage(tab, readPage)) };
    }
    case 'click': {
      const tab = await targetTab(command.tab);
      const clicked = await inPage(tab, clickItem, [command.item ?? null, command.text || '']);
      if (clicked.error) return clicked;
      await sleep(AFTER_CLICK_MS);
      const after = await chrome.tabs.get(tab.id);
      return { clicked: clicked.label, title: after.title, url: after.url };
    }
    case 'type': {
      const tab = await targetTab(command.tab);
      return inPage(tab, typeInto, [command.item ?? null, command.label || '', command.text || '', !!command.submit]);
    }
    case 'tab': {
      const tab = await targetTab(command.tab);
      if (command.tabAction === 'switch') {
        await chrome.tabs.update(tab.id, { active: true });
        await chrome.windows.update(tab.windowId, { focused: true });
      } else if (command.tabAction === 'close') await chrome.tabs.remove(tab.id);
      else if (command.tabAction === 'back') await chrome.tabs.goBack(tab.id);
      else if (command.tabAction === 'forward') await chrome.tabs.goForward(tab.id);
      else if (command.tabAction === 'reload') await chrome.tabs.reload(tab.id);
      return { title: command.tabAction === 'close' ? '' : tab.title };
    }
    default:
      throw new Error(`unknown command ${command.action}`);
  }
}

// ---------- these run inside the page ----------

function readPage() {
  const visible = (el) => {
    const box = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return box.width > 0 && box.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
  };
  const clean = (text) => String(text || '').trim().replace(/\s+/g, ' ').slice(0, 80);
  document.querySelectorAll('[data-arty-n]').forEach((el) => el.removeAttribute('data-arty-n'));
  const items = [];
  const selector = 'a[href], button, input, textarea, select, [role=button], [role=link], [contenteditable=true]';
  for (const el of document.querySelectorAll(selector)) {
    if (items.length >= 150 || !visible(el)) continue;
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    if (type === 'hidden') continue;
    const isField = ['input', 'textarea', 'select'].includes(tag) && !['button', 'submit', 'reset', 'checkbox', 'radio'].includes(type) || el.isContentEditable;
    const kind = tag === 'a' || el.getAttribute('role') === 'link' ? 'link' : isField ? (type === 'password' ? 'password field' : 'field') : 'button';
    let label;
    if (isField) {
      label = clean(el.labels?.[0]?.innerText || el.getAttribute('aria-label') || el.placeholder || el.name || el.id);
      if (el.value && type !== 'password') label += ` (now: "${String(el.value).slice(0, 40)}")`;
    } else {
      label = clean(el.getAttribute('aria-label') || el.innerText || el.value || el.title);
    }
    if (!label && kind === 'link') continue;
    const n = items.length + 1;
    el.setAttribute('data-arty-n', String(n));
    items.push({ n, kind, label: label || '(unlabelled)' });
  }
  return { title: document.title, url: location.href, text: (document.body?.innerText || '').replace(/\n{3,}/g, '\n\n').slice(0, 8000), items };
}

function clickItem(n, text) {
  let el = n !== null ? document.querySelector(`[data-arty-n="${n}"]`) : null;
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
