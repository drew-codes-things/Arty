const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const folder = path.join(__dirname, '..');
const source = fs.readFileSync(path.join(folder, 'background.js'), 'utf8');
const never = () => new Promise(() => {});

function loadExtension({ tabs, stored = {}, fetch = never }) {
  const done = [];
  const listeners = { add: () => {}, remove: () => {} };
  const chrome = {
    alarms: { create: () => {}, onAlarm: { addListener: () => {} } },
    runtime: { getPlatformInfo: async () => ({}), onStartup: { addListener: () => {} }, onInstalled: { addListener: () => {} } },
    storage: { local: { get: async () => stored }, onChanged: { addListener: () => {} } },
    action: { setBadgeText: async () => {}, setBadgeBackgroundColor: async () => {}, setTitle: async () => {} },
    tabs: {
      get: async (id) => tabs.find((tab) => tab.id === id),
      query: async () => tabs.filter((tab) => tab.active),
      update: async (id) => { done.push(['update', id]); return tabs.find((tab) => tab.id === id); },
      remove: async (id) => done.push(['remove', id]),
      goBack: async (id) => done.push(['back', id]),
      goForward: async (id) => done.push(['forward', id]),
      reload: async (id) => done.push(['reload', id]),
      onUpdated: listeners,
    },
    windows: { update: async () => {} },
    scripting: { executeScript: async () => { done.push(['script']); return [{ result: { field: 'search', clicked: 'Next' } }]; } },
  };
  const background = (callback, ms) => {
    const timer = setTimeout(callback, ms);
    timer.unref();
    return timer;
  };
  const context = { chrome, fetch, setTimeout: background, clearTimeout, console, URL, AbortSignal };
  vm.createContext(context);
  vm.runInContext(source, context);
  return { context, done };
}

const BANK_IN_FRONT = [{ id: 3, active: true, title: 'Inbox', url: 'https://bank.example/inbox', windowId: 1 }];

for (const command of [
  { action: 'click', text: 'Next', site: 'shop.example' },
  { action: 'type', text: 'socks', label: 'search', site: 'shop.example' },
  { action: 'tab', tabAction: 'reload', site: 'shop.example' },
]) {
  test(`${command.action} is refused when the tab is no longer on the checked site`, async () => {
    const { context, done } = loadExtension({ tabs: BANK_IN_FRONT });
    await assert.rejects(context.run(command), /bank\.example/);
    assert.deepStrictEqual(done, []);
  });
}

test('an action on the checked site still runs', async () => {
  const shop = [{ id: 7, active: true, title: 'Shop', url: 'https://shop.example/basket', windowId: 1 }];
  const { context, done } = loadExtension({ tabs: shop });
  const result = await context.run({ action: 'tab', tabAction: 'reload', site: 'shop.example' });
  assert.deepStrictEqual(done, [['reload', 7]]);
  assert.strictEqual(result.url, 'https://shop.example/basket');
});

test('sites are named the same way as Arty Desktop names them', () => {
  const { context } = loadExtension({ tabs: [] });
  const named = (address) => context.siteOf(address);
  assert.strictEqual(named('https://Shop.Example:443/x'), 'shop.example');
  assert.strictEqual(named('http://localhost:8080/admin'), 'http://localhost:8080');
  assert.strictEqual(named('https://bücher.example/'), 'xn--bcher-kva.example');
  assert.strictEqual(named('http://[::1]:3000/'), 'http://[::1]:3000');
  assert.strictEqual(named('chrome://newtab/'), 'chrome://newtab');
  assert.strictEqual(named('file:///home/me/x.html'), '');
});

test('the extension sends its pairing code and waits for one before polling', async () => {
  const sent = [];
  const fetch = (url, options) => { sent.push(options?.headers?.['X-Arty-Pairing']); return never(); };
  loadExtension({ tabs: [], stored: { pairing: 'abc123' }, fetch });
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.deepStrictEqual(sent, ['abc123']);
  const unpaired = [];
  loadExtension({ tabs: [], stored: {}, fetch: (url) => { unpaired.push(url); return never(); } });
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.deepStrictEqual(unpaired, []);
});

test('the popup saves the pairing code it is given', () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(folder, 'manifest.json'), 'utf8'));
  assert.ok(manifest.permissions.includes('storage'));
  assert.strictEqual(manifest.action.default_popup, 'popup.html');
  const popup = fs.readFileSync(path.join(folder, 'popup.js'), 'utf8');
  assert.match(popup, /chrome\.storage\.local\.set\(\{ pairing/);
});
