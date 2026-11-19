'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const appFolder = path.join(__dirname, '..');
const read = (...parts) => fs.readFileSync(path.join(appFolder, ...parts), 'utf8');

test('the window has a Link starfire panel that starts hidden, with a button to make a code and one to unlink', () => {
  const page = read('renderer', 'index.html');
  assert.match(page, /<section id="starfire-link"[^>]*\bhidden\b/);
  for (const id of ['link-make', 'link-unlink', 'link-close', 'link-code', 'link-state']) {
    assert.match(page, new RegExp(`id="${id}"`));
  }
});

test('the tray menu opens the panel and the preload carries the message', () => {
  assert.match(read('main.js'), /label: 'Link starfire…'[^\n]*send\('starfire-link'\)/);
  assert.match(read('preload.js'), /onStarfireLink: \(callback\) => ipcRenderer\.on\('starfire-link'/);
});

test('the panel talks only to the link routes of the local service', () => {
  const script = read('renderer', 'app.js');
  for (const route of ['/link/pairing/start', '/link/unlink', "api('/link')"]) {
    assert.ok(script.includes(route), route);
  }
});

test('a question from a linked app is shown even while presenting, and answered through /confirm', () => {
  const script = read('renderer', 'app.js');
  const start = script.indexOf('async function watchNotifications');
  const watcher = script.slice(start, script.indexOf('// ---------- calendar', start));
  const askAt = watcher.indexOf("'link_ask'");
  const presentingAt = watcher.indexOf('if (presentation.on) continue;');
  assert.ok(askAt > -1 && presentingAt > -1, 'both are in the watcher');
  assert.ok(askAt < presentingAt, 'the question is handled before presenting skips notes');
  assert.match(script, /function showLinkQuestion[\s\S]*linkQuestionCard[\s\S]*\/confirm/);
});

test('the code is shown in groups and wiped when it runs out', () => {
  const script = read('renderer', 'app.js');
  assert.match(script, /function hideStarfireLink[\s\S]*link-code'\)\.textContent = ''/);
  assert.match(script, /expires_in \* MILLISECONDS_PER_SECOND/);
});

test('the Link starfire panel is a full-panel view like the phone code', () => {
  const styles = read('renderer', 'style.css');
  assert.match(styles, /\.panel\.showing-link > :not\(\.bar\):not\(\.starfire-link\)/);
});
