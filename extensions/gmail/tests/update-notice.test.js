const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const folder = path.join(__dirname, '..');
const source = fs.readFileSync(path.join(folder, 'update-notice.js'), 'utf8');
const manifest = JSON.parse(fs.readFileSync(path.join(folder, 'manifest.json'), 'utf8'));

function answer(body, { status = 200 } = {}) {
  const text = typeof body === 'string' ? body : JSON.stringify(body);
  const bytes = new TextEncoder().encode(text);
  return {
    ok: status >= 200 && status < 300, status, headers: { get: () => null }, body: new Blob([bytes]).stream(),
    json: async () => JSON.parse(text),
  };
}

const latest = (version) => ({ latest: { gmail_extension: { version } } });

const SITE = 'https://arty.example.com';

function load({ reply = () => answer(latest('1.7')), saved = {}, written = () => answer({ site: SITE }) } = {}) {
  const requests = [];
  const shown = [];
  const opened = [];
  const logged = [];
  const listeners = {};
  const listen = (name) => ({ addListener: (callback) => { listeners[name] = callback; } });
  const chrome = {
    alarms: { create: (name, info) => { listeners.created = { name, info }; }, onAlarm: listen('alarm') },
    runtime: {
      getManifest: () => ({ version: '1.6' }), getURL: (name) => `chrome-extension://reader/${name}`,
      onStartup: listen('startup'), onInstalled: listen('installed'),
    },
    storage: { local: { get: async () => ({ ...saved }), set: async (values) => Object.assign(saved, values) }, onChanged: listen('changed') },
    notifications: {
      create: async (id, options) => shown.push({ id, options }), clear: async (id) => shown.push({ id, cleared: true }),
      onButtonClicked: listen('button'), onClicked: listen('clicked'), onClosed: listen('closed'),
    },
    tabs: { create: async ({ url }) => opened.push(url) },
  };
  const context = {
    chrome, URL, AbortSignal, TextDecoder,
    fetch: async (url, options) => {
      if (String(url) === 'chrome-extension://reader/update-source.json') return written();
      requests.push({ url, options });
      return reply();
    },
    console: { ...console, info: (line) => logged.push(line) },
  };
  vm.createContext(context);
  vm.runInContext(source, context);
  return { context, requests, shown, opened, logged, listeners, saved };
}

test('the Gmail reader is 1.6, gains no host permission for the update check and has a settings page', () => {
  assert.strictEqual(manifest.version, '1.6');
  assert.ok(manifest.permissions.includes('notifications'));
  assert.deepStrictEqual(manifest.host_permissions, ['https://mail.google.com/*', 'http://127.0.0.1:9334/*']);
  assert.ok(!manifest.host_permissions.some((pattern) => pattern.includes('*/')), 'no wildcard host');
  assert.strictEqual(manifest.options_ui.page, 'options.html');
  assert.match(fs.readFileSync(path.join(folder, 'options.html'), 'utf8'), /Tell me about new versions/);
  assert.match(fs.readFileSync(path.join(folder, 'background.js'), 'utf8'), /importScripts\("update-notice\.js"\)/);
  assert.match(fs.readFileSync(path.join(folder, '..', '..', 'deploy.sh'), 'utf8'), /extensions\/gmail\/update-notice\.js extensions\/gmail\/options\.html extensions\/gmail\/options\.js/);
});

test('checks start a minute after the browser starts and repeat once a day', () => {
  const loaded = load();
  loaded.listeners.startup();
  assert.deepStrictEqual(JSON.parse(JSON.stringify(loaded.listeners.created)), { name: 'arty-update-check', info: { delayInMinutes: 1, periodInMinutes: 1440 } });
  assert.strictEqual(loaded.requests.length, 0);
});

test('a newer version shows a notification with a button to the download page', async () => {
  const loaded = load();
  await loaded.context.checkForUpdate();
  assert.strictEqual(loaded.requests.length, 1);
  assert.strictEqual(loaded.requests[0].url, 'https://arty.example.com/download/manifest.json');
  assert.strictEqual(loaded.requests[0].options.credentials, 'omit');
  assert.strictEqual(loaded.requests[0].options.redirect, 'error');
  assert.strictEqual(loaded.shown[0].options.message, 'Version 1.7 is available');
  await loaded.listeners.button('arty-update', 0);
  assert.deepStrictEqual(loaded.opened, ['https://arty.example.com/download/']);
});

test('closing the notification dismisses that version only', async () => {
  const loaded = load();
  await loaded.context.checkForUpdate();
  await loaded.listeners.closed('arty-update', true);
  assert.strictEqual(loaded.saved.dismissedUpdate, '1.7');
  assert.strictEqual(await loaded.context.showUpdateNotice(), null);
  loaded.saved.latestVersion = '1.8';
  assert.strictEqual((await loaded.context.showUpdateNotice()).version, '1.8');
});

test('switched off it sends nothing, and failures log one line', async () => {
  const off = load({ saved: { updateCheck: false } });
  await off.context.checkForUpdate();
  assert.strictEqual(off.requests.length, 0);
  for (const reply of [() => answer('', { status: 500 }), () => answer('nope'), () => answer({ latest: {} }), () => { throw new TypeError('offline'); }]) {
    const failing = load({ reply });
    await failing.context.checkForUpdate();
    assert.strictEqual(failing.logged.length, 1);
    assert.deepStrictEqual(failing.shown, []);
  }
});

test('the site comes from update-source.json written by deploy.sh; without one nothing is asked', async () => {
  for (const [written, reason] of [[() => answer('', { status: 404 }), /missing or unreadable/], [() => { throw new TypeError('Failed to fetch'); }, /missing or unreadable/],
    [() => answer({ site: '' }), /names no Arty site/], [() => answer({ site: 7 }), /names no Arty site/], [() => answer({ site: 'http://arty.example.com' }), /only https/]]) {
    const loaded = load({ written });
    await loaded.context.checkForUpdate();
    assert.strictEqual(loaded.requests.length, 0);
    assert.strictEqual(loaded.logged.length, 1);
    assert.match(loaded.logged[0], reason);
    assert.deepStrictEqual(loaded.shown, []);
  }
  const deploy = fs.readFileSync(path.join(folder, '..', '..', 'deploy.sh'), 'utf8');
  assert.match(deploy, /arty_site_origin=\$\(own_app_origin ARTY_SITE_ADDRESS "\$\{ARTY_SITE_ADDRESS:-\}"\)/);
  assert.match(deploy, /gmail-extension\/update-source\.json/);
});

test('the reader names no outside server in its own files', () => {
  for (const name of ['update-notice.js', 'options.js', 'options.html', 'manifest.json']) {
    const hosts = [...fs.readFileSync(path.join(folder, name), 'utf8').matchAll(/https:\/\/([a-z0-9.*-]+)/gi)].map((found) => found[1]);
    assert.deepStrictEqual(hosts.filter((host) => !['mail.google.com', '*'].includes(host)), [], name);
  }
});
