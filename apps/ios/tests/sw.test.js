import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

function loadWorker() {
  const handlers = {};
  const puts = [];
  const cache = {
    put: async (key, response) => { puts.push(typeof key === "string" ? key : key.url); },
    match: async () => undefined,
    addAll: async () => {},
  };
  const context = {
    URL, Request, Promise,
    self: {
      location: new URL("https://arty.example/ios/sw.js"),
      addEventListener: (name, handler) => { handlers[name] = handler; },
      skipWaiting: () => {}, clients: { claim: () => {} },
    },
    caches: { open: async () => cache, keys: async () => [], delete: async () => true },
    fetch: async () => ({ ok: true, clone() { return this; } }),
  };
  vm.runInNewContext(readFileSync(new URL("../sw.js", import.meta.url), "utf8"), context);
  return { handlers, puts };
}

async function visit(worker, url) {
  let answered;
  worker.handlers.fetch({ request: new Request(url), respondWith: (promise) => { answered = promise; } });
  await answered;
}

test("an approve link is cached without its one-time code", async () => {
  const worker = loadWorker();
  await visit(worker, "https://arty.example/ios/?authorise=ABCD-EFGH");
  assert.deepEqual(worker.puts, ["https://arty.example/ios/"]);
});

test("app files are still cached under their own address", async () => {
  const worker = loadWorker();
  await visit(worker, "https://arty.example/ios/js/app.js");
  assert.deepEqual(worker.puts, ["https://arty.example/ios/js/app.js"]);
});
