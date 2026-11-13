const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'background.js'), 'utf8');
const readPageSource = source.slice(source.indexOf('function readPage('), source.indexOf('function waitForQuiet('));
const hintsLine = source.match(/^const PRIVATE_FIELD_HINTS = .*$/m)[0];
const INTERACTIVE = new Set(['A', 'BUTTON', 'INPUT', 'TEXTAREA', 'SELECT']);

function descendants(element) {
  return element.children.flatMap((child) => [child, ...descendants(child)]);
}

function node(tag, attributes = {}, children = [], text = '') {
  const element = {
    tagName: tag.toUpperCase(), id: attributes.id || '', className: attributes.class || '', name: '', value: '', placeholder: '',
    isContentEditable: false, labels: [], parentElement: null, children, innerText: text || children.map((child) => child.innerText).join('\n'),
    getAttribute: (key) => attributes[key] ?? null,
    setAttribute: () => {}, removeAttribute: () => {},
    getBoundingClientRect: () => ({ width: 10, height: 10 }),
    querySelector: () => descendants(element).find((inner) => /^H[1-4]$/.test(inner.tagName) || inner.getAttribute('role') === 'heading') || null,
  };
  for (const child of children) child.parentElement = element;
  return element;
}

const link = (label, href) => node('a', { href }, [], label);

function readTree(body) {
  const html = node('html', {}, [body]);
  const all = descendants(html);
  const context = {
    document: {
      title: 'lofi - Video site', body,
      querySelector: () => all.find((element) => element.tagName === 'MAIN' || element.getAttribute('role') === 'main') || null,
      querySelectorAll: (selector) => {
        if (selector === '[data-arty-n]' || selector === 'video, audio') return [];
        return all.filter((element) => INTERACTIVE.has(element.tagName) || element.getAttribute('role') === 'button');
      },
    },
    location: { href: 'https://video.example/results?q=lofi' },
    getComputedStyle: () => ({ visibility: 'visible', display: 'block' }),
  };
  vm.createContext(context);
  vm.runInContext(`${hintsLine}\n${readPageSource}\nresult = readPage(PRIVATE_FIELD_HINTS);`, context);
  return context.result;
}

function resultsPage() {
  return node('body', {}, [
    node('header', {}, [link('Home', '/'), node('input', { 'aria-label': 'Search' })]),
    node('div', { role: 'navigation' }, [link('Subscriptions', '/feed/subscriptions')]),
    node('div', { role: 'main' }, [
      node('ytd-ad-slot-renderer', {}, [node('h3', {}, [link('Sponsored video', '/watch?v=ad')])]),
      node('div', {}, [node('h3', {}, [link('First real video', '/watch?v=1')]), link('Its channel', '/c/one'),
        node('h4', {}, [link('Chapter two', '/watch?v=1&t=60s')])]),
      node('div', {}, [node('h3', {}, [link('A short clip', '/shorts/abc')])]),
      node('div', {}, [link('First real video again', '/watch?v=1')]),
      node('article', {}, [node('header', {}, [node('h2', {}, [link('Second real video', '/watch?v=2')])])]),
    ], 'First real video Its channel Second real video, all about lofi music for studying and sleeping. '.repeat(4)),
    node('div', { role: 'dialog' }, [node('button', {}, [], 'Accept all')]),
  ]);
}

test('each item says which part of the page it is in', () => {
  const groups = Object.fromEntries(readTree(resultsPage()).items.map((item) => [item.label, item.group]));
  assert.deepStrictEqual(groups, {
    Home: 'menu', Search: 'menu', Subscriptions: 'menu', 'Sponsored video': 'ad', 'First real video': 'result',
    'Its channel': 'main', 'Chapter two': 'main', 'A short clip': 'short', 'First real video again': 'main', 'Second real video': 'result',
    'Accept all': 'dialog',
  });
});

test('a heading link that repeats an earlier result is not listed as a second result', () => {
  const results = JSON.parse(JSON.stringify(readTree(resultsPage()).items.filter((item) => item.group === 'result').map((item) => item.label)));
  assert.deepStrictEqual(results, ['First real video', 'Second real video']);
});

test('the page text comes from its main part when it has one', () => {
  const page = readTree(resultsPage());
  assert.match(page.text, /^First real video Its channel/);
  assert.doesNotMatch(page.text, /Subscriptions/);
});

test('links say what they lead to when the address shows it', () => {
  const leads = Object.fromEntries(readTree(resultsPage()).items.filter((item) => item.kind === 'link').map((item) => [item.label, item.to]));
  assert.strictEqual(leads['First real video'], 'video');
  assert.strictEqual(leads['Its channel'], 'channel');
  assert.strictEqual(leads['A short clip'], 'short video');
  assert.strictEqual(leads.Home, '');
});

test('a read numbers its items from the number it is given', () => {
  const html = node('html', {}, [node('body', {}, [link('First', '/a'), link('Second', '/b')])]);
  const body = html.children[0];
  const all = descendants(html);
  const context = {
    document: { title: 't', body, querySelector: () => null, querySelectorAll: (selector) => (selector === '[data-arty-n]' ? [] : all.filter((element) => element.tagName === 'A')) },
    location: { href: 'https://video.example/' },
    getComputedStyle: () => ({ visibility: 'visible', display: 'block' }),
  };
  vm.createContext(context);
  vm.runInContext(`${hintsLine}\n${readPageSource}\nresult = readPage(PRIVATE_FIELD_HINTS, 251);`, context);
  assert.deepStrictEqual(JSON.parse(JSON.stringify(context.result.items.map((item) => item.n))), [251, 252]);
});
