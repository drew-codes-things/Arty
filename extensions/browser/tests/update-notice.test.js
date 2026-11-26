const test = require('node:test');
const assert = require('node:assert');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const folder = path.join(__dirname, '..');
const source = fs.readFileSync(path.join(folder, 'background.js'), 'utf8');
const manifest = JSON.parse(fs.readFileSync(path.join(folder, 'manifest.json'), 'utf8'));
const RELEASE_API_URL = 'https://api.github.com/repos/drew-codes-things/Arty/releases/latest';

function answer(body, { status = 200, headers = {} } = {}) {
  const bytes = new TextEncoder().encode(typeof body === 'string' ? body : JSON.stringify(body));
  return {
    ok: status >= 200 && status < 300, status,
    headers: { get: (name) => headers[name.toLowerCase()] ?? null },
    body: new Blob([bytes]).stream(),
  };
}

const asset = (name) => ({ name, size: 1, browser_download_url: `https://github.com/drew-codes-things/Arty/releases/download/v1.7.0/${name}` });
const latest = (version) => ({
  tag_name: 'v1.7.0',
  assets: [asset('Arty-1.7.0.apk'), asset(`Arty-Browser-Extension-${version}.xpi`), asset(`Arty-Browser-Extension-${version}.zip`), asset('SHA256SUMS.txt')],
});

function loadExtension({ reply = () => answer(latest('1.9')), saved = {} } = {}) {
  const requests = [];
  const alarms = [];
  const listeners = { alarm: [], startup: [], installed: [], changed: [] };
  const badges = [];
  const logged = [];
  const fetch = async (url, options = {}) => {
    if (String(url).startsWith('http://127.0.0.1')) return new Promise(() => {});
    requests.push({ url, options });
    return reply();
  };
  const chrome = {
    alarms: { create: (name, info) => alarms.push({ name, info }), onAlarm: { addListener: (callback) => listeners.alarm.push(callback) } },
    runtime: {
      getPlatformInfo: async () => ({}), getManifest: () => ({ version: '1.8' }),
      onStartup: { addListener: (callback) => listeners.startup.push(callback) },
      onInstalled: { addListener: (callback) => listeners.installed.push(callback) },
    },
    storage: {
      local: { get: async () => ({ ...saved }), set: async (values) => Object.assign(saved, values) },
      onChanged: { addListener: (callback) => listeners.changed.push(callback) },
    },
    action: { setBadgeText: async ({ text }) => badges.push(text), setTitle: async () => {} },
    tabs: { query: async () => [], onUpdated: { addListener: () => {}, removeListener: () => {} } },
  };
  const quick = (callback, ms) => {
    const timer = setTimeout(callback, Math.min(ms, 20));
    timer.unref();
    return timer;
  };
  const context = {
    chrome, fetch, setTimeout: quick, clearTimeout, URL, AbortSignal, TextDecoder, crypto: crypto.webcrypto, TextEncoder,
    console: { ...console, info: (line) => logged.push(line) },
  };
  vm.createContext(context);
  vm.runInContext(source, context);
  return { context, requests, alarms, listeners, saved, badges, logged };
}

const settle = () => new Promise((resolve) => setTimeout(resolve, 60));

test('the extension is 1.8 and may fetch the GitHub release', () => {
  assert.strictEqual(manifest.version, '1.8');
  assert.ok(manifest.host_permissions.includes('<all_urls>'));
});

test('the first check is a minute after the browser starts, then once a day', async () => {
  const loaded = loadExtension();
  assert.strictEqual(loaded.requests.length, 0);
  loaded.listeners.startup.forEach((callback) => callback());
  const scheduled = loaded.alarms.find((alarm) => alarm.name === 'arty-update-check');
  assert.deepStrictEqual(JSON.parse(JSON.stringify(scheduled.info)), { delayInMinutes: 1, periodInMinutes: 1440 });
  loaded.listeners.alarm.forEach((callback) => callback({ name: 'arty-keepalive' }));
  await settle();
  assert.strictEqual(loaded.requests.length, 0);
});

test('a newer version is saved as a notice with one plain https request', async () => {
  const loaded = loadExtension();
  await loaded.context.checkForUpdate();
  assert.strictEqual(loaded.requests.length, 1);
  assert.strictEqual(loaded.requests[0].url, RELEASE_API_URL);
  assert.strictEqual(loaded.requests[0].options.headers.Accept, 'application/vnd.github+json');
  assert.strictEqual(loaded.requests[0].options.credentials, 'omit');
  assert.strictEqual(loaded.requests[0].options.redirect, 'error');
  assert.ok(loaded.requests[0].options.signal instanceof AbortSignal);
  assert.deepStrictEqual(JSON.parse(JSON.stringify(loaded.saved.updateNotice)), { version: '1.9', text: 'Version 1.9 is available' });
});

test('the same or an older version, or a dismissed one, shows nothing; a later one shows again', async () => {
  for (const [version, saved, expected] of [['1.8', {}, null], ['1.7.9', {}, null], ['1.9', { dismissedUpdate: '1.9' }, null],
    ['1.10', { dismissedUpdate: '1.9' }, '1.10']]) {
    const loaded = loadExtension({ reply: () => answer(latest(version)), saved: { ...saved } });
    await loaded.context.checkForUpdate();
    assert.strictEqual(loaded.saved.updateNotice?.version ?? null, expected, version);
  }
});

test('switched off, it sends nothing', async () => {
  const loaded = loadExtension({ saved: { updateCheck: false } });
  await loaded.context.checkForUpdate();
  assert.strictEqual(loaded.requests.length, 0);
  assert.strictEqual(loaded.saved.updateNotice, null);
});

test('a failure logs one line and leaves no notice', async () => {
  const huge = JSON.stringify({ ...latest('9.0'), body: 'x'.repeat(256 * 1024) });
  for (const [reply, reason] of [[() => answer('', { status: 403 }), /403/], [() => answer('{oops'), /not valid JSON/],
    [() => answer(huge), /over 262144 bytes/], [() => { throw new TypeError('NetworkError'); }, /NetworkError/],
    [() => answer({ tag_name: 'v9.0.0', assets: [asset('Arty-9.0.0.apk')] }), /no browser extension zip/]]) {
    const loaded = loadExtension({ reply });
    await loaded.context.checkForUpdate();
    assert.strictEqual(loaded.logged.length, 1);
    assert.match(loaded.logged[0], reason);
    assert.strictEqual(loaded.saved.updateNotice, undefined);
  }
});

test('the badge says NEW while paired and a notice waits', async () => {
  const loaded = loadExtension();
  await loaded.context.checkForUpdate();
  await loaded.context.showPaired(true);
  await loaded.context.showPaired(false);
  assert.deepStrictEqual(loaded.badges.slice(-2), ['NEW', '!']);
});

test('the popup shows the notice, a dismiss button, the download page link and the switch', () => {
  const popup = fs.readFileSync(path.join(folder, 'popup.html'), 'utf8');
  const script = fs.readFileSync(path.join(folder, 'popup.js'), 'utf8');
  assert.match(popup, /id="update-notice"[^>]*hidden/);
  assert.match(popup, /Tell me about new versions/);
  assert.match(script, /dismissedUpdate/);
  assert.match(script, /chrome\.tabs\.create\(\{ url: RELEASE_PAGE_URL \}\)/);
});

test('the popup shows a waiting notice and hides it once dismissed', async () => {
  const popup = fs.readFileSync(path.join(folder, 'popup.js'), 'utf8');
  const listeners = {};
  const control = (id) => ({ hidden: true, checked: false, textContent: '', addEventListener: (kind, listener) => { listeners[id] = listener; } });
  const elements = Object.fromEntries(['pair', 'code', 'status', 'update-notice', 'update-check', 'update-text', 'update-open', 'update-dismiss'].map((id) => [id, control(id)]));
  const saved = { updateNotice: { version: '1.9', text: 'Version 1.9 is available' } };
  const opened = [];
  const chrome = {
    storage: { local: { get: async () => ({ ...saved }), set: async (values) => Object.assign(saved, values) } },
    tabs: { create: async (options) => opened.push(options.url) },
  };
  const context = { chrome, document: { getElementById: (id) => elements[id] }, console };
  vm.createContext(context);
  vm.runInContext(popup, context);
  await settle();
  assert.strictEqual(elements['update-notice'].hidden, false);
  assert.strictEqual(elements['update-text'].textContent, 'Version 1.9 is available');
  assert.strictEqual(elements['update-check'].checked, true);
  listeners['update-open']();
  assert.deepStrictEqual(opened, ['https://github.com/drew-codes-things/Arty/releases/latest']);
  await listeners['update-dismiss']();
  assert.strictEqual(saved.dismissedUpdate, '1.9');
  assert.strictEqual(elements['update-notice'].hidden, true);
});

test('the version comes from the extension zip in the release, the newest one if there are several', () => {
  const { context } = loadExtension();
  assert.strictEqual(context.releaseExtensionVersion(latest('1.9')), '1.9');
  assert.strictEqual(context.releaseExtensionVersion({ assets: [asset('Arty-Browser-Extension-1.9.zip'), asset('Arty-Browser-Extension-1.10.zip')] }), '1.10');
  for (const bad of [null, {}, { assets: 'x' }, { assets: [null, 7, { name: 5 }] }, { assets: [asset('Arty-Browser-Extension-1.9.zip.sig')] },
    { assets: [asset('Arty-Browser-Extension-v1.9.zip')] }, { assets: [asset('Arty-Browser-Extension-1.9<b>.zip')] }]) {
    assert.strictEqual(context.releaseExtensionVersion(bad), null);
  }
});

test('the only outside servers the extension names are GitHub and the sites it is asked to open', () => {
  const hosts = new Set();
  for (const name of ['background.js', 'popup.js', 'popup.html']) {
    for (const [, host] of fs.readFileSync(path.join(folder, name), 'utf8').matchAll(/https:\/\/([a-z0-9.-]+)/gi)) hosts.add(host);
  }
  assert.deepStrictEqual([...hosts].sort(), ['api.github.com', 'github.com']);
});
