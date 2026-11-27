import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import {
  CHECK_EVERY_MS, FIRST_CHECK_DELAY_MS, MAX_MANIFEST_BYTES, createUpdateChecker, fetchLatest, isNewer, latestVersion, manifestAddress, noticeFor,
} from "../js/appupdate.js";
import { APP_VERSION } from "../js/version.js";
import { loadPreferences, savePreferences } from "../js/settings.js";

const SITE = new URL("https://arty.example.com/ios/");

function answer(body, { status = 200, headers = {} } = {}) {
  const bytes = new TextEncoder().encode(typeof body === "string" ? body : JSON.stringify(body));
  return {
    ok: status >= 200 && status < 300, status,
    headers: { get: (name) => headers[name.toLowerCase()] ?? null },
    body: new Blob([bytes]).stream(),
  };
}

const manifest = (version) => ({ builds: {}, latest: { ios: { version }, desktop: { version: "9.9.9" } } });

function memoryStorage() {
  const values = new Map();
  return { getItem: (key) => values.get(key) ?? null, setItem: (key, value) => values.set(key, String(value)), removeItem: (key) => values.delete(key) };
}

test("the iPhone app is 1.8.0", () => {
  assert.equal(APP_VERSION, "1.8.0");
});

test("versions compare as numbers and tolerate a v or a tag prefix", () => {
  assert.ok(isNewer("1.10.0", "1.9.9"));
  assert.ok(isNewer("v1.8", "1.7.0"));
  assert.ok(isNewer("ios-v1.8.0", "1.7.0"));
  assert.ok(!isNewer("1.7", "1.7.0"));
  assert.ok(!isNewer("1.6.9", "1.7.0"));
  assert.ok(!isNewer("next", "1.7.0"));
});

test("a newer version shows until it is dismissed, and a later one shows again", () => {
  assert.deepEqual(noticeFor({ latest: "1.8.0", current: "1.7.0", dismissed: "", enabled: true }), { version: "1.8.0", text: "Version 1.8.0 is available" });
  assert.equal(noticeFor({ latest: "1.8.0", current: "1.7.0", dismissed: "1.8.0", enabled: true }), null);
  assert.ok(noticeFor({ latest: "1.9.0", current: "1.7.0", dismissed: "1.8.0", enabled: true }));
  assert.equal(noticeFor({ latest: "1.8.0", current: "1.7.0", dismissed: "", enabled: false }), null);
});

test("the manifest is read defensively", () => {
  assert.equal(latestVersion(manifest("1.8.0")), "1.8.0");
  for (const bad of [null, "x", [], {}, { latest: [] }, { latest: { ios: "1.8.0" } }, manifest(1.8), manifest("1.8.0 beta")]) {
    assert.equal(latestVersion(bad), null);
  }
});

test("only an https page checks, and it asks its own site with no cookies or redirects", async () => {
  assert.equal(manifestAddress(new URL("http://192.0.2.13:8081/ios/")), null);
  assert.equal(manifestAddress(SITE), "https://arty.example.com/download/manifest.json");
  const calls = [];
  const fetch = async (url, options) => { calls.push({ url, options }); return answer(manifest("1.8.0")); };
  assert.equal(await fetchLatest({ fetch, location: SITE }), "1.8.0");
  assert.equal(calls.length, 1);
  assert.equal(calls[0].options.credentials, "omit");
  assert.equal(calls[0].options.redirect, "error");
  assert.ok(calls[0].options.signal instanceof AbortSignal);
  await assert.rejects(fetchLatest({ fetch, location: new URL("http://192.0.2.13:8081/ios/") }), /not on https/);
  assert.equal(calls.length, 1);
});

test("a big answer, a failed status or bad JSON gives no version", async () => {
  const huge = JSON.stringify({ ...manifest("9.0.0"), padding: "x".repeat(MAX_MANIFEST_BYTES) });
  await assert.rejects(fetchLatest({ fetch: async () => answer(huge), location: SITE }), /over 262144 bytes/);
  await assert.rejects(fetchLatest({ fetch: async () => answer("", { status: 503 }), location: SITE }), /503/);
  await assert.rejects(fetchLatest({ fetch: async () => answer("<html>"), location: SITE }), /not valid JSON/);
});

test("it checks shortly after start, then once a day, and logs one line when it can't", async () => {
  const waiting = [];
  const timers = { setTimeout: (callback, delay) => waiting.push({ callback, delay }), clearTimeout: () => {} };
  const shown = [];
  const lines = [];
  let fails = true;
  const checker = createUpdateChecker({
    fetch: async () => { if (fails) throw new TypeError("Load failed"); return answer(manifest("1.9.0")); },
    location: SITE, settings: () => ({ enabled: true, dismissed: "" }), show: (notice) => shown.push(notice), log: (line) => lines.push(line), timers,
  });
  checker.start();
  assert.equal(waiting[0].delay, FIRST_CHECK_DELAY_MS);
  await waiting.shift().callback();
  assert.deepEqual(lines, ["Arty: update check skipped (Load failed)"]);
  assert.deepEqual(shown, []);
  assert.equal(waiting[0].delay, CHECK_EVERY_MS);
  fails = false;
  await waiting.shift().callback();
  assert.deepEqual(shown, [{ version: "1.9.0", text: "Version 1.9.0 is available" }]);
});

test("the setting is on by default and the dismissed version is remembered", () => {
  const storage = memoryStorage();
  assert.equal(loadPreferences(storage).updateCheck, true);
  assert.equal(loadPreferences(storage).dismissedUpdate, "");
  savePreferences(storage, { updateCheck: false, dismissedUpdate: "1.8.0" });
  assert.equal(loadPreferences(storage).updateCheck, false);
  assert.equal(loadPreferences(storage).dismissedUpdate, "1.8.0");
  storage.setItem("arty.preferences", JSON.stringify({ updateCheck: "yes", dismissedUpdate: 5 }));
  assert.equal(loadPreferences(storage).updateCheck, true);
  assert.equal(loadPreferences(storage).dismissedUpdate, "");
});

test("the page has the notice, the More screen the switch, and the worker caches the new files", () => {
  const page = readFileSync(new URL("../index.html", import.meta.url), "utf8");
  const app = readFileSync(new URL("../js/app.js", import.meta.url), "utf8");
  const worker = readFileSync(new URL("../sw.js", import.meta.url), "utf8");
  assert.match(page, /id="update-notice"[^>]*hidden/);
  assert.match(app, /Tell me about new versions/);
  assert.match(app, /target: "_blank"/);
  assert.match(worker, /"js\/appupdate\.js"/);
  assert.match(worker, /"js\/version\.js"/);
  assert.match(worker, /arty-ios-v20/);
});
