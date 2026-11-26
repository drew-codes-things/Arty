const test = require('node:test');
const assert = require('node:assert');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const folder = path.join(__dirname, '..');
const source = fs.readFileSync(path.join(folder, 'background.js'), 'utf8');
const manifest = JSON.parse(fs.readFileSync(path.join(folder, 'manifest.json'), 'utf8'));
const NATIVE_CODE = 'aaaaaaaaaaaaaaaabbbbbbbbbbbbbbbb';
const OLD_CODE = 'cccccccccccccccccccccccccccccccc';
const never = () => new Promise(() => {});
const proofFor = (code, nonce) => crypto.createHmac('sha256', code).update(`arty-browser-service:${nonce}`).digest('hex');

function loadExtension({ stored = {}, serviceCode = NATIVE_CODE, nativeAnswer, nativeFails = false }) {
  const calls = { native: [], next: [] };
  const saved = { ...stored };
  const fetch = async (url, options = {}) => {
    const address = new URL(url);
    if (address.pathname === '/browser/hello') {
      return { ok: true, status: 200, json: async () => ({ proof: proofFor(serviceCode, address.searchParams.get('nonce')) }) };
    }
    if (address.pathname === '/browser/next') calls.next.push(options.headers['X-Arty-Pairing']);
    return never();
  };
  const sendNativeMessage = async (host, message) => {
    calls.native.push({ host, message });
    if (nativeFails) throw new Error('Specified native messaging host not found.');
    return nativeAnswer;
  };
  const chrome = {
    alarms: { create: () => {}, onAlarm: { addListener: () => {} } },
    runtime: { getPlatformInfo: async () => ({}), onStartup: { addListener: () => {} }, onInstalled: { addListener: () => {} }, sendNativeMessage },
    storage: { local: { get: async () => ({ ...saved }), set: async (values) => Object.assign(saved, values) } },
    action: { setBadgeText: async () => {}, setTitle: async () => {} },
    tabs: { query: async () => [], onUpdated: { addListener: () => {}, removeListener: () => {} } },
  };
  const quick = (callback, ms) => {
    const timer = setTimeout(callback, Math.min(ms, 20));
    timer.unref();
    return timer;
  };
  const context = { chrome, fetch, setTimeout: quick, clearTimeout, console, URL, AbortSignal, crypto: crypto.webcrypto, TextEncoder, Date };
  vm.createContext(context);
  vm.runInContext(source, context);
  return { calls, saved };
}

const settle = () => new Promise((resolve) => setTimeout(resolve, 150));

test('the manifest asks for native messaging and is version 1.8', () => {
  assert.ok(manifest.permissions.includes('nativeMessaging'));
  assert.strictEqual(manifest.version, '1.8');
});

test('with no code saved, the extension asks Arty Desktop for one through native messaging and uses it', async () => {
  const { calls, saved } = loadExtension({ nativeAnswer: { type: 'pairing', code: NATIVE_CODE } });
  await settle();
  assert.deepStrictEqual(JSON.parse(JSON.stringify(calls.native[0])), { host: 'app.arty.desktop', message: { type: 'pair' } });
  assert.strictEqual(saved.pairing, NATIVE_CODE);
  assert.strictEqual(saved.pairedBy, 'native');
  assert.strictEqual(calls.next[0], NATIVE_CODE);
});

for (const [label, nativeAnswer] of [
  ['an answer with no code', { type: 'unavailable' }],
  ['a code that is not 32 hex characters', { type: 'pairing', code: 'ABCDEF' }],
  ['a code with extra characters', { type: 'pairing', code: `${NATIVE_CODE}0` }],
  ['an answer of the wrong type', { type: 'other', code: NATIVE_CODE }],
  ['nothing at all', undefined],
]) {
  test(`${label} from the native host is not used`, async () => {
    const { calls, saved } = loadExtension({ nativeAnswer });
    await settle();
    assert.strictEqual(saved.pairing, undefined);
    assert.deepStrictEqual(calls.next, []);
  });
}

test('a missing native host leaves the paste-a-code way working, and is not asked again within 30 seconds', async () => {
  const { calls, saved } = loadExtension({ nativeFails: true });
  await settle();
  assert.strictEqual(calls.native.length, 1);
  assert.strictEqual(saved.pairing, undefined);
  assert.deepStrictEqual(calls.next, []);
});

test('a saved code the service still proves is used without asking the native host', async () => {
  const { calls } = loadExtension({ stored: { pairing: OLD_CODE }, serviceCode: OLD_CODE, nativeAnswer: { type: 'pairing', code: NATIVE_CODE } });
  await settle();
  assert.strictEqual(calls.native.length, 0);
  assert.strictEqual(calls.next[0], OLD_CODE);
});

test('when the saved code no longer matches, a new code from the native host replaces it', async () => {
  const { calls, saved } = loadExtension({ stored: { pairing: OLD_CODE }, serviceCode: NATIVE_CODE, nativeAnswer: { type: 'pairing', code: NATIVE_CODE } });
  await settle();
  assert.strictEqual(calls.native.length, 1);
  assert.strictEqual(saved.pairing, NATIVE_CODE);
  assert.strictEqual(calls.next[0], NATIVE_CODE);
});

test('a code from the native host that the service cannot prove is never sent', async () => {
  const { calls } = loadExtension({ serviceCode: OLD_CODE, nativeAnswer: { type: 'pairing', code: NATIVE_CODE } });
  await settle();
  assert.strictEqual(calls.native.length, 1);
  assert.deepStrictEqual(calls.next, []);
});

test('the popup says when it was paired automatically, and a pasted code is marked as pasted', async () => {
  const popup = fs.readFileSync(path.join(folder, 'popup.js'), 'utf8');
  const elements = { pair: { addEventListener: (kind, listener) => { elements.submit = listener; } }, code: { value: '' }, status: { textContent: '' } };
  const control = () => ({ hidden: true, checked: false, textContent: '', addEventListener: () => {} });
  for (const id of ['update-notice', 'update-check', 'update-text', 'update-open', 'update-dismiss']) elements[id] = control();
  const saved = { pairing: NATIVE_CODE, pairedBy: 'native' };
  const chrome = { storage: { local: { get: async () => ({ ...saved }), set: async (values) => Object.assign(saved, values) } } };
  const context = { chrome, document: { getElementById: (id) => elements[id] }, console };
  vm.createContext(context);
  vm.runInContext(popup, context);
  await settle();
  assert.match(elements.status.textContent, /automatically/);
  elements.code.value = OLD_CODE;
  await elements.submit({ preventDefault: () => {} });
  assert.strictEqual(saved.pairing, OLD_CODE);
  assert.strictEqual(saved.pairedBy, 'code');
});
