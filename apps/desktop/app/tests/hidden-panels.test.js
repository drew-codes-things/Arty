'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const rendererFolder = path.join(__dirname, '..', 'renderer');
const CSS_COMMENT = new RegExp('/' + '\\*[\\s\\S]*?\\*' + '/', 'g');
const NEGATION = /:not\([^)]*\)/g;

function startsHidden(tag) {
  for (const [, name] of tag.matchAll(/(?:^|\s)([a-zA-Z-]+)(?:=(?:"[^"]*"|'[^']*'|[^\s>]*))?/g)) {
    if (name === 'hidden') return true;
  }
  return false;
}

function selectorsOfHiddenElements(page) {
  const selectors = new Set();
  for (const [, attributes] of page.matchAll(/<[a-z0-9]+\s([^>]*)>/gi)) {
    if (!startsHidden(attributes)) continue;
    const named = attributes.match(/\bclass="([^"]+)"/);
    if (named) named[1].split(/\s+/).forEach((name) => selectors.add(`.${name}`));
    const identified = attributes.match(/\bid="([^"]+)"/);
    if (identified) selectors.add(`#${identified[1]}`);
  }
  return [...selectors];
}

function rulesOf(styles) {
  const rules = [];
  const plain = styles.replace(CSS_COMMENT, '');
  for (const [, selectors, body] of plain.matchAll(/([^{}@]+)\{([^{}]*)\}/g)) {
    for (const selector of selectors.split(',')) rules.push({ selector: selector.trim(), body });
  }
  return rules;
}

function showsElement(body) {
  return /(^|[;\s])display\s*:(?!\s*none)/.test(body);
}

function mentions(selector, token) {
  const escaped = token.replace(/[.#]/g, '\\$&');
  return new RegExp(`${escaped}(?![\\w-])`).test(selector.replace(NEGATION, ''));
}

function classesThatCannotBeHidden(page, styles) {
  const rules = rulesOf(styles);
  return selectorsOfHiddenElements(page).filter((token) => {
    const setsDisplay = rules.some((rule) => mentions(rule.selector, token) && !rule.selector.includes('[hidden]') && showsElement(rule.body));
    const keptHidden = rules.some((rule) => mentions(rule.selector, token) && rule.selector.includes('[hidden]') && /display\s*:\s*none/.test(rule.body));
    return setsDisplay && !keptHidden;
  }).map((token) => (token.startsWith('#') ? token : token.slice(1)));
}

test('every element that starts hidden has a stylesheet rule that keeps it hidden', () => {
  const page = fs.readFileSync(path.join(rendererFolder, 'index.html'), 'utf8');
  const styles = fs.readFileSync(path.join(rendererFolder, 'style.css'), 'utf8');
  assert.ok(selectorsOfHiddenElements(page).length > 0, 'the page has hidden elements to check');
  assert.deepEqual(classesThatCannotBeHidden(page, styles), []);
});

test('a display rule in a selector list is caught', () => {
  const page = '<section class="panel box" hidden></section>';
  assert.deepEqual(classesThatCannotBeHidden(page, '.other, .box { display: flex; }'), ['box']);
});

test('a display rule on a descendant selector is caught', () => {
  const page = '<div class="box" hidden></div>';
  assert.deepEqual(classesThatCannotBeHidden(page, '.panel .box { display: grid; }'), ['box']);
});

test('a display rule inside a media block is caught', () => {
  const page = '<div class="box" hidden></div>';
  assert.deepEqual(classesThatCannotBeHidden(page, '@media (min-width: 10px) { .box { display: flex; } }'), ['box']);
});

test('a hidden rule that keeps it hidden is accepted', () => {
  const page = '<div class="box" hidden></div>';
  assert.deepEqual(classesThatCannotBeHidden(page, '.box { display: flex; }\n.box[hidden] { display: none; }'), []);
});

test('names that only contain the word hidden do not count', () => {
  const page = '<div class="box" aria-hidden="true"></div><div class="hidden-note"></div>';
  assert.deepEqual(classesThatCannotBeHidden(page, '.box { display: flex; } .hidden-note { display: flex; }'), []);
});

test('a hidden element known only by its id is checked too', () => {
  const page = '<section id="drawer" hidden></section>';
  assert.deepEqual(classesThatCannotBeHidden(page, '#drawer { display: flex; }'), ['#drawer']);
});

test('a hidden id with a rule that keeps it hidden is accepted', () => {
  const page = '<section id="drawer" hidden></section>';
  assert.deepEqual(classesThatCannotBeHidden(page, '#drawer { display: flex; }\n#drawer[hidden] { display: none; }'), []);
});

test('a rule that already says display none does not count as showing the element', () => {
  const page = '<div class="box" hidden></div>';
  assert.deepEqual(classesThatCannotBeHidden(page, '.box { display: none; }'), []);
  assert.deepEqual(classesThatCannotBeHidden(page, '.box {display:none}'), []);
});
