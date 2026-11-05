import assert from "node:assert/strict";
import { test } from "node:test";
import {
  ArtyApi, ArtyError, CLIENT_NAME, GAVE_UP_MESSAGE, GIVE_UP_MS, MAX_UPLOAD_BYTES, RESTARTED_MESSAGE, errorMessage, messageContent,
} from "../js/api.js";

function reply(status, body, extra = {}) {
  const text = typeof body === "string" ? body : JSON.stringify(body);
  return {
    status, type: "basic", ...extra,
    text: async () => text,
    json: async () => JSON.parse(text),
    blob: async () => new Blob([text]),
  };
}

function fakeFetch(...replies) {
  const calls = [];
  const fetchImpl = async (url, options) => {
    calls.push({ url, ...options });
    const next = replies.shift();
    if (next instanceof Error) throw next;
    return typeof next === "function" ? next(url, options) : next;
  };
  return { calls, fetchImpl };
}

const KEYS = { apiKey: "app-key", accessId: "id.access", accessSecret: "secret" };

test("every request carries the app key and the Cloudflare service token", async () => {
  const { calls, fetchImpl } = fakeFetch(reply(200, { data: [{ id: "assistant-core" }] }));
  await new ArtyApi(KEYS, { base: "https://arty.example/", fetchImpl }).models();
  assert.equal(calls[0].url, "https://arty.example/v1/models");
  assert.equal(calls[0].headers.Authorization, "Bearer app-key");
  assert.equal(calls[0].headers["CF-Access-Client-Id"], "id.access");
  assert.equal(calls[0].headers["CF-Access-Client-Secret"], "secret");
  assert.equal(calls[0].redirect, "manual");
});

test("at home without Cloudflare no service token headers are sent", async () => {
  const { calls, fetchImpl } = fakeFetch(reply(200, { data: [] }));
  await new ArtyApi({ apiKey: "k", accessId: "", accessSecret: "" }, { fetchImpl }).models();
  assert.equal("CF-Access-Client-Id" in calls[0].headers, false);
});

test("models keep their ids and fall back to the id as the name", async () => {
  const { fetchImpl } = fakeFetch(reply(200, { data: [
    { id: "assistant-core", model: "qwen3:14b" },
    { id: "solo/roleplay-model:12b", name: "roleplay-model:12b (on its own: no Arty, no memory)" },
  ] }));
  const models = await new ArtyApi(KEYS, { fetchImpl }).models();
  assert.deepEqual(models.map((m) => [m.id, m.name]), [
    ["assistant-core", "assistant-core"],
    ["solo/roleplay-model:12b", "roleplay-model:12b (on its own: no Arty, no memory)"],
  ]);
});

test("a chat starts a job as the ios client with files as content parts", async () => {
  const { calls, fetchImpl } = fakeFetch(reply(202, { id: "job1" }));
  const id = await new ArtyApi(KEYS, { fetchImpl }).startChat([
    { role: "user", content: "what's this?", attachments: [{ fileId: "f1", name: "photo.jpg" }] },
  ], "solo/roleplay-model:12b");
  assert.equal(id, "job1");
  const sent = JSON.parse(calls[0].body);
  assert.equal(sent.client, CLIENT_NAME);
  assert.equal(sent.model, "solo/roleplay-model:12b");
  assert.deepEqual(sent.messages[0].content, [
    { type: "text", text: "what's this?" },
    { type: "file", file: { file_id: "f1", filename: "photo.jpg" } },
  ]);
  assert.equal(calls[0].headers["Content-Type"], "application/json");
});

test("plain messages stay plain text", () => {
  assert.equal(messageContent({ role: "user", content: "hi", attachments: [] }), "hi");
  assert.deepEqual(messageContent({ role: "user", content: " ", attachments: [{ fileId: "f", name: "a.pdf" }] }),
    [{ type: "file", file: { file_id: "f", filename: "a.pdf" } }]);
});

test("waiting shows partial text, rides out a dropped connection and returns the answer", async () => {
  const { fetchImpl } = fakeFetch(
    reply(200, { status: "running", partial: "The weather" }),
    new TypeError("Load failed"),
    reply(200, { status: "done", answer: "The weather is dry.", tools_used: ["weather"] }),
  );
  const partials = [];
  const result = await new ArtyApi(KEYS, { fetchImpl }).waitForAnswer("job1", { onPartial: (text) => partials.push(text), sleep: async () => {} });
  assert.deepEqual(partials, ["The weather"]);
  assert.deepEqual(result, { failed: false, text: "The weather is dry.", toolsUsed: ["weather"] });
});

test("a forgotten job and a failed job are reported, not retried forever", async () => {
  const gone = fakeFetch(reply(404, { error: { message: "no such chat job" } }));
  assert.deepEqual(await new ArtyApi(KEYS, { fetchImpl: gone.fetchImpl }).waitForAnswer("x", { sleep: async () => {} }),
    { failed: true, text: RESTARTED_MESSAGE });
  const broken = fakeFetch(reply(200, { status: "failed", error: "Ollama is down" }));
  assert.deepEqual(await new ArtyApi(KEYS, { fetchImpl: broken.fetchImpl }).waitForAnswer("x", { sleep: async () => {} }),
    { failed: true, text: "Arty hit an error: Ollama is down" });
});

test("waiting gives up after twenty minutes", async () => {
  let clock = 0;
  const { fetchImpl } = fakeFetch(...Array.from({ length: 5 }, () => reply(200, { status: "running" })));
  const result = await new ArtyApi(KEYS, { fetchImpl }).waitForAnswer("x", {
    sleep: async () => { clock += GIVE_UP_MS / 3; }, now: () => clock,
  });
  assert.deepEqual(result, { failed: true, text: GAVE_UP_MESSAGE });
});

test("errors read the brain's own message first, then explain the status", async () => {
  assert.equal(errorMessage(400, JSON.stringify({ error: { message: "body must be JSON" } })), "body must be JSON");
  assert.equal(errorMessage(401, ""), "Access refused. The app's keys may have been changed.");
  assert.equal(errorMessage(502, "<html>"), "Arty's server isn't answering. The machine running its brain may be off.");
  assert.equal(errorMessage(418, ""), "Request failed (HTTP 418).");
  const { fetchImpl } = fakeFetch(reply(401, { error: { message: "invalid API key" } }));
  await assert.rejects(new ArtyApi(KEYS, { fetchImpl }).tasks(), (error) => error instanceof ArtyError && error.status === 401 && error.message === "invalid API key");
});

test("a Cloudflare login redirect means the access token was refused", async () => {
  const { fetchImpl } = fakeFetch(reply(0, "", { type: "opaqueredirect" }));
  await assert.rejects(new ArtyApi(KEYS, { fetchImpl }).models(), /Cloudflare refused the access id or secret/);
});

test("no connection says so in plain words", async () => {
  const { fetchImpl } = fakeFetch(new TypeError("Load failed"));
  await assert.rejects(new ArtyApi(KEYS, { fetchImpl }).models(), (error) => error.status === 0 && /Can't reach Arty \(Load failed\)/.test(error.message));
});

test("uploads send the raw file with its name and refuse anything over the limit", async () => {
  const { calls, fetchImpl } = fakeFetch(reply(201, { id: "f9", name: "notes 1.pdf", mime: "application/pdf", kind: "other", size: 3 }));
  const api = new ArtyApi(KEYS, { fetchImpl });
  const file = { name: "notes 1.pdf", type: "application/pdf", size: 3 };
  assert.deepEqual(await api.upload(file), { fileId: "f9", name: "notes 1.pdf", mime: "application/pdf", kind: "other", size: 3 });
  assert.equal(calls[0].body, file);
  assert.equal(calls[0].headers["X-Filename"], "notes%201.pdf");
  await assert.rejects(api.upload({ name: "huge.mov", type: "video/quicktime", size: MAX_UPLOAD_BYTES + 1 }), /too big: Arty takes up to 95 MB/);
  assert.equal(calls.length, 1);
});

test("downloads use only the arty-files path, so home links work remotely", async () => {
  const { calls, fetchImpl } = fakeFetch(reply(200, "pdf bytes"));
  const blob = await new ArtyApi(KEYS, { base: "https://arty.example", fetchImpl }).download("http://192.168.1.13:8443/arty-files/abc/token/report.pdf");
  assert.equal(calls[0].url, "https://arty.example/arty-files/abc/token/report.pdf");
  assert.equal(await blob.text(), "pdf bytes");
  const expired = fakeFetch(reply(404, "<html>gone</html>"));
  await assert.rejects(new ArtyApi(KEYS, { fetchImpl: expired.fetchImpl }).download("/arty-files/a/b/c.txt"), /expired: Arty keeps files for 24 hours/);
  await assert.rejects(new ArtyApi(KEYS, { fetchImpl }).download("https://example.com/x.pdf"), /isn't a file from Arty/);
  await assert.rejects(new ArtyApi(KEYS, { fetchImpl }).download("/arty-files/../v1/memory"), /isn't a file from Arty/);
  assert.equal(calls.length, 1);
});

test("announcements wait at most 55 seconds, under Cloudflare's limit", async () => {
  const { calls, fetchImpl } = fakeFetch(reply(200, { announcements: [{ id: 7, kind: "reminder", title: "Bins" }], last: 7 }));
  const feed = await new ArtyApi(KEYS, { fetchImpl }).announcements(6, 300);
  assert.equal(calls[0].url, "/v1/announcements?after=6&wait=55");
  assert.equal(feed.last, 7);
  assert.equal(feed.announcements[0].title, "Bins");
});

test("task decisions and schedule cancels hit the brain's routes", async () => {
  const { calls, fetchImpl } = fakeFetch(reply(200, { message: "Approved." }), reply(200, { ok: true }), reply(200, { ok: true }));
  const api = new ArtyApi(KEYS, { fetchImpl });
  assert.equal(await api.decideTask(12, "approve"), "Approved.");
  await api.cancelSchedule(3);
  await api.deleteEvent(4);
  assert.deepEqual(calls.map((c) => [c.method, c.url]), [["POST", "/v1/tasks/12/approve"], ["DELETE", "/v1/schedules/3"], ["DELETE", "/v1/calendar/4"]]);
});
