'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const { CONTROL_HOTKEY, followControl } = require('../control-indicator');

const main = fs.readFileSync(path.join(__dirname, '..', 'main.js'), 'utf8');
const page = fs.readFileSync(path.join(__dirname, '..', 'renderer', 'control.html'), 'utf8');
const packaged = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'package.json'), 'utf8')).build.files;

function scripted(states) {
  const seen = [];
  let index = 0;
  return {
    seen,
    deps: {
      fetchState: async (after) => {
        seen.push(['fetch', after]);
        const next = states[index++];
        if (next instanceof Error) throw next;
        return next;
      },
      postShown: async (session) => seen.push(['shown', session]),
      showIndicator: async (state) => seen.push(['show', state.session]),
      hideIndicator: () => seen.push(['hide']),
      onEnabled: (enabled) => seen.push(['enabled', enabled]),
      sleep: async () => seen.push(['sleep']),
      keepGoing: () => index < states.length,
    },
  };
}

test('the bar shows before Arty is told it is up, and goes when control ends', async () => {
  const run = scripted([
    { version: 1, active: false, session: 0, enabled: true },
    { version: 2, active: true, session: 7, enabled: true },
    { version: 3, active: false, session: 7, enabled: true },
  ]);
  await followControl(run.deps);
  assert.deepEqual(run.seen, [
    ['fetch', 0], ['enabled', true],
    ['fetch', 1], ['show', 7], ['shown', 7],
    ['fetch', 2], ['hide'],
  ]);
});

test('losing the service hides the bar, and a new session shows it again', async () => {
  const run = scripted([
    { version: 1, active: true, session: 1, enabled: true },
    new Error('restarting'),
    { version: 1, active: true, session: 2, enabled: true },
  ]);
  await followControl(run.deps);
  assert.deepEqual(run.seen.filter(([what]) => ['show', 'hide', 'shown'].includes(what)),
    [['show', 1], ['shown', 1], ['hide'], ['show', 2], ['shown', 2]]);
});

test('the tray switch follows the setting', async () => {
  const run = scripted([
    { version: 1, active: false, session: 0, enabled: false },
    { version: 2, active: false, session: 0, enabled: true },
  ]);
  await followControl(run.deps);
  assert.deepEqual(run.seen.filter(([what]) => what === 'enabled'), [['enabled', false], ['enabled', true]]);
});

test('the bar never takes the focus, stays on top, has Stop and the hotkey, and ships with the app', () => {
  assert.equal(CONTROL_HOTKEY, 'CommandOrControl+Alt+End');
  assert.match(main, /focusable: false/);
  assert.match(main, /showInactive\(\)/);
  assert.match(main, /setAlwaysOnTop\(true, 'screen-saver'\)/);
  assert.match(main, /globalShortcut\.register\(controlIndicator\.CONTROL_HOTKEY, stopControl\)/);
  assert.match(main, /'\/control\/stop'/);
  assert.match(main, /label: 'Let Arty control the screen', type: 'checkbox'/);
  assert.match(page, /Arty is controlling the screen/);
  assert.match(page, /id="stop"/);
  assert.match(page, /Content-Security-Policy/);
  for (const file of ['control-indicator.js', 'control-preload.js']) assert.ok(packaged.includes(file), file);
});
