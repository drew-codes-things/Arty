import assert from "node:assert/strict";
import { test } from "node:test";

class FakeElement {
  constructor(tag) {
    this.tag = tag;
    this.children = [];
    this.listeners = {};
    this.attributes = {};
    this.textContent = "";
    this.hidden = false;
    this.disabled = false;
    this.classList = { add() {}, remove() {} };
    this.dataset = {};
  }
  setAttribute(name, value) { this.attributes[name] = value; }
  addEventListener(type, listener) { this.listeners[type] = listener; }
  append(child) { this.children.push(child); }
  replaceChildren(...children) { this.children = children; }
  get label() { return this.textContent || this.children.filter((child) => typeof child === "string").join(""); }
  get innerHTML() { throw new Error("device details must not go through innerHTML"); }
  set innerHTML(value) { throw new Error("device details must not go through innerHTML"); }
}

function fakePage() {
  const made = [];
  const toast = new FakeElement("div");
  globalThis.Node = FakeElement;
  globalThis.document = {
    createElement(tag) { const element = new FakeElement(tag); made.push(element); return element; },
    getElementById() { return toast; },
  };
  return { made, toast };
}

function texts(element) {
  return element.children.flatMap((child) => (typeof child === "string" ? [child] : texts(child)));
}

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));
const button = (made, label) => made.filter((element) => element.tag === "button" && element.label === label).at(-1);

const PHONE = { id: 3, name: "Pixel 9", address: "192.0.2.7", created: 1762860000, last_used: 1762870000 };
const SNEAKY = { id: 4, name: "<img src=x onerror=alert(1)>", address: "192.0.2.8", created: 1762860000, last_used: null };

test("approved devices are listed with where they came from", async () => {
  fakePage();
  const { renderDevices } = await import("../js/manage.js");
  const container = new FakeElement("div");
  await renderDevices(container, { devices: async () => [PHONE] });
  const shown = texts(container).join(" ");
  assert.match(shown, /Pixel 9/);
  assert.match(shown, /192\.0\.2\.7/);
  assert.match(shown, /Last used/);
});

test("with no devices the screen says so", async () => {
  fakePage();
  const { renderDevices } = await import("../js/manage.js");
  const container = new FakeElement("div");
  await renderDevices(container, { devices: async () => [] });
  assert.match(texts(container).join(" "), /No devices approved yet/);
});

test("a device name is shown as plain text, never as markup", async () => {
  fakePage();
  const { renderDevices } = await import("../js/manage.js");
  const container = new FakeElement("div");
  await renderDevices(container, { devices: async () => [SNEAKY] });
  assert.ok(texts(container).includes(SNEAKY.name));
  assert.match(texts(container).join(" "), /Never used/);
});

test("turning a device off needs a second tap and then refreshes the list", async () => {
  const { made } = fakePage();
  const { renderDevices } = await import("../js/manage.js");
  const revoked = [];
  let listed = 0;
  const api = {
    devices: async () => { listed += 1; return revoked.length ? [] : [PHONE]; },
    revokeDevice: async (id) => { revoked.push(id); },
  };
  const container = new FakeElement("div");
  await renderDevices(container, api);
  button(made, "Turn off").listeners.click();
  await settle();
  assert.deepEqual(revoked, []);
  button(made, "Tap again to turn it off").listeners.click();
  await settle();
  await settle();
  assert.deepEqual(revoked, [3]);
  assert.equal(listed, 2);
  assert.match(texts(container).join(" "), /No devices approved yet/);
});

test("a failed load offers to try again", async () => {
  const { made } = fakePage();
  const { renderDevices } = await import("../js/manage.js");
  const container = new FakeElement("div");
  await renderDevices(container, { devices: async () => { throw new Error("Arty's server isn't answering."); } });
  assert.match(texts(container).join(" "), /isn't answering/);
  assert.ok(button(made, "Try again"));
});
