import assert from "node:assert/strict";
import { test } from "node:test";

const QUESTION_PAUSE_MS = 1000;
const SESSION = "0123456789abcdef";

class FakeElement {
  constructor(tag) {
    this.tag = tag;
    this.children = [];
    this.listeners = {};
    this.attributes = {};
    this.textContent = "";
    this.hidden = false;
    this.disabled = false;
    this.isConnected = true;
    this.parent = null;
    this.dataset = {};
    this.classes = new Set();
    const element = this;
    this.classList = {
      add(name) { element.classes.add(name); },
      remove(name) { element.classes.delete(name); },
      contains(name) { return element.classes.has(name); },
      toggle(name, on) { if (on) element.classes.add(name); else element.classes.delete(name); },
    };
  }
  set className(value) { this.classes = new Set(String(value).split(/\s+/).filter(Boolean)); }
  get className() { return [...this.classes].join(" "); }
  setAttribute(name, value) { this.attributes[name] = value; }
  getAttribute(name) { return this.attributes[name] ?? null; }
  removeAttribute(name) { delete this.attributes[name]; }
  addEventListener(type, listener) { this.listeners[type] = listener; }
  adopt(children) {
    for (const child of children) if (child instanceof FakeElement) child.parent = this;
    return children;
  }
  append(...children) { this.children.push(...this.adopt(children)); }
  replaceChildren(...children) { this.children = this.adopt(children); }
  replaceWith() {}
  remove() { if (this.parent) this.parent.children = this.parent.children.filter((child) => child !== this); }
  get label() { return this.textContent || this.children.filter((child) => typeof child === "string").join(""); }
}

function fakePage() {
  globalThis.Node = FakeElement;
  globalThis.document = {
    createElement: (tag) => new FakeElement(tag),
    getElementById: () => new FakeElement("div"),
  };
  delete globalThis.IntersectionObserver;
}

function allElements(element) {
  return (element.children ?? []).filter((child) => typeof child !== "string").flatMap((child) => [child, ...allElements(child)]);
}

function findButton(root, label) {
  return allElements(root).find((element) => element.tag === "button" && element.label === label) ?? null;
}

function shownText(root) {
  return allElements(root).map((element) => element.label).join(" ");
}

function press(button) {
  return button.listeners.click({ currentTarget: button });
}

function stopTheClock(t) {
  t.mock.timers.enable({ apis: ["Date"], now: 1_000_000 });
}

function pagerLabel(root) {
  return allElements(root).find((element) => element.tag === "span" && /^\d+ of \d+$/.test(element.label))?.label ?? null;
}

function byLabel(root, label) {
  return allElements(root).find((element) => element.getAttribute("aria-label") === label) ?? null;
}

function pressKey(container, key, target = {}) {
  const event = { key, target, prevented: false, preventDefault() { this.prevented = true; } };
  container.listeners.keydown(event);
  return event;
}

function deckOfThree() {
  const container = new FakeElement("div");
  const cards = ["one", "two", "three"].map((name) => new FakeElement(name));
  return { container, cards };
}

test("several waiting cards are paged with a count and previous and next buttons", async () => {
  fakePage();
  const { cardDeck } = await import("../js/cardpager.js");
  const { container, cards } = deckOfThree();
  const deck = cardDeck(container);
  deck.show(cards);
  assert.deepEqual(cards.map((card) => card.hidden), [false, true, true]);
  assert.equal(pagerLabel(container), "1 of 3");
  assert.equal(byLabel(container, "Previous question").getAttribute("aria-disabled"), "true");
  await press(byLabel(container, "Next question"));
  assert.deepEqual(cards.map((card) => card.hidden), [true, false, true]);
  assert.equal(pagerLabel(container), "2 of 3");
  await press(byLabel(container, "Next question"));
  await press(byLabel(container, "Next question"));
  assert.deepEqual(cards.map((card) => card.hidden), [true, true, false]);
  assert.equal(byLabel(container, "Next question").getAttribute("aria-disabled"), "true");
  await press(byLabel(container, "Previous question"));
  assert.deepEqual(cards.map((card) => card.hidden), [true, false, true]);
});

test("one waiting card shows no pager", async () => {
  fakePage();
  const { cardDeck } = await import("../js/cardpager.js");
  const container = new FakeElement("div");
  const card = new FakeElement("one");
  cardDeck(container).show([card]);
  assert.equal(container.children[0].hidden, true);
  assert.equal(card.hidden, false);
});

test("the left and right arrow keys page the cards", async () => {
  fakePage();
  const { cardDeck } = await import("../js/cardpager.js");
  const { container, cards } = deckOfThree();
  cardDeck(container).show(cards);
  assert.equal(pressKey(container, "ArrowRight").prevented, true);
  assert.deepEqual(cards.map((card) => card.hidden), [true, false, true]);
  pressKey(container, "ArrowLeft");
  assert.deepEqual(cards.map((card) => card.hidden), [false, true, true]);
  assert.equal(pressKey(container, "ArrowRight", { tagName: "TEXTAREA" }).prevented, false);
  assert.equal(pressKey(container, "a").prevented, false);
  assert.deepEqual(cards.map((card) => card.hidden), [false, true, true]);
});

test("dropping an answered card moves on to the next one", async () => {
  fakePage();
  const { cardDeck } = await import("../js/cardpager.js");
  const { container, cards } = deckOfThree();
  const deck = cardDeck(container);
  deck.show(cards);
  await press(byLabel(container, "Next question"));
  deck.drop(cards[1]);
  assert.deepEqual([cards[0].hidden, cards[2].hidden], [true, false]);
  assert.equal(pagerLabel(container), "2 of 2");
  deck.drop(cards[2]);
  assert.equal(cards[0].hidden, false);
  assert.equal(container.children[0].hidden, true);
});

test("allowing a request from a computer shows the next waiting one", async (t) => {
  fakePage();
  stopTheClock(t);
  const { followComputer } = await import("../js/computers.js");
  const page = new FakeElement("div");
  const sent = [];
  const waitingOn = (id) => ({ seq: Number(id.slice(1)), type: "permission", request_id: id, tool: "bash", text: `run ${id}` });
  const reads = [
    { events: [waitingOn("r1"), waitingOn("r2")], next: 2, ended: false, session: { name: "Laptop", mode: "default", waiting_for: [] } },
    { events: [], next: 2, ended: true, session: { name: "Laptop", mode: "default", waiting_for: [] } },
  ];
  const seen = {};
  const api = {
    remoteAnswer: async (session, requestId, allow) => { sent.push([requestId, allow]); },
    remoteEvents: async () => {
      if (reads.length === 1) {
        const cardsNow = () => allElements(page).filter((element) => element.classes.has("remote-permission"));
        seen.before = cardsNow().map((card) => card.hidden);
        seen.label = pagerLabel(page);
        t.mock.timers.tick(QUESTION_PAUSE_MS);
        await press(findButton(cardsNow()[0], "Allow"));
        seen.after = cardsNow().map((card) => card.hidden);
        seen.afterLabel = pagerLabel(page);
      }
      return reads.shift();
    },
  };
  await followComputer(page, api, SESSION, () => {});
  assert.deepEqual(seen.before, [false, true]);
  assert.equal(seen.label, "1 of 2");
  assert.deepEqual(seen.after, [false]);
  assert.equal(seen.afterLabel, "1 of 1");
  assert.deepEqual(sent, [["r1", true]]);
});

const MARKUP = "Let Arty click <img src=x onerror=alert(1)> 3 < 5 > 2?";

test("a waiting request shows < and > as written, as text and never as markup", async (t) => {
  fakePage();
  stopTheClock(t);
  const { permissionPrompt } = await import("../js/remote.js");
  assert.equal(permissionPrompt({ tool: "", text: `${MARKUP}\nline <b>two</b>` }).heading, MARKUP);
  assert.equal(permissionPrompt({ tool: "", text: `${MARKUP}\nline <b>two</b>` }).preview, "line <b>two</b>");
  const { followComputer } = await import("../js/computers.js");
  const page = new FakeElement("div");
  const reads = [
    { events: [{ seq: 1, type: "permission", request_id: "r1", tool: "", text: MARKUP }], next: 1, ended: false, session: { name: "Laptop", mode: "default", waiting_for: [] } },
    { events: [], next: 1, ended: true, session: { name: "Laptop", mode: "default", waiting_for: [] } },
  ];
  let shown;
  const api = {
    remoteEvents: async () => {
      if (reads.length === 1) shown = { text: allElements(page).map((element) => element.label).join(" "), tags: allElements(page).map((element) => element.tag) };
      return reads.shift();
    },
  };
  await followComputer(page, api, SESSION, () => {});
  assert.ok(shown.text.includes(MARKUP));
  assert.equal(shown.tags.includes("img"), false);
});
