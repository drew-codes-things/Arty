import assert from "node:assert/strict";
import { test } from "node:test";
import { SetupCodeError, parseSetupCode, readScannedCode } from "../js/setupcode.js";

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
  assert.throws(() => parseSetupCode('{"arty":3,"key":"k"}'), /newer/);
  assert.throws(() => readScannedCode('{"arty":3,"offer":"offer-id-1"}'), /newer/);
});

const linkCode = '{"arty":2,"url":"https://arty.example.com","offer":"offer-id-1","code":"00112233445566778899aabbccddeeff"}';

test("a link code from Arty Desktop gives only the one-use offer", () => {
  assert.deepEqual(readScannedCode(linkCode), { link: { offer: "offer-id-1", code: "00112233445566778899aabbccddeeff" } });
});

test("an older code with keys in it still works", () => {
  assert.deepEqual(readScannedCode(full), { settings: { apiKey: "k-123", accessId: "abc.access", accessSecret: "s-456" } });
});

test("a damaged link code is turned down", () => {
  for (const text of ['{"arty":2,"offer":"offer-id-1"}', '{"arty":2,"offer":"x","code":"00112233445566778899aabbccddeeff"}',
    '{"arty":2,"offer":"offer-id-1","code":"not-hex"}']) {
    assert.throws(() => readScannedCode(text), /damaged/, text);
  }
  assert.throws(() => parseSetupCode(linkCode), /link code/);
});

test("missing parts are named", () => {
  assert.throws(() => parseSetupCode('{"arty":1,"url":"https://a.example"}'), /app key/);
  assert.throws(() => parseSetupCode('{"arty":1,"key":"k","id":"i"}'), /Cloudflare/);
});
