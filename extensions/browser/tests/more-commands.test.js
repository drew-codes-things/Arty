const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const crypto = require('crypto');

const folder = path.join(__dirname, '..');
const source = fs.readFileSync(path.join(folder, 'background.js'), 'utf8');
const never = () => new Promise(() => {});
const SHOP = [{ id: 7, active: true, title: 'Shop', url: 'https://shop.example/basket', windowId: 1 }];

function loadExtension({ tabs = SHOP, stored = {}, fetch = never, inject, capture } = {}) {
  const done = [];
  const listeners = { add: () => {}, remove: () => {} };
  const chrome = {
    alarms: { create: () => {}, onAlarm: { addListener: () => {} } },
    runtime: { getPlatformInfo: async () => ({}), onStartup: { addListener: () => {} }, onInstalled: { addListener: () => {} } },
    storage: { local: { get: async () => stored, set: async () => {} } },
    action: { setBadgeText: async () => {}, setTitle: async () => {} },
    tabs: {
      get: async (id) => tabs.find((tab) => tab.id === id),
      query: async () => tabs.filter((tab) => tab.active),
      update: async (id, change) => { done.push(['update', id, change]); return tabs.find((tab) => tab.id === id); },
      captureVisibleTab: async (windowId, options) => { done.push(['capture', windowId, options.format]); return capture ?? 'data:image/jpeg;base64,AAAA'; },
      onUpdated: listeners,
    },
    windows: { update: async () => {} },
    scripting: {
      executeScript: async ({ func, args }) => { done.push(['script', func.name, args]); return inject ? inject(func.name, args) : [{ result: {} }]; },
    },
  };
  const background = (callback, ms) => {
    const timer = setTimeout(callback, ms);
    timer.unref();
    return timer;
  };
  const context = { chrome, fetch, setTimeout: background, clearTimeout, console, URL, AbortSignal, crypto: crypto.webcrypto, TextEncoder };
  vm.createContext(context);
  vm.runInContext(source, context);
  return { context, done };
}

test('scroll moves the page and hands back what is now in view', async () => {
  const view = { moved: true, top: false, bottom: false, text: 'Section two' };
  const { context, done } = loadExtension({ inject: () => [{ result: view }] });
  const result = await context.run({ action: 'scroll', direction: 'down', site: 'shop.example' });
  assert.strictEqual(result.text, 'Section two');
  assert.strictEqual(result.url, 'https://shop.example/basket');
  assert.deepStrictEqual(done.map((step) => step.slice(0, 2)), [['script', 'scrollPage']]);
  assert.strictEqual(done[0][2][0], 'down');
});

test('scroll and screenshot are refused when the tab has moved to another site', async () => {
  for (const action of ['scroll', 'screenshot']) {
    const { context, done } = loadExtension();
    await assert.rejects(context.run({ action, direction: 'down', site: 'bank.example' }), /shop\.example/);
    assert.deepStrictEqual(done, []);
  }
});

test('a screenshot is the visible tab as a picture, with its title and address', async () => {
  const { context, done } = loadExtension({ inject: () => [{ result: false }] });
  const result = await context.run({ action: 'screenshot', site: 'shop.example' });
  assert.strictEqual(result.image, 'data:image/jpeg;base64,AAAA');
  assert.strictEqual(result.url, 'https://shop.example/basket');
  assert.deepStrictEqual(done.map((step) => step[0]), ['script', 'update', 'capture']);
  assert.strictEqual(done[0][1], 'privateFieldFilled');
});

test('no screenshot is taken while a password or payment field has something in it', async () => {
  const { context, done } = loadExtension({ inject: () => [{ result: true }] });
  const result = await context.run({ action: 'screenshot', site: 'shop.example' });
  assert.match(result.error, /password or payment/);
  assert.ok(!done.some((step) => step[0] === 'capture'));
});

test('a page script that fails in Firefox comes back as its own error, not a crash', async () => {
  const { context } = loadExtension({ inject: () => [{ error: new Error('Missing host permission for the tab') }] });
  await assert.rejects(context.run({ action: 'click', text: 'Next', site: 'shop.example' }), /Missing host permission/);
  const empty = loadExtension({ inject: () => [] });
  await assert.rejects(empty.context.run({ action: 'read' }), /can't work in this page/);
});

test('a result that fails to send is sent again instead of being lost', async () => {
  const sent = [];
  let handedOut = false;
  const fetch = async (url, options) => {
    if (url.includes('/browser/hello')) {
      const nonce = new URL(url).searchParams.get('nonce');
      const proof = crypto.createHmac('sha256', 'abc123').update(`arty-browser-service:${nonce}`).digest('hex');
      return { ok: true, status: 200, json: async () => ({ proof }) };
    }
    if (url.includes('/browser/next')) {
      if (handedOut) return never();
      handedOut = true;
      return { ok: true, status: 200, json: async () => ({ id: 4, action: 'tabs' }) };
    }
    sent.push(JSON.parse(options.body));
    if (sent.length === 1) throw new TypeError('Failed to fetch');
    return { ok: true, status: 200 };
  };
  loadExtension({ stored: { pairing: 'abc123' }, fetch });
  for (let waited = 0; sent.length < 2 && waited < 3000; waited += 50) await new Promise((resolve) => setTimeout(resolve, 50));
  assert.strictEqual(sent.length, 2);
  assert.deepStrictEqual(sent[0], sent[1]);
  assert.strictEqual(sent[1].id, 4);
});

function fieldsPage(fields) {
  const element = ({ type = 'text', name = '', autocomplete = '', label = '', value = '' }) => {
    const attributes = { type, autocomplete, 'aria-label': label };
    return {
      tagName: 'INPUT', name, id: '', value, placeholder: '', getAttribute: (key) => attributes[key] ?? null,
      getBoundingClientRect: () => ({ width: 10, height: 10 }),
    };
  };
  return { document: { querySelectorAll: () => fields.map(element) }, getComputedStyle: () => ({ visibility: 'visible', display: 'block' }) };
}

function runInPage(name, page, args) {
  const start = source.indexOf(`function ${name}(`);
  const end = source.indexOf('\n}\n', start) + 3;
  const context = { ...page };
  vm.createContext(context);
  vm.runInContext(`${source.slice(start, end)}\nresult = ${name}(...${JSON.stringify(args)});`, context);
  return context.result;
}

test('the screenshot check spots filled card, code and password fields, and only filled ones', () => {
  const { context } = loadExtension();
  const hints = vm.runInContext('PRIVATE_FIELD_HINTS', context);
  assert.strictEqual(runInPage('privateFieldFilled', fieldsPage([{ name: 'q', value: 'socks' }]), [hints]), false);
  assert.strictEqual(runInPage('privateFieldFilled', fieldsPage([{ autocomplete: 'cc-number', value: '' }]), [hints]), false);
  for (const filled of [{ autocomplete: 'cc-number', value: '4111' }, { type: 'password', value: 'x' }, { name: 'otp', value: '1' }]) {
    assert.strictEqual(runInPage('privateFieldFilled', fieldsPage([filled]), [hints]), true, JSON.stringify(filled));
  }
});

test('text that came into view keeps the words of one paragraph on one line', () => {
  const block = (name, top) => ({ name, box: { width: 100, top, bottom: top + 20 } });
  const infobox = block('infobox', 10);
  const article = block('article', 40);
  const below = block('below', 2000);
  const text = (content, parent) => ({ textContent: content, parentElement: { getBoundingClientRect: () => parent.box, closest: (selector) => (selector.includes('aria-hidden') ? null : parent) } });
  const nodes = [text('Born ', infobox), text('Maida Vale', infobox), text(', London', infobox), text('Turing was', article),
    text(' a mathematician.', article), text('Not in view', below)];
  const page = {
    window: { scrollY: 0, innerHeight: 900, scrollBy() { this.scrollY = 300; }, scrollTo() {} },
    document: { documentElement: { scrollHeight: 5000 }, body: { scrollHeight: 5000 }, createTreeWalker: () => ({ nextNode: () => nodes.shift() }) },
    NodeFilter: { SHOW_TEXT: 4 },
    getComputedStyle: () => ({ fontSize: '16px' }),
  };
  page.window.window = page.window;
  const result = runInPage('scrollPage', { ...page, window: page.window, scrollY: 0 }, ['down', 4000]);
  assert.strictEqual(result.text, 'Born Maida Vale , London\nTuring was a mathematician.');
});

test('reading, scrolling or looking at a tab that is not open uses the tab in front and says which', async () => {
  const page = { title: 'Shop', url: 'https://shop.example/basket', text: 'Socks', items: [] };
  for (const command of [{ action: 'read', tab: 99 }, { action: 'scroll', tab: 99, site: 'shop.example' }, { action: 'screenshot', tab: 99, site: 'shop.example' }]) {
    const { context } = loadExtension({ inject: (name) => [{ result: name === 'privateFieldFilled' ? false : page }] });
    const result = await context.run(command);
    assert.strictEqual(result.tab, 7, command.action);
    assert.strictEqual(result.missingTab, 99, command.action);
  }
});

test('clicking, typing or changing a tab that is not open is refused, not moved to the tab in front', async () => {
  for (const command of [{ action: 'click', tab: 99, text: 'Next' }, { action: 'type', tab: 99, text: 'x' }, { action: 'tab', tab: 99, tabAction: 'close' }]) {
    const { context, done } = loadExtension();
    await assert.rejects(context.run(command), /no tab 99/);
    assert.deepStrictEqual(done, []);
  }
});

test('a read waits for the page to settle first, so pages that draw themselves after loading are read whole', async () => {
  const { context, done } = loadExtension({ inject: (name) => [{ result: name === 'readPage' ? { title: 'Results', url: 'https://shop.example/basket', text: '', items: [] } : true }] });
  await context.run({ action: 'read' });
  assert.deepStrictEqual(done.map((step) => step[1]), ['waitForQuiet', 'readPage', 'playersOnPage']);
});

test('waiting for the page to settle ends after a quiet spell, or at the limit on a page that never stops', async () => {
  const quietPage = (mutating) => ({
    document: { documentElement: {} },
    setTimeout, clearTimeout,
    MutationObserver: class {
      constructor(callback) { this.timer = mutating ? setInterval(callback, 5) : null; }
      observe() {}
      disconnect() { clearInterval(this.timer); }
    },
  });
  for (const [mutating, least, most] of [[false, 40, 150], [true, 200, 400]]) {
    const started = Date.now();
    const context = { ...quietPage(mutating) };
    vm.createContext(context);
    const start = source.indexOf('function waitForQuiet(');
    vm.runInContext(source.slice(start, source.indexOf('\n}\n', start) + 3), context);
    await context.waitForQuiet(40, 200);
    const waited = Date.now() - started;
    assert.ok(waited >= least - 5 && waited < most, `mutating ${mutating}: waited ${waited} ms`);
  }
});
