import assert from "node:assert/strict";
import { test } from "node:test";
import { allowCard, isCameraAction, phoneLink, photoQuestion, splitPhoneAction } from "../js/phone.js";

const line = (action) => `<phone-action>${JSON.stringify(action)}</phone-action>`;

test("the phone action line is taken out of the reply and read", () => {
  const reply = `Opening a call to Mum. Check it's the right number, then press call.\n\n${line({ kind: "call", who: "07700 900123", body: "" })}`;
  assert.deepEqual(splitPhoneAction(reply), {
    text: "Opening a call to Mum. Check it's the right number, then press call.",
    action: { kind: "call", who: "07700 900123", body: "" },
  });
});

test("a reply without an action, or with a broken one, is left as it is", () => {
  assert.deepEqual(splitPhoneAction("Plain answer."), { text: "Plain answer.", action: null });
  assert.deepEqual(splitPhoneAction("Hi\n<phone-action>{not json</phone-action>"), { text: "Hi", action: null });
  assert.deepEqual(splitPhoneAction(`Hi ${line({ kind: "launch", who: "x" })}`), { text: "Hi", action: null });
  assert.deepEqual(splitPhoneAction(null), { text: "", action: null });
});

test("numbers become tel links, and a text opens Messages with only the words filled in", () => {
  assert.equal(phoneLink({ kind: "call", who: "07700 900123", body: "" }), "tel:07700900123");
  assert.equal(phoneLink({ kind: "call", who: "+44 (0)7700-900123", body: "" }), "tel:+4407700900123");
  assert.equal(phoneLink({ kind: "text", who: "07700900123", body: "I'll be late & sorry" }), "sms:&body=I'll%20be%20late%20%26%20sorry");
  assert.equal(phoneLink({ kind: "text", who: "07700900123", body: "" }), "sms:");
});

test("a text gets an Allow card with the exact words, and the recipient is picked on the phone", () => {
  assert.deepEqual(allowCard({ kind: "text", who: "Sam", body: "Running late" }), {
    title: "Arty asks to open Messages with this text",
    lines: ["Text: Running late", "To: you pick Sam from your contacts in Messages"],
    button: "Open Messages",
  });
  assert.equal(allowCard({ kind: "call", who: "Sam", body: "" }), null);
});

test("alarms, timers and apps are not something the iPhone app acts on", () => {
  for (const action of [{ kind: "alarm", hour: 7, minute: 0 }, { kind: "timer", seconds: 60 }, { kind: "app", app: "Spotify" }]) {
    assert.equal(splitPhoneAction(`Done. ${line(action)}`).action, null);
  }
});

test("a name opens Messages with the text but no recipient, and a call needs a number", () => {
  assert.equal(phoneLink({ kind: "text", who: "Mum", body: "On my way" }), "sms:&body=On%20my%20way");
  assert.equal(phoneLink({ kind: "call", who: "Mum", body: "" }), null);
});

test("anything that could smuggle another link in is refused", () => {
  assert.equal(phoneLink({ kind: "call", who: "123;javascript:alert(1)", body: "" }), null);
  assert.equal(phoneLink({ kind: "call", who: "12", body: "" }), null);
  assert.equal(phoneLink({ kind: "call", who: "1".repeat(16), body: "" }), null);
  assert.equal(phoneLink({ kind: "text", who: "07700900123?x=1", body: "" }), "sms:");
  assert.equal(phoneLink({ kind: "text", who: "+447700900123", body: "hi" }), "sms:&body=hi");
  assert.equal(phoneLink(null), null);
});

test("a camera action is read with its question and has no link", () => {
  const reply = `Tap the button to open the camera.\n\n${line({ kind: "camera", question: "Read the label" })}`;
  const { text, action } = splitPhoneAction(reply);
  assert.equal(text, "Tap the button to open the camera.");
  assert.deepEqual(action, { kind: "camera", question: "Read the label" });
  assert.equal(isCameraAction(action), true);
  assert.equal(phoneLink(action), null);
});

test("a camera action with a missing or odd question is still a camera action with an empty one", () => {
  assert.deepEqual(splitPhoneAction(`Hi ${line({ kind: "camera" })}`).action, { kind: "camera", question: "" });
  assert.deepEqual(splitPhoneAction(`Hi ${line({ kind: "camera", question: 5 })}`).action, { kind: "camera", question: "" });
  assert.equal(splitPhoneAction(`Hi ${line({ kind: "camera", question: "x".repeat(1000) })}`).action.question.length, 300);
  assert.equal(isCameraAction({ kind: "call", who: "Mum" }), false);
  assert.equal(isCameraAction(null), false);
});

test("the photo's question is what you typed, else what Arty heard, else a plain default", () => {
  assert.equal(photoQuestion({ kind: "camera", question: "Read the label" }, "what colour is it?"), "what colour is it?");
  assert.equal(photoQuestion({ kind: "camera", question: "Read the label" }, "  "), "Read the label");
  assert.equal(photoQuestion({ kind: "camera", question: "" }, ""), "What is in this photo?");
  assert.equal(photoQuestion(null), "What is in this photo?");
});
