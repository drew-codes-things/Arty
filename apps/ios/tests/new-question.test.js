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

test("a remote request from a computer ignores Allow until it has been on screen for a second", async (t) => {
  fakePage();
  stopTheClock(t);
  const { followComputer } = await import("../js/computers.js");
  const page = new FakeElement("div");
  const sent = [];
  const seen = {};
  const reads = [
    { events: [{ seq: 1, type: "permission", request_id: "r1", tool: "bash", text: "rm -rf build" }], next: 1, ended: false,
      session: { name: "Laptop", mode: "default", waiting_for: [] } },
    { events: [], next: 1, ended: true, session: { name: "Laptop", mode: "default", waiting_for: [] } },
  ];
  const api = {
    remoteAnswer: async (session, requestId, allow) => { sent.push([requestId, allow]); },
    remoteEvents: async () => {
      if (reads.length === 1) {
        const allow = findButton(page, "Allow");
        seen.flash = shownText(page);
        seen.waitingLook = allow.getAttribute("aria-disabled");
        await press(allow);
        await press(findButton(page, "Deny"));
        seen.sentAtOnce = sent.length;
        t.mock.timers.tick(QUESTION_PAUSE_MS - 1);
        await press(allow);
        seen.sentJustBefore = sent.length;
        t.mock.timers.tick(1);
        await press(allow);
      }
      return reads.shift();
    },
  };
  await followComputer(page, api, SESSION, () => {});
  assert.match(seen.flash, /New question/);
  assert.equal(seen.waitingLook, "true");
  assert.equal(seen.sentAtOnce, 0);
  assert.equal(seen.sentJustBefore, 0);
  assert.deepEqual(sent, [["r1", true]]);
});

const WAITING_TASK = { id: 7, status: "waiting", title: "Delete old backups", updated: 1, waiting: { kind: "approval", message: "Delete 3 old backups?" } };

test("a waiting task on the Tasks screen ignores Approve, Deny and Cancel for a second", async (t) => {
  fakePage();
  stopTheClock(t);
  const { renderTasks } = await import("../js/manage.js");
  const container = new FakeElement("div");
  const decided = [];
  const api = { tasks: async () => [WAITING_TASK], decideTask: async (id, action) => { decided.push([id, action]); return "ok"; } };
  await renderTasks(container, api);
  assert.match(shownText(container), /New question/);
  for (const label of ["Approve", "Deny", "Cancel task"]) await press(findButton(container, label));
  assert.deepEqual(decided, []);
  t.mock.timers.tick(QUESTION_PAUSE_MS);
  await press(findButton(container, "Approve"));
  assert.deepEqual(decided, [[7, "approve"]]);
});

test("a running task's Cancel is not held back", async (t) => {
  fakePage();
  stopTheClock(t);
  const { renderTasks } = await import("../js/manage.js");
  const container = new FakeElement("div");
  const decided = [];
  const api = { tasks: async () => [{ id: 8, status: "running", title: "Long job", updated: 1 }], decideTask: async (id, action) => { decided.push([id, action]); } };
  await renderTasks(container, api);
  assert.doesNotMatch(shownText(container), /New question/);
  await press(findButton(container, "Cancel"));
  assert.deepEqual(decided, [[8, "cancel"]]);
});

test("an approval in Updates ignores its buttons for a second", async (t) => {
  fakePage();
  stopTheClock(t);
  const { UpdatesFeed } = await import("../js/updates.js");
  const container = new FakeElement("div");
  const decided = [];
  const api = { tasks: async () => [WAITING_TASK], decideTask: async (id, action) => { decided.push([id, action]); return "ok"; } };
  const storage = { getItem: () => null, setItem() {} };
  const feed = new UpdatesFeed({ api, storage, container, badge: new FakeElement("span"), isShowing: () => false });
  feed.items = [{ id: 3, kind: "approval", title: "Approval", message: "Delete 3 old backups?", created: 1, task_id: 7, actions: ["approve", "deny"] }];
  feed.waitingTaskIds = new Set([7]);
  feed.render();
  assert.match(shownText(container), /New question/);
  await press(findButton(container, "Approve"));
  await press(findButton(container, "Deny"));
  assert.deepEqual(decided, []);
  t.mock.timers.tick(QUESTION_PAUSE_MS);
  await press(findButton(container, "Deny"));
  assert.deepEqual(decided, [[7, "deny"]]);
});

test("the second only starts once the question is on screen", async (t) => {
  fakePage();
  stopTheClock(t);
  const observers = [];
  globalThis.IntersectionObserver = class {
    constructor(callback) { this.callback = callback; this.watched = []; observers.push(this); }
    observe(element) { this.watched.push(element); }
    disconnect() { this.watched = []; }
  };
  const { renderTasks } = await import("../js/manage.js");
  const container = new FakeElement("div");
  const decided = [];
  await renderTasks(container, { tasks: async () => [WAITING_TASK], decideTask: async (id, action) => { decided.push([id, action]); } });
  t.mock.timers.tick(5 * QUESTION_PAUSE_MS);
  await press(findButton(container, "Approve"));
  assert.deepEqual(decided, [], "a question scrolled out of sight is not answerable");
  const [observer] = observers;
  observer.callback(observer.watched.map((target) => ({ target, isIntersecting: true })));
  t.mock.timers.tick(QUESTION_PAUSE_MS - 1);
  await press(findButton(container, "Approve"));
  assert.deepEqual(decided, []);
  t.mock.timers.tick(1);
  await press(findButton(container, "Approve"));
  assert.deepEqual(decided, [[7, "approve"]]);
  delete globalThis.IntersectionObserver;
});

test("the flash and the waiting look go after a second", async (t) => {
  fakePage();
  t.mock.timers.enable({ apis: ["Date", "setTimeout"], now: 1_000_000 });
  const { answerGuard, flashNewQuestion } = await import("../js/answerguard.js");
  const card = new FakeElement("article");
  const allow = new FakeElement("button");
  card.append(allow);
  const guard = answerGuard();
  flashNewQuestion(card, [allow], guard);
  assert.equal(card.classList.contains("answer-waiting"), true);
  assert.match(shownText(card), /New question/);
  assert.equal(allow.getAttribute("aria-disabled"), "true");
  assert.equal(guard.isReady(), false);
  t.mock.timers.tick(QUESTION_PAUSE_MS);
  assert.equal(guard.isReady(), true);
  assert.equal(card.classList.contains("answer-waiting"), false);
  assert.doesNotMatch(shownText(card), /New question/);
  assert.equal(allow.getAttribute("aria-disabled"), null);
});
