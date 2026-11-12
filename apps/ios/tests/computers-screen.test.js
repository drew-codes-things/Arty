import assert from "node:assert/strict";
import { test } from "node:test";

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
    this.classList = { add() {}, remove() {} };
    this.dataset = {};
  }
  setAttribute(name, value) { this.attributes[name] = value; }
  addEventListener(type, listener) { this.listeners[type] = listener; }
  append(child) { this.children.push(child); }
  replaceChildren(...children) { this.children = children; }
  get label() { return this.textContent || this.children.filter((child) => typeof child === "string").join(""); }
  get innerHTML() { throw new Error("remote text must not go through innerHTML"); }
  set innerHTML(value) { throw new Error("remote text must not go through innerHTML"); }
}

function fakePage() {
  const made = [];
  globalThis.Node = FakeElement;
  globalThis.document = {
    createElement(tag) { const element = new FakeElement(tag); made.push(element); return element; },
    getElementById() { return new FakeElement("div"); },
  };
  return made;
}

function texts(element) {
  return element.children.flatMap((child) => (typeof child === "string" ? [child] : [element.textContent, ...texts(child)]));
}

const SESSION = "0123456789abcdef";

test("following a computer shows its lines, lets a waiting request be allowed and stops when it ends", async () => {
  const made = fakePage();
  const { followComputer } = await import("../js/computers.js");
  const answers = [];
  const reads = [
    { events: [{ seq: 1, type: "user", text: "fix the test" }, { seq: 2, type: "permission", request_id: "r1", tool: "bash", text: "pytest" }],
      next: 2, ended: false, session: { name: "Laptop", mode: "default", waiting_for: [] } },
    { events: [{ seq: 3, type: "assistant", text: "<b>All green</b>" }, { seq: 4, type: "end" }], next: 4, ended: true,
      session: { name: "Laptop", mode: "default", waiting_for: [] } },
  ];
  let allowDuringRun;
  const api = {
    remoteEvents: async () => {
      if (reads.length === 1) allowDuringRun = made.filter((element) => element.tag === "button" && element.label === "Allow").at(-1);
      return reads.shift();
    },
    remoteAnswer: async (session, requestId, allow) => { answers.push([session, requestId, allow]); },
  };
  const page = new FakeElement("div");
  await followComputer(page, api, SESSION, () => {});
  assert.ok(allowDuringRun, "an Allow button was shown for the waiting request");
  await allowDuringRun.listeners.click();
  assert.deepEqual(answers, [[SESSION, "r1", true]]);
  const shown = texts(page).join(" ");
  assert.match(shown, /fix the test/);
  assert.match(shown, /<b>All green<\/b>/);
  assert.match(shown, /ended/);
});

test("a device without remote control is told how to turn it on", async () => {
  fakePage();
  const { renderComputers } = await import("../js/computers.js");
  const container = new FakeElement("div");
  const refused = Object.assign(new Error("not allowed"), { status: 403 });
  await renderComputers(container, { remoteSessions: async () => { throw refused; } });
  assert.match(texts(container).join(" "), /Arty devices page/);
});

function sessionRead(fields) {
  return { events: [], ended: false, session: { name: "Laptop", mode: "default", waiting_for: [] }, ...fields };
}

test("a phone that fell behind says so and starts again from where the computer is now", async () => {
  fakePage();
  const { followComputer } = await import("../js/computers.js");
  const page = new FakeElement("div");
  const reads = [
    sessionRead({ events: [{ seq: 1, type: "user", text: "old line" }, { seq: 2, type: "permission", request_id: "r1", tool: "pytest" }], next: 2 }),
    sessionRead({ missed: true, events: [{ seq: 900, type: "assistant", text: "new line" }], next: 900,
      session: { name: "Laptop", mode: "default", waiting_for: [{ request_id: "r9", tool: "ls", text: "." }] } }),
    sessionRead({ next: 900, ended: true }),
  ];
  let transcriptAfterGap;
  let waitingAfterGap;
  const api = {
    remoteEvents: async () => {
      if (reads.length === 1) {
        transcriptAfterGap = texts(page.children[2]).join(" ");
        waitingAfterGap = texts(page.children[3]).join(" ");
      }
      return reads.shift();
    },
  };
  await followComputer(page, api, SESSION, () => {});
  assert.match(transcriptAfterGap, /missed/);
  assert.match(transcriptAfterGap, /new line/);
  assert.doesNotMatch(transcriptAfterGap, /old line/);
  assert.match(waitingAfterGap, /ls/);
  assert.doesNotMatch(waitingAfterGap, /pytest/);
});

test("only the latest lines are kept on screen", async () => {
  fakePage();
  const { followComputer } = await import("../js/computers.js");
  const { MAX_KEPT_ITEMS: MAX_KEPT_EVENTS } = await import("../js/remote.js");
  const page = new FakeElement("div");
  const total = MAX_KEPT_EVENTS + 5;
  const events = Array.from({ length: total }, (_, index) => ({ seq: index + 1, type: "user", text: `line ${index + 1}` }));
  const reads = [sessionRead({ events: events.slice(0, 3), next: 3 }), sessionRead({ events: events.slice(3), next: total, ended: true })];
  await followComputer(page, { remoteEvents: async () => reads.shift() }, SESSION, () => {});
  const transcript = page.children[2];
  assert.equal(transcript.children.length, MAX_KEPT_EVENTS);
  assert.deepEqual(texts(transcript.children[0]).filter(Boolean).at(-1), "line 6");
  assert.deepEqual(texts(transcript.children.at(-1)).filter(Boolean).at(-1), `line ${total}`);
});

test("only as many waiting requests are kept as the brain keeps open", async () => {
  fakePage();
  const { followComputer } = await import("../js/computers.js");
  const page = new FakeElement("div");
  const asked = 25;
  const events = Array.from({ length: asked }, (_, index) => ({ seq: index + 1, type: "permission", request_id: `r${index + 1}`, tool: `tool${index + 1}` }));
  const reads = [sessionRead({ events, next: asked }), sessionRead({ next: asked, ended: true })];
  let cards;
  const api = {
    remoteEvents: async () => {
      if (reads.length === 1) cards = page.children[3].children.map((card) => texts(card).join(" "));
      return reads.shift();
    },
  };
  await followComputer(page, api, SESSION, () => {});
  assert.equal(cards.length, 20);
  assert.match(cards[0], /tool6\b/);
  assert.match(cards.at(-1), /tool25\b/);
});

function findButton(element, label) {
  for (const child of element.children ?? []) {
    if (typeof child === "string") continue;
    if (child.tag === "button" && child.label === label) return child;
    const found = findButton(child, label);
    if (found) return found;
  }
  return null;
}

function shownCards(page) {
  return page.children[3].children.filter((card) => !card.hidden);
}

function allText(element) {
  return [element.textContent, ...element.children.flatMap((child) => (typeof child === "string" ? [child] : allText(child)))].join(" ");
}

function settle() {
  return new Promise((resolve) => { setImmediate(resolve); });
}

const ASKING = sessionRead({ events: [{ seq: 1, type: "permission", request_id: "r1", tool: "bash", text: "pytest" }], next: 1 });

test("an answer from the phone is sent once and its card goes as soon as it is accepted", async () => {
  fakePage();
  const { followComputer } = await import("../js/computers.js");
  const page = new FakeElement("div");
  const answers = [];
  let accept;
  let state = {};
  const reads = [ASKING, sessionRead({ next: 1 }), sessionRead({ next: 1, ended: true })];
  const api = {
    remoteAnswer: (session, requestId, allow) => {
      answers.push([requestId, allow]);
      return new Promise((resolve) => { accept = resolve; });
    },
    remoteEvents: async () => {
      if (reads.length === 2) {
        const allow = findButton(page.children[3], "Allow");
        allow.listeners.click();
        allow.listeners.click();
        findButton(page.children[3], "Deny").listeners.click();
        state.disabledWhileSending = allow.disabled;
        accept({ ok: true });
        await settle();
        state.cardsAfterAnswer = shownCards(page).length;
      }
      if (reads.length === 1) state.cardsAfterNextPoll = shownCards(page).length;
      return reads.shift();
    },
  };
  await followComputer(page, api, SESSION, () => {});
  assert.deepEqual(answers, [["r1", true]]);
  assert.equal(state.disabledWhileSending, true);
  assert.equal(state.cardsAfterAnswer, 0);
  assert.equal(state.cardsAfterNextPoll, 0);
});

test("a failed answer leaves the card usable and says why", async () => {
  fakePage();
  const { followComputer } = await import("../js/computers.js");
  const page = new FakeElement("div");
  let tries = 0;
  const state = {};
  const reads = [ASKING, sessionRead({ next: 1 }), sessionRead({ next: 1, ended: true })];
  const api = {
    remoteAnswer: async () => {
      tries += 1;
      throw new Error("Can't reach Arty");
    },
    remoteEvents: async () => {
      if (reads.length === 2) {
        await findButton(page.children[3], "Allow").listeners.click();
        state.cardText = allText(page.children[3]);
      }
      if (reads.length === 1) {
        state.cards = shownCards(page).length;
        const allow = findButton(page.children[3], "Allow");
        state.enabled = !allow.disabled;
        await allow.listeners.click();
      }
      return reads.shift();
    },
  };
  await followComputer(page, api, SESSION, () => {});
  assert.match(state.cardText, /Can't reach Arty/);
  assert.equal(state.cards, 1);
  assert.equal(state.enabled, true);
  assert.equal(tries, 2);
});

async function answerThenRead(laterRead) {
  fakePage();
  const { followComputer } = await import("../js/computers.js");
  const page = new FakeElement("div");
  const sent = [];
  const state = {};
  const reads = [ASKING, sessionRead({ next: 1 }), laterRead, sessionRead({ next: 9, ended: true })];
  const api = {
    remoteAnswer: async (session, requestId, allow) => { sent.push([requestId, allow]); },
    remoteEvents: async () => {
      if (reads.length === 3) await findButton(page.children[3], "Allow").listeners.click();
      if (reads.length === 1) {
        state.cards = shownCards(page).map((card) => allText(card));
        const allow = findButton(page.children[3], "Allow");
        if (allow) await allow.listeners.click();
      }
      return reads.shift();
    },
  };
  await followComputer(page, api, SESSION, () => {});
  return { sent, cards: state.cards };
}

test("a request the computer asks again under the same id gets a fresh card", async () => {
  const { sent, cards } = await answerThenRead(sessionRead({ events: [{ seq: 2, type: "permission", request_id: "r1", tool: "bash", text: "again" }], next: 2 }));
  assert.equal(cards.length, 1);
  assert.match(cards[0], /again/);
  assert.deepEqual(sent, [["r1", true], ["r1", true]]);
});

test("a reload after missed lines does not bring back a request already answered", async () => {
  const { sent, cards } = await answerThenRead(sessionRead({ missed: true, events: [{ seq: 1, type: "permission", request_id: "r1", tool: "bash", text: "pytest" }], next: 1 }));
  assert.equal(cards.length, 0);
  assert.deepEqual(sent, [["r1", true]]);
});

function allElements(element) {
  return (element.children ?? []).filter((child) => typeof child !== "string").flatMap((child) => [child, ...allElements(child)]);
}

test("a session reads like a chat: bubbles, one quiet tool group, a rendered reply and an inline request with a preview", async () => {
  fakePage();
  const { followComputer } = await import("../js/computers.js");
  const page = new FakeElement("div");
  const reads = [
    sessionRead({ events: [{ seq: 1, type: "user", text: "fix it" },
      { seq: 2, type: "tool_call", tool: "run_command", text: JSON.stringify({ command: "pytest -q" }) },
      { seq: 3, type: "tool_result", tool: "run_command", text: "3 passed" },
      { seq: 4, type: "tool_call", tool: "read_file", text: JSON.stringify({ path: "src/app.py" }) },
      { seq: 5, type: "assistant", text: "**Done** <img src=x onerror=alert(1)>\n" },
      { seq: 6, type: "assistant", text: "[open](javascript:alert(1)) [docs](https://example.com)" },
      { seq: 7, type: "permission", request_id: "r1", text: "Edit app.py?\n--- app.py\n+++ app.py\n+x = 2" }], next: 7 }),
    sessionRead({ next: 7, ended: true }),
  ];
  let shown;
  const api = {
    remoteEvents: async () => {
      if (reads.length === 1) shown = { transcript: page.children[2], waiting: allText(page.children[3]), page: allText(page) };
      return reads.shift();
    },
  };
  await followComputer(page, api, SESSION, () => {});
  const lines = shown.transcript.children;
  assert.equal(lines.length, 3);
  assert.match(allText(lines[0]), /fix it/);
  assert.match(allText(lines[1]), /Ran a command, read app\.py/);
  assert.match(allText(lines[1]), /3 passed/);
  assert.doesNotMatch(allText(lines[1]), /[{}"]command/);
  const reply = allElements(lines[2]);
  assert.ok(reply.some((element) => element.tag === "strong" && allText(element).includes("Done")));
  assert.match(allText(lines[2]), /<img src=x onerror=alert\(1\)>/);
  const links = reply.filter((element) => element.tag === "a");
  assert.deepEqual(links.map((link) => link.attributes.href), ["https://example.com"]);
  assert.match(shown.waiting, /Edit app\.py\?/);
  assert.match(shown.waiting, /\+x = 2/);
  assert.match(shown.page, /Waiting for you/);
});

test("a tool group grows in place as steps finish, without being rebuilt", async () => {
  fakePage();
  const { followComputer } = await import("../js/computers.js");
  const page = new FakeElement("div");
  const reads = [
    sessionRead({ events: [{ seq: 1, type: "tool_call", tool: "run_command", text: JSON.stringify({ command: "make" }) }], next: 1 }),
    sessionRead({ events: [{ seq: 2, type: "tool_result", tool: "run_command", text: "built" },
      { seq: 3, type: "tool_call", tool: "run_command", text: JSON.stringify({ command: "make test" }) }], next: 3 }),
    sessionRead({ next: 3, ended: true }),
  ];
  const seen = [];
  const api = {
    remoteEvents: async () => {
      if (reads.length < 3) seen.push({ group: page.children[2].children[0], count: page.children[2].children.length, text: allText(page) });
      return reads.shift();
    },
  };
  await followComputer(page, api, SESSION, () => {});
  assert.equal(seen[0].group, seen[1].group);
  assert.equal(seen[1].count, 1);
  assert.match(seen[0].text, /Running make/);
  assert.match(seen[1].text, /Ran 2 commands/);
  assert.match(seen[1].text, /built/);
  assert.match(seen[1].text, /Running make test/);
});

test("an answered request leaves one line saying who answered and how", async () => {
  fakePage();
  const { followComputer } = await import("../js/computers.js");
  const page = new FakeElement("div");
  const reads = [
    sessionRead({ events: [{ seq: 1, type: "permission", request_id: "r1", text: "Run command: pytest" },
      { seq: 2, type: "permission_answered", request_id: "r1", allow: true, by: "Phone" }], next: 2, ended: true }),
  ];
  await followComputer(page, { remoteEvents: async () => reads.shift() }, SESSION, () => {});
  assert.match(allText(page.children[2]), /Allowed by Phone: Run command: pytest/);
  assert.equal(shownCards(page).length, 0);
});

test("Stop shows while Arty works, and after the end the composer stays but is switched off with the reason", async () => {
  fakePage();
  const { followComputer } = await import("../js/computers.js");
  const page = new FakeElement("div");
  const reads = [sessionRead({ events: [{ seq: 1, type: "user", text: "go" }], next: 1 }), sessionRead({ events: [{ seq: 2, type: "end" }], next: 2, ended: true })];
  let stopWhileWorking;
  const api = {
    remoteEvents: async () => {
      if (reads.length === 1) stopWhileWorking = findButton(page, "Stop");
      if (reads.length === 1) stopWhileWorking = { hidden: stopWhileWorking.hidden };
      return reads.shift();
    },
  };
  await followComputer(page, api, SESSION, () => {});
  assert.equal(stopWhileWorking.hidden, false);
  const box = allElements(page).find((element) => element.tag === "textarea");
  assert.equal(box.disabled, true);
  assert.equal(findButton(page, "Stop").hidden, true);
  assert.equal(findButton(page, "Send").disabled, true);
  assert.match(allText(page), /ended/);
  assert.match(allText(page), /can't take messages/);
});
