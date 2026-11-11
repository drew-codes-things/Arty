import assert from "node:assert/strict";
import { test } from "node:test";
import { ArtyApi, approvePage } from "../js/api.js";
import { approveElsewhereLink, codesMatch, deviceSummary, readAuthoriseCode, setupHint, withoutAuthoriseCode } from "../js/authorise.js";

function reply(status, body) {
  const text = JSON.stringify(body);
  return { status, type: "basic", text: async () => text, json: async () => JSON.parse(text) };
}

function fakeFetch(...replies) {
  const calls = [];
  return { calls, fetchImpl: async (url, options) => { calls.push({ url, ...options }); return replies.shift(); } };
}

test("the code is read from the link in either case and with or without the dash", () => {
  assert.equal(readAuthoriseCode("?authorise=abcd-efgh"), "ABCD-EFGH");
  assert.equal(readAuthoriseCode("?authorise=ABCDEFGH"), "ABCD-EFGH");
  assert.equal(readAuthoriseCode("?authorise=ABCD-EFG1"), null);
  assert.equal(readAuthoriseCode("?authorise=<script>"), null);
  assert.equal(readAuthoriseCode(""), null);
});

test("the code is taken out of the address once read", () => {
  assert.equal(withoutAuthoriseCode("https://arty.example/ios/?authorise=ABCD-EFGH#x"), "/ios/#x");
  assert.equal(withoutAuthoriseCode("https://arty.example/ios/?a=1&authorise=ABCD-EFGH"), "/ios/?a=1");
});

test("the request says the name is the device's own claim and shows where it asked from", () => {
  assert.equal(deviceSummary({ name: "Arty CLI on vivadrew", address: "192.0.2.5" }),
    "A device calling itself \u201cArty CLI on vivadrew\u201d, asking from 192.0.2.5, wants its own key for Arty.");
  assert.equal(deviceSummary({}), "A device calling itself \u201cA device\u201d wants its own key for Arty.");
});

test("approving needs the code typed from the terminal, not just the link", () => {
  assert.equal(codesMatch("ABCD-EFGH", "abcd efgh"), true);
  assert.equal(codesMatch("ABCD-EFGH", "abcdefgh"), true);
  assert.equal(codesMatch("ABCD-EFGH", "ABCD-EFGJ"), false);
  assert.equal(codesMatch("ABCD-EFGH", ""), false);
});

test("approved devices can be listed and revoked", async () => {
  const { calls, fetchImpl } = fakeFetch(reply(200, { devices: [{ id: 3, name: "laptop" }] }), reply(200, { ok: true }));
  const api = new ArtyApi({ apiKey: "app-key" }, { base: "https://arty.example", fetchImpl });
  const listed = await api.devices();
  await api.revokeDevice(3);
  assert.deepEqual(listed, [{ id: 3, name: "laptop" }]);
  assert.equal(calls[0].url, "https://arty.example/v1/devices");
  assert.equal(calls[1].url, "https://arty.example/v1/devices/3");
  assert.equal(calls[1].method, "DELETE");
});

test("the pending request is looked up and answered with the app's key", async () => {
  const { calls, fetchImpl } = fakeFetch(reply(200, { name: "Arty CLI on x", address: "192.0.2.5" }), reply(200, { ok: true }));
  const api = new ArtyApi({ apiKey: "app-key" }, { base: "https://arty.example", fetchImpl });
  const pending = await api.pendingDevice("ABCD-EFGH");
  await api.answerDevice("ABCD-EFGH", false);
  assert.equal(pending.name, "Arty CLI on x");
  assert.equal(calls[0].url, "https://arty.example/v1/device/pending?code=ABCD-EFGH");
  assert.equal(calls[0].headers.Authorization, "Bearer app-key");
  assert.equal(calls[1].url, "https://arty.example/v1/device/approve");
  assert.deepEqual(JSON.parse(calls[1].body), { user_code: "ABCD-EFGH", approve: false });
});

test("a link opened before Arty is set up here says an approval is waiting", () => {
  assert.match(setupHint("?authorise=abcd-efgh"), /waiting for you to approve it \(code ABCD-EFGH\)/);
  assert.match(setupHint("?authorise=abcd-efgh"), /home screen keeps its own settings/);
  assert.equal(setupHint(""), null);
  assert.equal(setupHint("?authorise=<b>"), null);
});

test("with no key here the computer is sent to starfire's approve page with its code", () => {
  assert.equal(approveElsewhereLink("https://starfire.example/arty-devices.html", "ABCD-EFGH"),
    "https://starfire.example/arty-devices.html?code=ABCD-EFGH");
  assert.equal(approveElsewhereLink("", "ABCD-EFGH"), null);
  assert.equal(approveElsewhereLink("javascript:alert(1)", "ABCD-EFGH"), null);
  assert.equal(approveElsewhereLink("https://starfire.example/arty-devices.html", "not a code"), null);
});

test("the approve page is asked of the brain this page came from, without a key", async () => {
  const { calls, fetchImpl } = fakeFetch(reply(200, { approve_page: "https://starfire.example/arty-devices.html" }));
  assert.equal(await approvePage({ base: "https://arty.example", fetchImpl }), "https://starfire.example/arty-devices.html");
  assert.equal(calls[0].url, "https://arty.example/v1/device/approver");
  assert.equal(calls[0].headers?.Authorization, undefined);
});

test("no approve page is offered when the brain can't say or has none", async () => {
  assert.equal(await approvePage({ fetchImpl: fakeFetch(reply(200, { approve_page: "" })).fetchImpl }), "");
  assert.equal(await approvePage({ fetchImpl: fakeFetch(reply(404, {})).fetchImpl }), "");
  assert.equal(await approvePage({ fetchImpl: async () => { throw new Error("offline"); } }), "");
  assert.equal(await approvePage({ fetchImpl: fakeFetch(reply(200, { approve_page: 5 })).fetchImpl }), "");
});
