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
  const context = { document: { createElement: (tag) => new FakeElement(tag) }, Date, setTimeout, ...extra };
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

test('the window loads the question cards before the app and uses them for every question', () => {
  const page = read('index.html');
  assert.ok(page.indexOf('src="questions.js"') >= 0 && page.indexOf('src="questions.js"') < page.indexOf('src="app.js"'));
  const renderer = read('app.js');
  assert.match(renderer, /function addQuestion\([^)]*\) \{[^}]*stepQuestionCard\(/);
  assert.match(renderer, /function addTaskButtons\([^)]*\) \{[^]*?taskAnswerCard\(/);
  assert.doesNotMatch(renderer, /createElement\('button'\)[^]*?Allow/);
});
