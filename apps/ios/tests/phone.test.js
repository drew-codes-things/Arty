import assert from "node:assert/strict";
import { test } from "node:test";
import { phoneLink, splitPhoneAction } from "../js/phone.js";

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

test("numbers become tel and sms links, with the text filled in", () => {
  assert.equal(phoneLink({ kind: "call", who: "07700 900123", body: "" }), "tel:07700900123");
  assert.equal(phoneLink({ kind: "call", who: "+44 (0)7700-900123", body: "" }), "tel:+4407700900123");
  assert.equal(phoneLink({ kind: "text", who: "07700900123", body: "I'll be late & sorry" }), "sms:07700900123&body=I'll%20be%20late%20%26%20sorry");
  assert.equal(phoneLink({ kind: "text", who: "07700900123", body: "" }), "sms:07700900123");
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
  assert.equal(phoneLink(null), null);
});
