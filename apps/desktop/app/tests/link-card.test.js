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

function question(card) {
  return allElements(card).find((element) => element.tagName === 'p');
}

function loadQuestions(t) {
  t.mock.timers.enable({ apis: ['Date', 'setTimeout'], now: 1_000_000 });
  const context = { document: { createElement: (tag) => new FakeElement(tag), addEventListener() {} }, Date, setTimeout };
  vm.createContext(context);
  vm.runInContext(read('questions.js'), context);
  return context;
}

const STEP = 'starfire Desktop asks to do this on your computer: run a command (computer_run_command).\ncommand: ls\nAllow it?';

async function pressAfterPause(t, card, label) {
  t.mock.timers.tick(QUESTION_PAUSE_MS);
  button(card, label).listeners.click();
  await new Promise((resolve) => setImmediate(resolve));
}

test('a linked app card Arty no longer waits on says it was too late, never allowed', async (t) => {
  const { linkQuestionCard } = loadQuestions(t);
  const card = linkQuestionCard(STEP, async () => false);
  await pressAfterPause(t, card, 'Allow');
  assert.equal(question(card).textContent, `${STEP}  (too late, nothing was done)`);
  assert.doesNotMatch(question(card).textContent, /\(allowed\)/);
});

test('a linked app card says allowed only once Arty took the answer', async (t) => {
  const { linkQuestionCard } = loadQuestions(t);
  let accept;
  const card = linkQuestionCard(STEP, () => new Promise((resolve) => { accept = resolve; }));
  await pressAfterPause(t, card, 'Allow');
  assert.doesNotMatch(question(card).textContent, /\(allowed\)/);
  accept(true);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(question(card).textContent, `${STEP}  (allowed)`);
});

test('a linked app card keeps its lines and is marked as one', (t) => {
  const { linkQuestionCard } = loadQuestions(t);
  const card = linkQuestionCard(STEP, async () => true);
  assert.equal(card.classList.contains('link-ask'), true);
  assert.equal(question(card).textContent, STEP);
  assert.match(read('style.css'), /\.ask\.link-ask p \{[^}]*white-space: pre-wrap/);
});

test('the window shows link questions on the link card and reads whether Arty took the answer', () => {
  const script = read('app.js');
  const start = script.indexOf('function showLinkQuestion');
  const body = script.slice(start, script.indexOf('function notifyLinkQuestion', start));
  assert.match(body, /linkQuestionCard\(/);
  assert.match(body, /\.ok === true/);
});

const MARKUP_STEP = 'Let Arty click “<img src=x onerror=alert(1)> 3 < 5 > 2”?';

test('step and link cards show < and > exactly as written, as text and never as markup', (t) => {
  const { stepQuestionCard, linkQuestionCard } = loadQuestions(t);
  for (const card of [stepQuestionCard(MARKUP_STEP, () => {}), linkQuestionCard(MARKUP_STEP, async () => true)]) {
    assert.equal(question(card).textContent, MARKUP_STEP);
    assert.equal(allElements(card).some((element) => element.tagName === 'img'), false);
  }
  assert.doesNotMatch(read('questions.js'), /innerHTML|insertAdjacentHTML/);
});
