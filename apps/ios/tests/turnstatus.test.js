import assert from "node:assert/strict";
import { test } from "node:test";
import {
  AUTO_MODEL_HINT, REMOTE_SECTION, chatStats, defaultModel, drawnTabs, modelLabel, persistedPick, pickerModels, pickerTab, pickerTabs, replyModelLabel, shortCount, statusLine, wholePercent,
} from "../js/turnstatus.js";

const CODE_REPLY = {
  role: "assistant", content: "Done.",
  arty: { model: "qwen3:14b", role: "code", auto: true, context_window: 24576, steps: 2,
    usage: { prompt_tokens: 1200, completion_tokens: 60, context_tokens: 1260, cached_tokens: 1100, tokens_per_second: 27.4 } },
};

const BIG = "hf.co/unsloth/Qwen3.6-35B-A3B-GGUF:UD-IQ4_XS";
const SIDE = "side-model";
const REMOTE_A = "remote/remote-a/remote-model-a";
const REMOTE_B = "remote/remote-a/remote-model-b";
const NEW_BRAIN = [
  { id: "Arty", name: "Arty", model: BIG, description: "Qwen3.6 35B for chat, code and pictures" },
  { id: `arty/${BIG}`, name: "Arty (Qwen3.6 35B)", label: "Qwen3.6 35B", model: BIG, description: "35B · all tools" },
  { id: "arty/qwen3:14b", name: "Arty (qwen3:14b)", label: "qwen3:14b", model: "qwen3:14b", description: "14B · all tools" },
  { id: `arty/${REMOTE_A}`, name: "Arty (Remote model A)", label: "Remote model A", model: REMOTE_A, description: "" },
  { id: `arty/${REMOTE_B}`, name: "Arty (Remote model B)", label: "Remote model B", model: REMOTE_B, description: "" },
  { id: `solo/${BIG}`, name: "Qwen3.6 35B (on its own: no Arty, no memory)", label: "Qwen3.6 35B", model: BIG, description: "" },
  { id: `arty/${SIDE}`, name: "Arty (Side model)", label: "Side model", model: SIDE, description: "", private: true },
  { id: "lab/p9", name: "P9", model: "", description: "" },
  { id: "auto", name: "auto", model: "", description: "Picks the model for each message: code on coder:9b" },
];

function shown(tab) {
  return tab.models.map((model) => model.name);
}

test("the picker has an Arty tab and a Choose a model tab, each with the Remote models underneath", () => {
  const { tabs, remote } = pickerTabs(NEW_BRAIN);
  assert.deepEqual(tabs.map((tab) => tab.name), ["Arty", "Choose a model"]);
  assert.deepEqual(shown(tabs[0]), ["Arty", "Arty Auto"]);
  assert.deepEqual(shown(tabs[1]), ["Qwen3.6 35B", "Side model", "qwen3:14b", "P9"]);
  assert.equal(REMOTE_SECTION, "Remote");
  assert.deepEqual(remote.map((model) => [model.id, model.name]), [[`arty/${REMOTE_A}`, "Remote model A"], [`arty/${REMOTE_B}`, "Remote model B"]]);
  assert.equal(tabs[0].models[1].description, "Picks the model for each message: code on coder:9b");
});

test("no long engine id or picture helper appears anywhere in the picker", () => {
  const { tabs, remote } = pickerTabs([...NEW_BRAIN, { id: "arty/qwen2.5vl:7b", name: "Arty (qwen2.5vl:7b)", label: "qwen2.5vl:7b", model: "qwen2.5vl:7b" },
    { id: "solo/qwen2.5vl:7b", name: "qwen2.5vl:7b (on its own)", model: "qwen2.5vl:7b" }]);
  const names = [...tabs.flatMap(shown), ...remote.map((model) => model.name)];
  assert.ok(names.every((name) => !name.includes("hf.co") && !name.includes("remote/") && !name.includes("qwen2.5vl")), names.join(", "));
});

test("Arty is the default when the brain offers it", () => {
  assert.equal(defaultModel(NEW_BRAIN), "Arty");
  assert.equal(pickerTab(NEW_BRAIN, "Arty"), 0);
  assert.equal(pickerTab(NEW_BRAIN, "arty/qwen3:14b"), 1);
  assert.equal(pickerTab(NEW_BRAIN, `arty/${REMOTE_A}`), 0);
});

test("a key allowed only remote models gets a sensible picker and its first remote model as the default", () => {
  const onlyRemote = NEW_BRAIN.filter((model) => model.model.startsWith("remote/"));
  const { tabs, remote } = pickerTabs(onlyRemote);
  assert.deepEqual(shown(tabs[0]), []);
  assert.deepEqual(shown(tabs[1]), []);
  assert.equal(remote.length, 2);
  assert.equal(pickerModels(onlyRemote).some((model) => model.id === "auto"), false);
  assert.equal(pickerTab(onlyRemote, `arty/${REMOTE_A}`), 1);
  assert.deepEqual(drawnTabs(onlyRemote).map((tab) => [tab.index, tab.name]), [[1, "Choose a model"]]);
  assert.deepEqual(drawnTabs(NEW_BRAIN).map((tab) => tab.index), [0, 1]);
  assert.equal(defaultModel(onlyRemote), `arty/${REMOTE_A}`);
  assert.equal(defaultModel([]), "auto");
});

test("an older brain with no labels, no Auto and no remote models still gets both tabs", () => {
  const { tabs, remote } = pickerTabs([{ id: "Arty", name: "Arty", model: "qwen3:14b", description: "" },
    { id: "arty/qwen3:14b", name: "Arty (qwen3:14b)", model: "qwen3:14b", description: "" }]);
  assert.deepEqual(tabs.map(shown), [["Arty", "Arty Auto"], ["qwen3:14b"]]);
  assert.equal(tabs[0].models[1].description, AUTO_MODEL_HINT);
  assert.deepEqual(remote, []);
});

test("a chat's model is named as the picker names it, and the main entry is just Arty", () => {
  const models = [{ id: "Arty", name: "Arty", model: "hf.co/unsloth/Qwen3.6-35B-A3B-GGUF:UD-IQ4_XS" }, { id: "solo/x:1b", name: "x:1b (on its own)", model: "x:1b" }];
  assert.equal(modelLabel(models, "auto"), "Arty Auto");
  assert.equal(modelLabel(models, "Arty"), "Arty");
  assert.equal(modelLabel(models, "solo/x:1b"), "x:1b");
  assert.equal(modelLabel(models, "gone:3b"), "gone:3b");
  assert.equal(modelLabel([{ id: "extra/pal", name: "Pal", model: "qwen3:14b" }], "extra/pal"), "Pal");
});

test("a reply shows its model, and the role when Arty Auto chose it", () => {
  assert.equal(replyModelLabel(CODE_REPLY.arty), "qwen3:14b · Arty Auto: code");
  assert.equal(replyModelLabel({ model: "qwen3:14b", role: null, auto: false }), "qwen3:14b");
  assert.equal(replyModelLabel(undefined), "");
  assert.equal(replyModelLabel({ steps: 0 }), "");
});

test("counts are shortened like 1.3K and 108K", () => {
  assert.deepEqual([999, 1260, 108000, 1_500_000].map(shortCount), ["999", "1.3K", "108K", "1.5M"]);
});

test("100% only shows when the share really is whole", () => {
  assert.equal(wholePercent(9999, 10000), 99);
  assert.equal(wholePercent(10000, 10000), 100);
  assert.equal(wholePercent(1, 0), null);
  assert.equal(wholePercent(undefined, 10), null);
});

test("the status counts turns and steps over the chat and reads the latest reply's numbers", () => {
  const messages = [
    { role: "user", content: "hi" },
    { role: "assistant", content: "Hello.", arty: { model: "qwen3:14b", steps: 1 } },
    { role: "user", content: "fix my code" },
    CODE_REPLY,
    { role: "user", content: "thanks" },
  ];
  const stats = chatStats(messages);
  assert.deepEqual(stats, { turns: 3, steps: 3, tokensPerSecond: 27.4, tokens: 1260, cacheHit: 91, contextUsed: 5 });
  assert.deepEqual(statusLine(stats), ["3 turns 3 steps · 27 tok/s", "1.3K tok · Cache hit 91%"]);
});

test("unknown numbers are hidden rather than shown as zero", () => {
  const plain = [{ role: "user", content: "hi" }, { role: "assistant", content: "Hello." }];
  assert.deepEqual(chatStats(plain), { turns: 1 });
  assert.deepEqual(statusLine(chatStats(plain)), ["1 turn"]);
  const noCache = [{ role: "user", content: "hi" }, { role: "assistant", content: "Hi.",
    arty: { model: "m", steps: 0, usage: { prompt_tokens: 10, completion_tokens: 2, context_tokens: 12 } } }];
  assert.deepEqual(chatStats(noCache), { turns: 1, steps: 0, tokens: 12 });
  assert.deepEqual(chatStats([]), { turns: 0 });
});

test("an offline model is marked offline in the picker", () => {
  const { tabs, remote } = pickerTabs([
    { id: "arty/remote/remote-a/big-model", name: "Arty (Big, offline)", label: "Big", model: "remote/remote-a/big-model", available: false },
    { id: "arty/qwen3:14b", name: "Arty (qwen3:14b)", model: "qwen3:14b", available: true },
  ]);
  assert.deepEqual([...shown(tabs[1]), ...remote.map((model) => model.name)], ["qwen3:14b", "Big (offline)"]);
});

test("a reply shows the model's label, never the raw engine id", () => {
  const big = "hf.co/owner/Big-35B-A3B-GGUF:UD-IQ4_XS";
  const listed = [{ id: `arty/${big}`, name: "Arty (big)", model: big, label: "Big 35B", available: true }];
  assert.equal(replyModelLabel({ model: big }, listed), "Big 35B");
  assert.equal(replyModelLabel({ model: big, auto: true, role: "code" }, listed), "Big 35B · Arty Auto: code");
  assert.equal(replyModelLabel({ model: big }), "Big-35B-A3B");
  assert.equal(replyModelLabel({ model: big }, [{ id: `arty/${big}`, name: "Arty (big)", model: big, label: big }]), "Big-35B-A3B");
});

test("only a staying switch named in a message moves the picker", () => {
  assert.equal(persistedPick({ model: "qwen3:14b", persist: true, pick: "arty/qwen3:14b" }), "arty/qwen3:14b");
  assert.equal(persistedPick({ model: "qwen3:14b", auto: false }), null);
  assert.equal(persistedPick({ persist: true, pick: "" }), null);
  assert.equal(persistedPick(undefined), null);
});

test("a finished turn with a staying switch picks that model for the chat", async () => {
  const { readFile } = await import("node:fs/promises");
  const chat = await readFile(new URL("../js/chat.js", import.meta.url), "utf8");
  assert.match(chat, /const switched = persistedPick\(reply\.arty\);/);
  assert.match(chat, /if \(switched\) savePreferences\(this\.storage, \{ modelChoice: switched \}\);/);
});
