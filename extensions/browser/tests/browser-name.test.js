const test = require('node:test');
const assert = require('node:assert');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const folder = path.join(__dirname, '..');
const source = fs.readFileSync(path.join(folder, 'background.js'), 'utf8');
const manifest = JSON.parse(fs.readFileSync(path.join(folder, 'manifest.json'), 'utf8'));
const never = () => new Promise(() => {});
const PAIRING = 'abc123';
const CHROME_USER_AGENT = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36';

const proofFor = (code, nonce) => crypto.createHmac('sha256', code).update(`arty-browser-service:${nonce}`).digest('hex');

function loadExtension({ navigator, firefox, tabs = [{ id: 1, active: true, url: 'https://shop.example/' }] }) {
  const calls = [];
  const fetch = async (url, options = {}) => {
    const address = new URL(url);
    calls.push({ path: address.pathname, query: Object.fromEntries(address.searchParams), pairing: options.headers?.['X-Arty-Pairing'] });
    if (address.pathname === '/browser/hello') {
      return { ok: true, status: 200, json: async () => ({ proof: proofFor(PAIRING, address.searchParams.get('nonce')) }) };
    }
    return never();
  };
  const saved = { pairing: PAIRING };
  const chrome = {
    alarms: { create: () => {}, onAlarm: { addListener: () => {} } },
    runtime: { getPlatformInfo: async () => ({}), onStartup: { addListener: () => {} }, onInstalled: { addListener: () => {} } },
    storage: { local: { get: async () => ({ ...saved }), set: async (values) => Object.assign(saved, values) } },
    action: { setBadgeText: async () => {}, setTitle: async () => {} },
    tabs: { query: async () => tabs, onUpdated: { addListener: () => {}, removeListener: () => {} } },
  };
  const quick = (callback, ms) => {
    const timer = setTimeout(callback, Math.min(ms, 20));
    timer.unref();
    return timer;
  };
  const context = { chrome, fetch, setTimeout: quick, clearTimeout, console, URL, AbortSignal, crypto: crypto.webcrypto, TextEncoder };
  if (navigator) context.navigator = navigator;
  if (firefox) context.browser = firefox;
  vm.createContext(context);
  vm.runInContext(source, context);
  return calls;
}

const settle = () => new Promise((resolve) => setTimeout(resolve, 80));

async function reportedName(setup) {
  const calls = loadExtension(setup);
  await settle();
  const next = calls.find((call) => call.path === '/browser/next');
  assert.ok(next, JSON.stringify(calls));
  assert.strictEqual(next.pairing, PAIRING);
  return next.query.browser_name;
}

const brands = (...names) => ({ userAgent: CHROME_USER_AGENT, userAgentData: { brands: names.map((brand) => ({ brand, version: '156' })) } });

test('each Chromium browser reports its own name from its brands', async () => {
  assert.strictEqual(await reportedName({ navigator: brands('Chromium', 'Google Chrome', 'Not.A/Brand') }), 'Chrome');
  assert.strictEqual(await reportedName({ navigator: brands('Not)A;Brand', 'Opera', 'Chromium') }), 'Opera');
  assert.strictEqual(await reportedName({ navigator: brands('Opera GX', 'Chromium') }), 'Opera');
  assert.strictEqual(await reportedName({ navigator: brands('Microsoft Edge', 'Chromium', 'Not_A Brand') }), 'Edge');
  assert.strictEqual(await reportedName({ navigator: brands('Brave', 'Chromium', 'Not_A Brand') }), 'Brave');
  assert.strictEqual(await reportedName({ navigator: brands('Chromium', 'Not_A Brand') }), 'Chromium');
});

test('without brands the user agent names Opera and Edge', async () => {
  assert.strictEqual(await reportedName({ navigator: { userAgent: `${CHROME_USER_AGENT} OPR/120.0.0.0` } }), 'Opera');
  assert.strictEqual(await reportedName({ navigator: { userAgent: `${CHROME_USER_AGENT} Edg/156.0.0.0` } }), 'Edge');
  assert.strictEqual(await reportedName({ navigator: { userAgent: CHROME_USER_AGENT } }), 'Chrome');
});

test('Vivaldi is told apart by the extra data on its tabs', async () => {
  const tabs = [{ id: 1, active: true, url: 'https://shop.example/', vivExtData: '{}' }];
  assert.strictEqual(await reportedName({ navigator: brands('Chromium', 'Google Chrome'), tabs }), 'Vivaldi');
});

test('Firefox reports its name from the browser info', async () => {
  const firefox = { runtime: { getBrowserInfo: async () => ({ name: 'Firefox', vendor: 'Mozilla', version: '140.0' }) } };
  assert.strictEqual(await reportedName({ navigator: { userAgent: 'Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0' }, firefox }), 'Firefox');
  assert.strictEqual(await reportedName({ navigator: { userAgent: 'Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0' } }), 'Firefox');
});

test('a browser it cannot tell sends no name', async () => {
  assert.strictEqual(await reportedName({}), '');
});

function extensionIdOf(key) {
  const digest = crypto.createHash('sha256').update(Buffer.from(key, 'base64')).digest('hex').slice(0, 32);
  return [...digest].map((digit) => String.fromCharCode('a'.charCodeAt(0) + parseInt(digit, 16))).join('');
}

test('the browser-neutral name keeps the fixed id Arty Desktop checks', () => {
  const service = fs.readFileSync(path.join(folder, '..', '..', 'apps', 'desktop', 'server', 'arty_server.py'), 'utf8');
  const checked = service.match(/^EXTENSION_ID = "([a-p]{32})"/m)[1];
  assert.strictEqual(extensionIdOf(manifest.key), checked);
  assert.strictEqual(manifest.browser_specific_settings.gecko.id, 'arty-browser@arty.home');
  assert.doesNotMatch(manifest.name, /Chrome/);
  assert.doesNotMatch(manifest.action.default_title, /Chrome/);
});
