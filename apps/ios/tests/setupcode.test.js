import assert from "node:assert/strict";
import { test } from "node:test";
import { SetupCodeError, parseSetupCode } from "../js/setupcode.js";

const full = '{"arty":1,"url":"https://arty.example.com","key":"k-123","id":"abc.access","secret":"s-456"}';

test("a setup code gives the app key and the Access token", () => {
  assert.deepEqual(parseSetupCode(full), { apiKey: "k-123", accessId: "abc.access", accessSecret: "s-456" });
});

test("the Access token is optional and values are trimmed", () => {
  assert.deepEqual(parseSetupCode(' {"arty":1,"url":"http://arty-server:8081","key":" k "} '),
    { apiKey: "k", accessId: "", accessSecret: "" });
});

test("other QR codes are turned down", () => {
  for (const text of ["https://example.com", "", "[1,2]", "null", '{"key":"k"}']) {
    assert.throws(() => parseSetupCode(text), (error) => error instanceof SetupCodeError && /isn't an Arty setup code/.test(error.message), text);
  }
});

test("a newer format asks for an update", () => {
  assert.throws(() => parseSetupCode('{"arty":2,"key":"k"}'), /newer/);
});

test("missing parts are named", () => {
  assert.throws(() => parseSetupCode('{"arty":1,"url":"https://a.example"}'), /app key/);
  assert.throws(() => parseSetupCode('{"arty":1,"key":"k","id":"i"}'), /Cloudflare/);
});
