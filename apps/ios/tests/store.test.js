import assert from "node:assert/strict";
import { test } from "node:test";
import {
  CONVERSATIONS_KEY, ConversationStore, DEFAULT_MODEL, HISTORY_MESSAGES, MAX_CONVERSATIONS, historyToSend, randomId, recentWindow, titleFor,
} from "../js/conversations.js";
import { forgetEverything, forgetSettings, isComplete, loadPreferences, loadSettings, savePreferences, saveSettings } from "../js/settings.js";

function memoryStorage() {
  const values = new Map();
  return {
    getItem: (key) => (values.has(key) ? values.get(key) : null),
    setItem: (key, value) => values.set(key, String(value)),
    removeItem: (key) => values.delete(key),
  };
}

const conversation = (length) => Array.from({ length }, (_, i) => (i % 2 === 0 ? `user ${i}` : `arty ${i}`));
const window = (messages) => recentWindow(messages, 14, 8, (m) => m.startsWith("user"));

test("short conversations are sent whole", () => {
  assert.deepEqual(window(conversation(9)), conversation(9));
});

test("the window matches the phone app: 14 to 21 messages, starting with the user", () => {
  for (let length = 15; length <= 80; length += 1) {
    const sent = window(conversation(length));
    assert.ok(sent.length >= 14 && sent.length <= 21, `length ${length} sent ${sent.length}`);
    assert.ok(sent[0].startsWith("user"), `length ${length} starts with ${sent[0]}`);
    assert.equal(sent.at(-1), conversation(length).at(-1));
  }
  assert.throws(() => recentWindow([], 0, 8, () => true), RangeError);
});

test("failed replies are not sent back to Arty", () => {
  const sent = historyToSend([
    { role: "user", content: "hi", attachments: [] },
    { role: "assistant", content: "Can't reach Arty", failed: true },
    { role: "user", content: "hi again" },
  ]);
  assert.deepEqual(sent, [
    { role: "user", content: "hi", attachments: [] },
    { role: "user", content: "hi again", attachments: [] },
  ]);
  assert.ok(historyToSend(Array.from({ length: 40 }, (_, i) => ({ role: i % 2 ? "assistant" : "user", content: `${i}` }))).length >= HISTORY_MESSAGES);
});

test("titles come from the first message or file", () => {
  assert.equal(titleFor("  What's on today and what's the weather looking like this week?  "), "What's on today and what's the weather l");
  assert.equal(titleFor("", [{ name: "photo.jpg" }]), "photo.jpg");
  assert.equal(titleFor(""), "New chat");
});

test("conversations are saved newest first and can be removed", () => {
  let clock = 1;
  let next = 0;
  const store = new ConversationStore(memoryStorage(), { now: () => clock++, newId: () => `c${next++}` });
  const first = store.save({ ...store.create(), title: "first" });
  const second = store.save({ ...store.create("arty/qwen3:14b"), title: "second" });
  assert.deepEqual(store.list().map((c) => c.title), ["second", "first"]);
  store.save({ ...first, messages: [{ role: "user", content: "again" }] });
  assert.deepEqual(store.list().map((c) => c.title), ["first", "second"]);
  assert.equal(store.get(second.id).model, "arty/qwen3:14b");
  store.remove(first.id);
  assert.deepEqual(store.list().map((c) => c.id), [second.id]);
  assert.equal(store.get("missing"), null);
});

test("new chats use the main model, which is called Arty", () => {
  assert.equal(DEFAULT_MODEL, "Arty");
  assert.equal(new ConversationStore(memoryStorage(), { newId: () => "c1" }).create().model, "Arty");
});

test("chats saved under the main model's old name open as Arty", () => {
  const storage = memoryStorage();
  storage.setItem(CONVERSATIONS_KEY, JSON.stringify([
    { id: "old", title: "old", model: "assistant-core", messages: [], updated: 2 },
    { id: "picked", title: "picked", model: "arty/qwen3:14b", messages: [], updated: 1 },
  ]));
  const store = new ConversationStore(storage);
  assert.equal(store.get("old").model, "Arty");
  assert.deepEqual(store.list().map((c) => c.model), ["Arty", "arty/qwen3:14b"]);
});

test("old conversations beyond the limit are dropped and bad storage reads as empty", () => {
  let clock = 1;
  let next = 0;
  const storage = memoryStorage();
  const store = new ConversationStore(storage, { now: () => clock++, newId: () => `c${next++}` });
  for (let i = 0; i < MAX_CONVERSATIONS + 5; i += 1) store.save(store.create());
  assert.equal(store.list().length, MAX_CONVERSATIONS);
  assert.equal(store.get("c0"), null);
  storage.setItem("arty.conversations", "{broken");
  assert.deepEqual(store.list(), []);
});

test("connection settings need the app key, and the Access id and secret together", () => {
  const storage = memoryStorage();
  assert.equal(loadSettings(storage), null);
  assert.throws(() => saveSettings(storage, { apiKey: " " }), /app key is needed/);
  assert.throws(() => saveSettings(storage, { apiKey: "k", accessId: "id" }), /go together/);
  assert.deepEqual(saveSettings(storage, { apiKey: " k ", accessId: "id ", accessSecret: " s" }), { apiKey: "k", accessId: "id", accessSecret: "s" });
  assert.deepEqual(loadSettings(storage), { apiKey: "k", accessId: "id", accessSecret: "s" });
  assert.ok(isComplete({ apiKey: "k" }));
  forgetSettings(storage);
  assert.equal(loadSettings(storage), null);
});

test("preferences keep their defaults and merge changes", () => {
  const storage = memoryStorage();
  assert.deepEqual(loadPreferences(storage), { readAloud: false, lastChat: null, lastSeenUpdate: 0, quietCategories: [] });
  savePreferences(storage, { readAloud: true });
  assert.deepEqual(savePreferences(storage, { lastSeenUpdate: 9 }), { readAloud: true, lastChat: null, lastSeenUpdate: 9, quietCategories: [] });
  storage.setItem("arty.preferences", JSON.stringify({ quietCategories: "email" }));
  assert.deepEqual(loadPreferences(storage).quietCategories, []);
});

test("forgetting everything clears the keys, the chats and the preferences", () => {
  const storage = memoryStorage();
  saveSettings(storage, { apiKey: "k" });
  savePreferences(storage, { readAloud: true, lastChat: "c1" });
  new ConversationStore(storage, { newId: () => "c1" }).save({ id: "c1", title: "t", model: "m", messages: [] });
  storage.setItem("unrelated", "kept");
  forgetEverything(storage);
  assert.equal(loadSettings(storage), null);
  assert.deepEqual(new ConversationStore(storage).list(), []);
  assert.equal(loadPreferences(storage).readAloud, false);
  assert.equal(storage.getItem("unrelated"), "kept");
});

test("ids work on a plain http page, where crypto.randomUUID is missing", () => {
  const onlyRandomValues = { getRandomValues: (bytes) => bytes.fill(171) };
  assert.equal(randomId(onlyRandomValues), "ab".repeat(16));
  assert.equal(randomId({ randomUUID: () => "uuid-1", getRandomValues: onlyRandomValues.getRandomValues }), "uuid-1");
  assert.notEqual(randomId(), randomId());
});
