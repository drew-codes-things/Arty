'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const rendererFolder = path.join(__dirname, '..', 'renderer');
const read = (name) => fs.readFileSync(path.join(rendererFolder, name), 'utf8');
const QUESTION_PAUSE_MS = 1000;

class FakeElement {
  constructor(tag) {
    this.tagName = tag;
    this.children = [];
    this.listeners = {};
    this.attributes = {};
    this.textContent = '';
    this.parent = null;
    this.classes = new Set();
    const element = this;
    this.classList = {
      add(name) { element.classes.add(name); },
      remove(name) { element.classes.delete(name); },
      contains(name) { return element.classes.has(name); },
    };
  }

  set className(value) { this.classes = new Set(String(value).split(/\s+/).filter(Boolean)); }

  get className() { return [...this.classes].join(' '); }

  setAttribute(name, value) { this.attributes[name] = value; }

  getAttribute(name) { return this.attributes[name] ?? null; }

  removeAttribute(name) { delete this.attributes[name]; }

  append(...children) {
    for (const child of children) child.parent = this;
    this.children.push(...children);
  }

  remove() { if (this.parent) this.parent.children = this.parent.children.filter((child) => child !== this); }

  addEventListener(kind, listener) { this.listeners[kind] = listener; }
}

function allElements(element) {
  return element.children.flatMap((child) => [child, ...allElements(child)]);
}

function button(card, label) {
  return allElements(card).find((element) => element.tagName === 'button' && element.textContent === label) ?? null;
}

function shownText(card) {
  return [card, ...allElements(card)].map((element) => element.textContent).join(' ');
}

function loadQuestions(t, extra = {}) {
  t.mock.timers.enable({ apis: ['Date', 'setTimeout'], now: 1_000_000 });
  const keyListeners = [];
  const document = { createElement: (tag) => new FakeElement(tag), addEventListener: (kind, listener) => keyListeners.push(listener) };
  const context = { document, Date, setTimeout, ...extra };
  context.pressKey = (key, target = {}) => {
    const event = { key, target, prevented: false, preventDefault() { this.prevented = true; } };
    keyListeners.forEach((listener) => listener(event));
    return event;
  };
  vm.createContext(context);
  vm.runInContext(read('questions.js'), context);
  return context;
}

test('a step question ignores Allow and Not now until it has been on screen for a second', (t) => {
  const { stepQuestionCard } = loadQuestions(t);
  const answers = [];
  const card = stepQuestionCard('Arty wants to click "Buy now" on shop.example', (ok) => answers.push(ok));
  assert.match(shownText(card), /New question/);
  assert.equal(button(card, 'Allow').getAttribute('aria-disabled'), 'true');
  button(card, 'Allow').listeners.click();
  button(card, 'Not now').listeners.click();
  t.mock.timers.tick(QUESTION_PAUSE_MS - 1);
  button(card, 'Allow').listeners.click();
  assert.deepEqual(answers, []);
  t.mock.timers.tick(1);
  assert.doesNotMatch(shownText(card), /New question/);
  assert.equal(button(card, 'Allow').getAttribute('aria-disabled'), null);
  button(card, 'Allow').listeners.click();
  assert.deepEqual(answers, [true]);
  assert.match(shownText(card), /\(allowed\)/);
  assert.equal(button(card, 'Not now'), null);
});

test('a paused task card ignores its buttons for a second, then sends one answer', (t) => {
  const { taskAnswerCard } = loadQuestions(t);
  const sent = [];
  const card = taskAnswerCard(['approve', 'deny', 'unknown'], (action, result) => sent.push([action, result.textContent]));
  assert.deepEqual(allElements(card).filter((element) => element.tagName === 'button').map((element) => element.textContent), ['Approve', 'Deny']);
  button(card, 'Approve').listeners.click();
  assert.deepEqual(sent, []);
  t.mock.timers.tick(QUESTION_PAUSE_MS);
  button(card, 'Deny').listeners.click();
  assert.deepEqual(sent, [['deny', '…']]);
  assert.equal(card.classList.contains('done'), true);
});

test('the second only starts once the question is on screen', (t) => {
  const observers = [];
  class FakeObserver {
    constructor(callback) { this.callback = callback; this.watched = []; observers.push(this); }

    observe(element) { this.watched.push(element); }

    disconnect() { this.watched = []; }
  }
  const { stepQuestionCard } = loadQuestions(t, { IntersectionObserver: FakeObserver });
  const answers = [];
  const card = stepQuestionCard('Arty wants to type in the search box', (ok) => answers.push(ok));
  t.mock.timers.tick(5 * QUESTION_PAUSE_MS);
  button(card, 'Allow').listeners.click();
  assert.deepEqual(answers, []);
  const [observer] = observers;
  observer.callback(observer.watched.map((target) => ({ target, isIntersecting: true })));
  t.mock.timers.tick(QUESTION_PAUSE_MS);
  button(card, 'Allow').listeners.click();
  assert.deepEqual(answers, [true]);
});

function pagerLabel(card) {
  return allElements(card).find((element) => element.tagName === 'span' && /^\d+ of \d+$/.test(element.textContent))?.textContent ?? null;
}

function pagerButton(card, label) {
  return allElements(card).find((element) => element.getAttribute('aria-label') === label) ?? null;
}

function showingCards(cards) {
  return cards.filter((card) => !card.hidden);
}

test('several waiting questions are paged with a count and previous and next buttons', (t) => {
  const { stepQuestionCard } = loadQuestions(t);
  const cards = ['one', 'two', 'three'].map((name) => stepQuestionCard(`Arty wants to ${name}`, () => {}));
  assert.deepEqual(showingCards(cards), [cards[0]]);
  assert.equal(pagerLabel(cards[0]), '1 of 3');
  assert.equal(pagerButton(cards[0], 'Previous question').getAttribute('aria-disabled'), 'true');
  assert.equal(pagerButton(cards[0], 'Next question').getAttribute('aria-disabled'), 'false');
  pagerButton(cards[0], 'Next question').listeners.click();
  assert.deepEqual(showingCards(cards), [cards[1]]);
  assert.equal(pagerLabel(cards[1]), '2 of 3');
  pagerButton(cards[1], 'Next question').listeners.click();
  pagerButton(cards[2], 'Next question').listeners.click();
  assert.deepEqual(showingCards(cards), [cards[2]]);
  assert.equal(pagerButton(cards[2], 'Next question').getAttribute('aria-disabled'), 'true');
  pagerButton(cards[2], 'Previous question').listeners.click();
  assert.deepEqual(showingCards(cards), [cards[1]]);
});

test('a single waiting question shows no pager', (t) => {
  const { stepQuestionCard } = loadQuestions(t);
  const card = stepQuestionCard('Arty wants to open a page', () => {});
  const bar = allElements(card).find((element) => element.className === 'card-pager');
  assert.equal(bar.hidden, true);
  assert.equal(card.hidden, false);
});

test('the left and right arrow keys page the questions', (t) => {
  const { stepQuestionCard, pressKey } = loadQuestions(t);
  const cards = ['one', 'two'].map((name) => stepQuestionCard(`Arty wants to ${name}`, () => {}));
  assert.equal(pressKey('ArrowRight').prevented, true);
  assert.deepEqual(showingCards(cards), [cards[1]]);
  pressKey('ArrowLeft');
  assert.deepEqual(showingCards(cards), [cards[0]]);
  assert.equal(pressKey('ArrowRight', { tagName: 'TEXTAREA' }).prevented, false);
  assert.deepEqual(showingCards(cards), [cards[0]]);
  assert.equal(pressKey('a').prevented, false);
});

test('answering a question moves on to the next one and the count drops', (t) => {
  const { stepQuestionCard } = loadQuestions(t);
  const answers = [];
  const cards = ['one', 'two', 'three'].map((name) => stepQuestionCard(`Arty wants to ${name}`, (ok) => answers.push([name, ok])));
  pagerButton(cards[0], 'Next question').listeners.click();
  t.mock.timers.tick(QUESTION_PAUSE_MS);
  button(cards[1], 'Not now').listeners.click();
  assert.deepEqual(answers, [['two', false]]);
  assert.deepEqual(showingCards(cards), [cards[1], cards[2]]);
  assert.equal(pagerLabel(cards[2]), '2 of 2');
  assert.equal(cards[0].hidden, true);
  button(cards[2], 'Allow').listeners.click();
  assert.deepEqual(answers, [['two', false], ['three', true]]);
  assert.deepEqual(showingCards(cards), cards);
  assert.equal(cards[0].hidden, false);
  assert.equal(pagerLabel(cards[0]), '1 of 1');
});

test('the window loads the question cards before the app and uses them for every question', () => {
  const page = read('index.html');
  assert.ok(page.indexOf('src="questions.js"') >= 0 && page.indexOf('src="questions.js"') < page.indexOf('src="app.js"'));
  const renderer = read('app.js');
  assert.match(renderer, /function addQuestion\([^)]*\) \{[^}]*stepQuestionCard\(/);
  assert.match(renderer, /function addTaskButtons\([^)]*\) \{[^]*?taskAnswerCard\(/);
  assert.doesNotMatch(renderer, /createElement\('button'\)[^]*?Allow/);
});
