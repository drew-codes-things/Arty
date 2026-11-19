'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const rendererFolder = path.join(__dirname, '..', 'renderer');
const read = (name) => fs.readFileSync(path.join(rendererFolder, name), 'utf8');

function load() {
  const context = vm.createContext({});
  vm.runInContext(`${read('presenting.js')}\nthis.exports = { PRESENTING_STORAGE_KEY, PRESENTING_CLASS, PRESENT_SHORTCUT_LABEL, isPrivateModel, modelsForAudience, modelNameForAudience, scrubAddresses, isPresentShortcut, audienceMaySee, presentTooltip, createPresentation };`, context);
  return context.exports;
}

function fakeRoot() {
  const classes = new Set();
  return { classes, classList: { toggle: (name, on) => (on ? classes.add(name) : classes.delete(name)) } };
}

const OWN_MODEL = { id: 'addon/pal', name: 'Pal (private)', model: 'tiny:1b', description: 'Its own persona and memory, with Arty\'s tools.' };
const SHARED_MODEL = { id: 'arty/qwen3:14b', name: 'qwen3:14b', model: 'qwen3:14b', description: '14B' };
const AUTO_MODEL = { id: 'auto', name: 'Arty Auto', model: '', description: 'Picks the model for each message' };

test('a model from the private add-on is recognised by its label', () => {
  const { isPrivateModel } = load();
  assert.equal(isPrivateModel(OWN_MODEL), true);
  assert.equal(isPrivateModel({ id: 'x/y', name: 'Kitty (private)' }), true);
  assert.equal(isPrivateModel({ id: 'addon/pal', name: 'Pal' }), false);
  assert.equal(isPrivateModel(SHARED_MODEL), false);
  assert.equal(isPrivateModel(AUTO_MODEL), false);
  assert.equal(isPrivateModel(null), false);
});

test('presenting leaves private models out of the picker, and not presenting leaves the list alone', () => {
  const { modelsForAudience } = load();
  const models = [AUTO_MODEL, SHARED_MODEL, OWN_MODEL];
  assert.deepEqual(JSON.parse(JSON.stringify(modelsForAudience(models, true))), [AUTO_MODEL, SHARED_MODEL]);
  assert.deepEqual(JSON.parse(JSON.stringify(modelsForAudience(models, false))), models);
});

test('the picker button never names a private model to the audience', () => {
  const { modelNameForAudience } = load();
  assert.equal(modelNameForAudience(OWN_MODEL, true), 'Arty Auto');
  assert.equal(modelNameForAudience(OWN_MODEL, false), 'Pal (private)');
  assert.equal(modelNameForAudience(SHARED_MODEL, true), 'qwen3:14b');
  assert.equal(modelNameForAudience(undefined, true), null);
});

test('addresses are scrubbed from text shown to the audience', () => {
  const { scrubAddresses } = load();
  const spoken = (text) => scrubAddresses(text);
  assert.equal(spoken('Couldn\'t reach http://192.0.2.13:8081/v1/models: timed out'), 'Couldn\'t reach the server: timed out');
  assert.equal(spoken('failed on 192.0.2.16:10300 twice'), 'failed on the server twice');
  assert.equal(spoken('HTTPSConnectionPool(host=\'arty.example.xyz\', port=443): Max retries'), 'HTTPSConnectionPool(the server, port=443): Max retries');
  assert.equal(spoken('try arty-server.local:8081 again'), 'try the server again');
  assert.equal(spoken('see arty.example.xyz for details'), 'see the server for details');
  assert.equal(spoken('The meeting is at 10:30 and 12:45'), 'The meeting is at 10:30 and 12:45');
  assert.equal(spoken(''), '');
});

test('Ctrl+Shift+P is the shortcut, with nothing else held and no repeat', () => {
  const { isPresentShortcut } = load();
  assert.equal(isPresentShortcut({ ctrlKey: true, shiftKey: true, altKey: false, metaKey: false, key: 'P', repeat: false }), true);
  assert.equal(isPresentShortcut({ ctrlKey: true, shiftKey: true, altKey: false, metaKey: false, key: 'p', repeat: false }), true);
  assert.equal(isPresentShortcut({ ctrlKey: true, shiftKey: false, altKey: false, metaKey: false, key: 'p', repeat: false }), false);
  assert.equal(isPresentShortcut({ ctrlKey: true, shiftKey: true, altKey: true, metaKey: false, key: 'P', repeat: false }), false);
  assert.equal(isPresentShortcut({ ctrlKey: true, shiftKey: true, altKey: false, metaKey: false, key: 'A', repeat: false }), false);
  assert.equal(isPresentShortcut({ ctrlKey: true, shiftKey: true, altKey: false, metaKey: false, key: 'P', repeat: true }), false);
});

test('memory and updates are closed to the audience, the conversation and calendar are not', () => {
  const { audienceMaySee } = load();
  assert.equal(audienceMaySee('memory', true), false);
  assert.equal(audienceMaySee('updates', true), false);
  assert.equal(audienceMaySee('calendar', true), true);
  assert.equal(audienceMaySee(null, true), true);
  assert.equal(audienceMaySee('memory', false), true);
  assert.equal(audienceMaySee('updates', false), true);
});

test('the chip tells what presenting does and names the shortcut', () => {
  const { presentTooltip, PRESENT_SHORTCUT_LABEL } = load();
  assert.equal(PRESENT_SHORTCUT_LABEL, 'Ctrl+Shift+P');
  assert.match(presentTooltip(false), /Presentation mode/);
  assert.match(presentTooltip(false), /Ctrl\+Shift\+P/);
  assert.match(presentTooltip(true), /Ctrl\+Shift\+P/);
  assert.notEqual(presentTooltip(true), presentTooltip(false));
});

test('presenting starts off, is remembered when switched, and marks the page', () => {
  const { createPresentation, PRESENTING_CLASS } = load();
  const root = fakeRoot();
  const saved = [];
  const changes = [];
  const presentation = createPresentation({ initial: false, save: (value) => saved.push(value), root, changed: (on) => changes.push(on) });
  presentation.start();
  assert.equal(presentation.on, false);
  assert.equal(root.classes.has(PRESENTING_CLASS), false);
  presentation.toggle();
  assert.equal(presentation.on, true);
  assert.equal(root.classes.has(PRESENTING_CLASS), true);
  presentation.toggle();
  assert.equal(presentation.on, false);
  assert.deepEqual(saved, [true, false]);
  assert.deepEqual(changes, [false, true, false]);
});

test('a remembered presenting state is applied when the window starts', () => {
  const { createPresentation, PRESENTING_CLASS } = load();
  const root = fakeRoot();
  const changes = [];
  const presentation = createPresentation({ initial: true, save: () => {}, root, changed: (on) => changes.push(on) });
  presentation.start();
  assert.equal(presentation.on, true);
  assert.equal(root.classes.has(PRESENTING_CLASS), true);
  assert.deepEqual(changes, [true]);
});

test('the page has the chip, the badge, the script and the rules that hide what the audience must not see', () => {
  const page = read('index.html');
  const styles = read('style.css');
  assert.match(page, /<button id="present"[^>]*title="[^"]*Ctrl\+Shift\+P[^"]*"/);
  assert.match(page, /id="presenting-badge"[^>]*>Presenting</);
  assert.ok(page.indexOf('presenting.js') > -1 && page.indexOf('presenting.js') < page.indexOf('app.js'));
  for (const hidden of ['#mem', '#upd', '#read', '#memory', '#updates', '.past']) {
    assert.match(styles, new RegExp(`\\.presenting[^{}]*${hidden.replace(/[.#]/g, '\\$&')}[^{}]*\\{[^}]*display:\\s*none`), `${hidden} is hidden while presenting`);
  }
  assert.match(styles, /html\.presenting\s*\{[^}]*font-size:\s*\d+%/);
});

test('the window guards every way in to what the audience must not see', () => {
  const app = read('app.js');
  assert.match(app, /isPresentShortcut\(event\)/);
  assert.match(app, /audienceMaySee\(view, presentation\.on\)/);
  assert.match(app, /modelsForAudience\(models, presentation\.on\)/);
  assert.match(app, /scrubAddresses\(/);
  for (const entry of ['onConnect', 'onPhoneCode', 'onChromeSites']) {
    assert.match(app, new RegExp(`window\\.arty\\?\\.${entry}\\(\\(\\) => whenNotPresenting|window\\.arty\\?\\.${entry}\\(whenNotPresenting`), `${entry} waits for presenting to end`);
  }
});
