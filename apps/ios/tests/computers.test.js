import assert from "node:assert/strict";
import { test } from "node:test";
import { ArtyApi } from "../js/api.js";
import { FIRST_POLL, isSessionId, nextPoll, openRequests, permissionText, sender, sessionLine } from "../js/remote.js";

function reply(status, body) {
  const text = JSON.stringify(body);
  return { status, type: "basic", text: async () => text, json: async () => JSON.parse(text) };
}

function recorder(...replies) {
  const calls = [];
  const fetchImpl = async (url, options) => {
    calls.push({ url, ...options });
    return replies.shift();
  };
  return { calls, api: new ArtyApi({ apiKey: "app-key" }, { base: "https://arty.example", fetchImpl }) };
}

const SESSION = "0123456789abcdef";

test("a session line names the computer, folder, model and mode", () => {
  assert.equal(sessionLine({ name: "Laptop", folder: "arty-cli", model: "arty/qwen3:14b", mode: "plan" }),
    "Laptop · arty-cli · arty/qwen3:14b · plan mode");
  assert.equal(sessionLine({}), "A computer · default mode");
});

test("a permission request is described with its tool and what it wants to do", () => {
  assert.equal(permissionText({ tool: "bash", text: "rm -rf build" }), "Arty wants to use bash: rm -rf build");
  assert.equal(permissionText({}), "Arty wants to use a tool");
  assert.equal(permissionText({ text: "Run `python3 test_greet.py`?" }), "Run `python3 test_greet.py`?");
});

test("open requests are the waiting ones plus new ones minus those answered", () => {
  const open = openRequests([{ request_id: "r1" }, { request_id: "r2" }],
    [{ type: "permission", request_id: "r3" }, { type: "permission_answered", request_id: "r1" }]);
  assert.deepEqual(open.map((request) => request.request_id), ["r2", "r3"]);
});

test("only real session ids go into addresses", () => {
  assert.equal(isSessionId(SESSION), true);
  assert.equal(isSessionId("../devices"), false);
});

test("the relay calls go to the brain with the app key", async () => {
  const { calls, api } = recorder(reply(200, { sessions: [{ id: SESSION }] }), reply(200, { events: [], next: 3 }),
    reply(200, { ok: true }), reply(200, { ok: true }), reply(200, { ok: true }), reply(200, { ok: true }));
  assert.deepEqual(await api.remoteSessions(), [{ id: SESSION }]);
  assert.deepEqual(await api.remoteEvents(SESSION, 3, 999), { events: [], next: 3 });
  await api.remoteMessage(SESSION, "run the tests");
  await api.remoteAnswer(SESSION, "r1", false);
  await api.remoteInterrupt(SESSION);
  await api.setRemoteControl(7, true);
  assert.deepEqual(calls.map((call) => [call.method, call.url.replace("https://arty.example", "")]), [
    ["GET", "/v1/remote/sessions"],
    ["GET", `/v1/remote/sessions/${SESSION}/events?after=3&wait=20`],
    ["POST", `/v1/remote/sessions/${SESSION}/messages`],
    ["POST", `/v1/remote/sessions/${SESSION}/answers`],
    ["POST", `/v1/remote/sessions/${SESSION}/interrupt`],
    ["POST", "/v1/devices/7/remote-control"],
  ]);
  assert.deepEqual(JSON.parse(calls[3].body), { request_id: "r1", allow: false });
  assert.deepEqual(JSON.parse(calls[5].body), { allow: true });
  assert.ok(calls.every((call) => call.headers.Authorization === "Bearer app-key"));
});

test("a bad session id never reaches the brain", async () => {
  const { calls, api } = recorder();
  await assert.rejects(api.remoteEvents("../devices", 0, 0));
  assert.equal(calls.length, 0);
});

test("after the first read every poll waits, even before the session has any events", () => {
  assert.deepEqual(FIRST_POLL, { after: 0, wait: 0 });
  assert.deepEqual(nextPoll(FIRST_POLL, { next: 0 }, 20), { after: 0, wait: 20 });
  assert.deepEqual(nextPoll({ after: 4, wait: 20 }, { next: 7 }, 20), { after: 7, wait: 20 });
  assert.deepEqual(nextPoll({ after: 4, wait: 20 }, {}, 20), { after: 4, wait: 20 });
});

test("a double tap sends a message once, and text typed while it sends is kept", async () => {
  const posted = [];
  let finish;
  const field = { value: " run the tests " };
  const send = sender((text) => { posted.push(text); return new Promise((resolve) => { finish = resolve; }); }, field, () => {});
  const first = send();
  const second = send();
  field.value = "next message";
  finish();
  await Promise.all([first, second]);
  assert.deepEqual(posted, ["run the tests"]);
  assert.equal(field.value, "next message");
});

test("a message that fails to send is put back for another try", async () => {
  const problems = [];
  const field = { value: "hello" };
  await sender(() => Promise.reject(new Error("offline")), field, (message) => problems.push(message))();
  assert.equal(field.value, "hello");
  assert.deepEqual(problems, ["offline"]);
});
