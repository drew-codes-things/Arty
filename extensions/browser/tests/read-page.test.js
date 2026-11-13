const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'background.js'), 'utf8');
const readPageSource = source.slice(source.indexOf('function readPage('), source.indexOf('function clickItem('));
const hintsLine = source.match(/^const PRIVATE_FIELD_HINTS = .*$/m)[0];

function field({ type = 'text', name = '', id = '', autocomplete = '', label = '', value = '' }) {
  const attributes = { type, autocomplete, 'aria-label': label };
  return {
    tagName: 'INPUT', name, id, value, placeholder: '', isContentEditable: false, labels: [],
    getBoundingClientRect: () => ({ width: 10, height: 10 }),
    getAttribute: (key) => attributes[key] ?? null,
    setAttribute: () => {}, removeAttribute: () => {},
  };
}

function readPageWith(elements) {
  const context = {
    document: { title: 'Checkout', body: { innerText: 'Pay now' }, querySelector: () => null, querySelectorAll: (selector) => (selector === '[data-arty-n]' ? [] : elements) },
    location: { href: 'https://shop.example/checkout' },
    getComputedStyle: () => ({ visibility: 'visible', display: 'block' }),
  };
  vm.createContext(context);
  vm.runInContext(`${hintsLine}\n${readPageSource}\nresult = readPage(PRIVATE_FIELD_HINTS);`, context);
  return JSON.stringify(context.result.items);
}

test('values typed into private fields never reach Arty', () => {
  const listing = readPageWith([
    field({ name: 'cardnumber', autocomplete: 'cc-number', label: 'Card number', value: '4111111111111111' }),
    field({ name: 'otp', autocomplete: 'one-time-code', label: 'Code', value: '123456' }),
    field({ type: 'text', name: 'password', label: 'Password', value: 'hunter2' }),
    field({ name: 'cvc', label: 'Security code', value: '737' }),
    field({ name: 'iban', label: 'IBAN', value: 'GB00TEST' }),
  ]);
  for (const secret of ['4111111111111111', '123456', 'hunter2', '737', 'GB00TEST']) assert.ok(!listing.includes(secret), secret);
});

test('ordinary fields still show what is in them', () => {
  assert.match(readPageWith([field({ name: 'q', label: 'Search', value: 'weather' })]), /"value":"weather"/);
});
