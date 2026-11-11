import assert from "node:assert/strict";
import { test } from "node:test";

const HAVE_ENOUGH_DATA = 4;
const WAIT_AFTER_PLAY_MS = 600;

class FakeElement {
  constructor(tag) {
    this.tag = tag;
    this.listeners = {};
    this.removed = false;
  }
  setAttribute() {}
  append() {}
  addEventListener(type, listener) { this.listeners[type] = listener; }
  remove() { this.removed = true; }
  getContext() {
    return { drawImage() {}, getImageData: (x, y, width, height) => ({ data: new Uint8ClampedArray(width * height * 4), width, height }) };
  }
}

function fakePage() {
  const page = { buttons: [], overlays: [], scripts: [], startPlaying: null, tracksStopped: 0, framesRead: 0 };
  globalThis.Node = FakeElement;
  globalThis.document = {
    createElement(tag) {
      const element = new FakeElement(tag);
      if (tag === "button") page.buttons.push(element);
      if (tag === "div") page.overlays.push(element);
      if (tag === "script") page.scripts.push(element);
      if (tag === "video") {
        Object.assign(element, { readyState: HAVE_ENOUGH_DATA, HAVE_ENOUGH_DATA, videoWidth: 640, videoHeight: 480 });
        element.play = () => new Promise((resolve) => { page.startPlaying = resolve; });
      }
      return element;
    },
    body: { append() {} },
    head: { append() {} },
  };
  globalThis.jsQR = () => { page.framesRead += 1; return null; };
  const stream = { getTracks: () => [{ stop: () => { page.tracksStopped += 1; } }] };
  Object.defineProperty(globalThis, "navigator", {
    value: { mediaDevices: { getUserMedia: async () => stream } }, configurable: true,
  });
  return page;
}

const settle = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

test("cancelling before the camera starts closes the scanner and reads no frames", async () => {
  const page = fakePage();
  const { scanQrCode } = await import("../js/scanner.js");
  const scanning = scanQrCode();
  await settle(0);
  page.buttons.at(-1).listeners.click();
  page.startPlaying();
  assert.equal(await scanning, null);
  await settle(WAIT_AFTER_PLAY_MS);
  assert.equal(page.framesRead, 0);
  assert.equal(page.tracksStopped, 1);
  assert.ok(page.overlays.at(-1).removed);
});

test("one frame that can't be read is skipped and the next one still finds the code", async () => {
  const page = fakePage();
  let frames = 0;
  globalThis.jsQR = () => {
    frames += 1;
    if (frames === 1) throw new Error("one odd frame");
    return { data: "the-code" };
  };
  const { scanQrCode } = await import("../js/scanner.js");
  const scanning = scanQrCode();
  await settle(0);
  page.startPlaying();
  assert.equal(await Promise.race([scanning, settle(2000).then(() => "still waiting")]), "the-code");
  assert.equal(page.tracksStopped, 1);
});

test("a frame that can't be read closes the scanner with an error instead of hanging", async () => {
  const page = fakePage();
  globalThis.jsQR = () => { throw new Error("odd frame"); };
  const { scanQrCode } = await import("../js/scanner.js");
  const scanning = scanQrCode();
  await settle(0);
  page.startPlaying();
  const outcome = await Promise.race([scanning.then(() => "resolved", (error) => error.message), settle(3000).then(() => "still waiting")]);
  assert.match(outcome, /odd frame/);
  assert.equal(page.tracksStopped, 1);
  assert.ok(page.overlays.at(-1).removed);
});

test("a reader script that loads but defines nothing is reported as a load problem, not a camera problem", async () => {
  const page = fakePage();
  delete globalThis.jsQR;
  const { scanQrCode } = await import("../js/scanner.js");
  const scanning = scanQrCode();
  await settle(0);
  page.scripts.at(-1).listeners.load();
  const outcome = await Promise.race([scanning.then(() => "resolved", (error) => error.message), settle(2000).then(() => "still waiting")]);
  assert.match(outcome, /QR code reader/);
});
