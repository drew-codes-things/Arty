import assert from "node:assert/strict";
import { test } from "node:test";
import {
  AUTO_MODEL_HINT, AUTO_MODEL_ID, AUTO_MODEL_NAME, chatStats, modelLabel, pickerModels, replyModelLabel, shortCount, statusLine, wholePercent,
} from "../js/turnstatus.js";

const CODE_REPLY = {
  role: "assistant", content: "Done.",
  arty: { model: "qwen3:14b", role: "code", auto: true, context_window: 24576, steps: 2,
    usage: { prompt_tokens: 1200, completion_tokens: 60, context_tokens: 1260, cached_tokens: 1100, tokens_per_second: 27.4 } },
};

test("the picker is Arty Auto first, then every model once by its own name", () => {
  const listed = pickerModels([
    { id: "Arty", name: "Arty", model: "qwen3:14b", description: "Picks the model for each request" },
    { id: "arty/qwen3:14b", name: "Arty (qwen3:14b)", model: "qwen3:14b", description: "14B · all tools" },
    { id: "arty/mimo-v2.6:9b", name: "Arty (mimo-v2.6:9b)", model: "mimo-v2.6:9b", description: "" },
    { id: "solo/mimo-v2.6:9b", name: "mimo-v2.6:9b (on its own: no Arty, no memory)", model: "mimo-v2.6:9b", description: "" },
    { id: "lab/p9", name: "P9", model: "", description: "" },
    { id: "extra/pal", name: "Pal", model: "qwen3:14b", description: "" },
    { id: "extra/buddy", name: "Buddy", model: "tiny:1b", description: "" },
    { id: "auto", name: "auto", model: "", description: "Picks the model for each message: code on coder:9b" },
  ]);
  assert.deepEqual(listed.map((model) => [model.id, model.name]), [
    ["auto", "Arty Auto"], ["arty/qwen3:14b", "qwen3:14b"], ["arty/mimo-v2.6:9b", "mimo-v2.6:9b"], ["lab/p9", "P9"], ["extra/buddy", "Buddy"],
  ]);
  assert.equal(listed[0].description, "Picks the model for each message: code on coder:9b");
  assert.equal(listed[1].description, "14B · all tools");
});

test("an older brain that lists no Auto still gets the entry first", () => {
  const listed = pickerModels([{ id: "Arty", name: "Arty", model: "qwen3:14b", description: "" }]);
  assert.deepEqual(listed, [{ id: AUTO_MODEL_ID, name: AUTO_MODEL_NAME, model: "", description: AUTO_MODEL_HINT }]);
});

test("a chat's model is named by its model, so an old Arty chat reads as the main model", () => {
  const models = [{ id: "Arty", name: "Arty", model: "qwen3:14b" }, { id: "solo/x:1b", name: "x:1b (on its own)", model: "x:1b" }];
  assert.equal(modelLabel(models, "auto"), "Arty Auto");
  assert.equal(modelLabel(models, "Arty"), "qwen3:14b");
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
  const listed = pickerModels([
    { id: "arty/remote/friend/big-model", name: "Arty (remote/friend/big-model, offline)", model: "remote/friend/big-model", available: false },
    { id: "arty/qwen3:14b", name: "Arty (qwen3:14b)", model: "qwen3:14b", available: true },
  ]);
  assert.deepEqual(listed.slice(1).map((model) => model.name), ["remote/friend/big-model (offline)", "qwen3:14b"]);
});
