import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import {
  NOTIFICATION_CATEGORIES, STILL_IN_UPDATES_NOTE, categoryOf, isAlerting, renderNotificationSwitches, withCategorySwitched,
} from "../js/notifications.js";
import { loadPreferences, savePreferences } from "../js/settings.js";
import { UpdatesFeed } from "../js/updates.js";

class FakeElement {
  constructor(tag) {
    this.tag = tag;
    this.children = [];
    this.listeners = {};
    this.attributes = {};
    this.textContent = "";
    this.hidden = false;
    this.checked = false;
    this.className = "";
    this.dataset = {};
  }
  setAttribute(name, value) {
    this.attributes[name] = value;
    if (name === "checked") this.checked = true;
    if (name === "hidden") this.hidden = true;
  }
  removeAttribute(name) { delete this.attributes[name]; }
  addEventListener(type, listener) { this.listeners[type] = listener; }
  append(child) { this.children.push(child); }
  replaceChildren(...children) { this.children = children; }
}

function fakePage() {
  const made = [];
  const toast = new FakeElement("div");
  globalThis.Node = FakeElement;
  globalThis.document = {
    createElement(tag) { const element = new FakeElement(tag); made.push(element); return element; },
    getElementById(id) { return id === "toast" ? toast : new FakeElement("div"); },
  };
  return { made, toast };
}

function memoryStorage() {
  const values = new Map();
  return {
    getItem: (key) => (values.has(key) ? values.get(key) : null),
    setItem: (key, value) => values.set(key, String(value)),
    removeItem: (key) => values.delete(key),
  };
}

const BRAIN_CATEGORIES = ["email", "server", "tasks", "approvals", "reminders", "calendar", "lectures", "arty", "other"];

function feedWith(storage, isShowing = () => false) {
  return new UpdatesFeed({
    api: { tasks: async () => [] }, storage, container: new FakeElement("section"), badge: new FakeElement("span"), isShowing,
  });
}

test("the switches match the brain's categories in order", () => {
  assert.deepEqual(NOTIFICATION_CATEGORIES.map((category) => category.key), BRAIN_CATEGORIES);
  for (const category of NOTIFICATION_CATEGORIES) assert.ok(category.title && category.description, category.key);
});

test("fixes Arty offers are approvals, so the server switch no longer mentions them", () => {
  const byKey = Object.fromEntries(NOTIFICATION_CATEGORIES.map((category) => [category.key, category]));
  assert.equal(byKey.server.description, "Services going down or coming back, homelab alerts");
  assert.equal(byKey.approvals.description, "When Arty needs your OK before it does something, like fixing a server");
});

test("an update is sorted by its category, or by its kind from an older brain", () => {
  assert.equal(categoryOf({ kind: "message", category: "email" }), "email");
  assert.equal(categoryOf({ kind: "approval" }), "approvals");
  assert.equal(categoryOf({ kind: "reminder", category: null }), "reminders");
  assert.equal(categoryOf({ kind: "alert" }), "server");
  assert.equal(categoryOf({ kind: "message", category: "spam" }), "other");
  assert.equal(categoryOf({ kind: "message" }), "other");
});

test("only switched-off categories go quiet", () => {
  assert.equal(isAlerting({ category: "email" }, ["email"]), false);
  assert.equal(isAlerting({ category: "server" }, ["email"]), true);
  assert.equal(isAlerting({ category: "email" }, []), true);
});

test("the switches are saved with the other preferences on this iPhone", () => {
  const storage = memoryStorage();
  assert.deepEqual(loadPreferences(storage).quietCategories, []);
  savePreferences(storage, { quietCategories: withCategorySwitched([], "email", false) });
  assert.deepEqual(loadPreferences(storage).quietCategories, ["email"]);
  assert.deepEqual(withCategorySwitched(["email", "server"], "email", true), ["server"]);
  assert.deepEqual(withCategorySwitched(["email"], "email", false), ["email"]);
});

test("a switched-off category shows no pop-up and no badge, but stays in the list", async () => {
  const { toast } = fakePage();
  const storage = memoryStorage();
  savePreferences(storage, { quietCategories: ["email"] });
  const feed = feedWith(storage);
  await feed.receive([{ id: 1, kind: "message", category: "email", title: "Important email", message: "Sam" }]);
  assert.equal(toast.textContent, "");
  assert.equal(feed.items.length, 1);
  assert.equal(feed.unseenCount(), 0);
  assert.equal(feed.badge.hidden, true);
  await feed.receive([{ id: 2, kind: "alert", category: "server", title: "Plex is down", message: "" },
    { id: 3, kind: "message", category: "email", title: "Another email", message: "" }]);
  assert.match(toast.textContent, /^Plex is down/);
  assert.equal(feed.items.length, 3);
  assert.equal(feed.unseenCount(), 1);
  assert.equal(feed.badge.textContent, "1");
});

test("the settings page has a switch per category, saves them and warns about approvals", () => {
  const { made } = fakePage();
  const storage = memoryStorage();
  const shown = renderNotificationSwitches(storage);
  assert.ok(shown.every((element) => element instanceof FakeElement), "replaceChildren needs a flat list of elements");
  const switches = made.filter((element) => element.tag === "input");
  assert.equal(switches.length, BRAIN_CATEGORIES.length);
  assert.ok(switches.every((element) => element.checked));
  const approvals = switches[BRAIN_CATEGORIES.indexOf("approvals")];
  const warning = made.find((element) => element.className.includes("switch-warning"));
  assert.equal(warning.hidden, true);
  approvals.checked = false;
  approvals.listeners.change();
  assert.deepEqual(loadPreferences(storage).quietCategories, ["approvals"]);
  assert.equal(warning.hidden, false);
  approvals.checked = true;
  approvals.listeners.change();
  assert.deepEqual(loadPreferences(storage).quietCategories, []);
  assert.equal(warning.hidden, true);
  assert.ok(shown.some((element) => element.children?.includes(STILL_IN_UPDATES_NOTE)));
  assert.match(STILL_IN_UPDATES_NOTE, /Updates/);
});

test("the offline cache knows the new file and has a new version", () => {
  const worker = readFileSync(new URL("../sw.js", import.meta.url), "utf8");
  assert.match(worker, /"js\/notifications\.js"/);
  assert.doesNotMatch(worker, /const CACHE = "arty-ios-v10";/);
});
