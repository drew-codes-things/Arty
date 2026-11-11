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
  }
  setAttribute(name, value) { this.attributes[name] = value; }
  addEventListener(type, listener) { this.listeners[type] = listener; }
  append(child) { this.children.push(child); }
  replaceChildren(...children) { this.children = children; }
  get label() { return this.children.filter((child) => typeof child === "string").join(""); }
}

function fakePage() {
  const made = [];
  globalThis.Node = FakeElement;
  globalThis.document = { createElement(tag) { const element = new FakeElement(tag); made.push(element); return element; } };
  return made;
}

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));
const lastButton = (made, label) => made.filter((element) => element.tag === "button" && element.label === label).at(-1);

test("approving again after a failed try works without starting over", async () => {
  const made = fakePage();
  const { renderAuthorise } = await import("../js/authorise.js");
  const answers = [];
  const api = {
    pendingDevice: async () => ({ name: "laptop", address: "192.0.2.5" }),
    answerDevice: async (code, approve) => {
      answers.push([code, approve]);
      if (answers.length === 1) throw new Error("The network went away.");
    },
  };
  const container = new FakeElement("div");
  renderAuthorise(container, api, "ABCD-EFGH", () => {});
  await settle();
  made.find((element) => element.tag === "input").listeners.input({ target: { value: "abcd efgh" } });
  lastButton(made, "Approve").listeners.click();
  await settle();
  const status = made.find((element) => element.tag === "p" && element.textContent.includes("network"));
  assert.match(status.textContent, /try again/);
  lastButton(made, "Retry").listeners.click();
  await settle();
  assert.deepEqual(answers, [["ABCD-EFGH", true], ["ABCD-EFGH", true]]);
  assert.match(status.textContent, /^Approved/);
});
