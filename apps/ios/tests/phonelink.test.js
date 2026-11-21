import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import {
  ARTY, LINK_CONTEXT, LinkError, PHONE, PHONE_SEALING, blinds, codeScalar, deviceKey, elementFromHex, isLinkOffer, linkPhone, openSealed, phoneName,
  proof, sharedKey, shareHex,
} from "../js/phonelink.js";

const vectors = JSON.parse(readFileSync(new URL("./fixtures/phone_link_vectors.json", import.meta.url), "utf8"));
const OFFER = "offer-id-123";
const phoneSecret = BigInt(`0x${vectors.phone_secret_hex}`);
const artySecret = BigInt(`0x${vectors.arty_secret_hex}`);

function bytesOfHex(hex) {
  return Uint8Array.from(hex.match(/../g), (pair) => parseInt(pair, 16));
}

function fakeArty({ code = vectors.code, access = "sealed", wrongProof = false, status = 200,
  sealed = vectors.sealed_access, answeredShare = null } = {}) {
  const calls = [];
  let key;
  const fetchImpl = async (url, options) => {
    const body = JSON.parse(options.body);
    calls.push({ url, body, options });
    if (status !== 200) return { status, json: async () => ({ error: { message: "This code has run out or was already used." } }) };
    if (url.endsWith("/link/phone/begin")) {
      const share = await shareHex(artySecret, code, ARTY);
      key = await sharedKey(artySecret, code, ARTY, body.share, share);
      const shown = wrongProof ? "0".repeat(64) : await proof(key, ARTY);
      return { status: 200, json: async () => ({ session: "s1", share: answeredShare ?? share, proof: shown }) };
    }
    if (body.proof !== await proof(key, PHONE_SEALING)) return { status: 403, json: async () => ({ error: { message: "didn't prove it" } }) };
    if (access === "sealed") return { status: 200, json: async () => ({ linked: true, name: "iPhone", sealed_access: sealed }) };
    const clear = access === "clear" ? { access: { id: "abc.access", secret: "s-456" } } : {};
    return { status: 200, json: async () => ({ linked: true, name: "iPhone", ...clear }) };
  };
  return { calls, fetchImpl };
}

test("the iPhone's sums match the brain's, so both sides make the same key", async () => {
  assert.equal(LINK_CONTEXT, vectors.context);
  const made = await blinds();
  assert.equal(made[PHONE].toString(16), vectors.blind_phone_hex);
  assert.equal(made[ARTY].toString(16), vectors.blind_arty_hex);
  assert.equal((await codeScalar(vectors.code)).toString(16), vectors.code_scalar_hex);
  assert.equal(await shareHex(phoneSecret, vectors.code, PHONE), vectors.phone_share);
  const key = await sharedKey(phoneSecret, vectors.code, PHONE, vectors.phone_share, vectors.arty_share);
  assert.deepEqual(key, bytesOfHex(vectors.key_hex));
  assert.equal(await proof(key, PHONE), vectors.phone_proof);
  assert.equal(await proof(key, PHONE_SEALING), vectors.phone_sealing_proof);
  assert.equal(await proof(key, ARTY), vectors.arty_proof);
  assert.equal(await deviceKey(key), vectors.device_key);
});

test("a share is only used when it lies in the prime-order subgroup", () => {
  for (const share of vectors.shares_in_group) assert.equal(elementFromHex(share), BigInt(`0x${share}`));
  for (const share of vectors.shares_refused) {
    assert.throws(() => elementFromHex(share), (error) => error instanceof LinkError && /wasn't a value this link can use/.test(error.message));
  }
});

test("Arty's share outside the subgroup stops the link before anything is proved or sent", async () => {
  for (const share of vectors.shares_refused) {
    const arty = fakeArty({ answeredShare: share });
    await assert.rejects(linkPhone({ offer: OFFER, code: vectors.code, fetchImpl: arty.fetchImpl, secret: phoneSecret }),
      (error) => error instanceof LinkError && /wasn't a value this link can use/.test(error.message));
    assert.deepEqual(arty.calls.map((call) => call.url), ["/link/phone/begin"]);
  }
});

test("scanning links the iPhone with its own key and the access pair, and the key is never sent", async () => {
  const arty = fakeArty();
  const settings = await linkPhone({ offer: OFFER, code: vectors.code, fetchImpl: arty.fetchImpl, secret: phoneSecret, name: "iPhone" });
  assert.deepEqual(settings, { apiKey: vectors.device_key, ...accessOf(JSON.parse(vectors.sealed_plain)) });
  assert.deepEqual(arty.calls.map((call) => call.url), ["/link/phone/begin", "/link/phone/finish"]);
  assert.deepEqual(arty.calls[0].body, { offer: OFFER, name: "iPhone", share: vectors.phone_share });
  for (const call of arty.calls) {
    assert.ok(!JSON.stringify(call).includes(vectors.device_key));
    assert.ok(!JSON.stringify(call).includes(vectors.code));
    assert.equal(call.options.headers.Authorization, undefined);
  }
});

test("at home with no Cloudflare the iPhone links with just its own key", async () => {
  const arty = fakeArty({ access: null });
  const settings = await linkPhone({ offer: OFFER, code: vectors.code, fetchImpl: arty.fetchImpl, secret: phoneSecret });
  assert.deepEqual(settings, { apiKey: vectors.device_key, accessId: "", accessSecret: "" });
});

test("an answer from something that doesn't know the code is refused before finishing", async () => {
  const arty = fakeArty({ wrongProof: true });
  await assert.rejects(linkPhone({ offer: OFFER, code: vectors.code, fetchImpl: arty.fetchImpl, secret: phoneSecret }),
    (error) => error instanceof LinkError && /didn't come from the Arty/.test(error.message));
  assert.equal(arty.calls.length, 1);
});

test("a code Arty made for another scan gives a different key, so finishing fails", async () => {
  const arty = fakeArty({ code: "ff".repeat(16) });
  await assert.rejects(linkPhone({ offer: OFFER, code: vectors.code, fetchImpl: arty.fetchImpl, secret: phoneSecret }), LinkError);
});

test("a used or late code shows Arty's own words", async () => {
  const arty = fakeArty({ status: 410 });
  await assert.rejects(linkPhone({ offer: OFFER, code: vectors.code, fetchImpl: arty.fetchImpl }), /run out or was already used/);
});

test("no connection, or Cloudflare in the way, says what to do", async () => {
  const offline = async () => { throw new TypeError("Load failed"); };
  await assert.rejects(linkPhone({ offer: OFFER, code: vectors.code, fetchImpl: offline }), /couldn't be reached to link/);
  const cloudflare = async () => ({ status: 0, json: async () => { throw new SyntaxError("opaque"); } });
  await assert.rejects(linkPhone({ offer: OFFER, code: vectors.code, fetchImpl: cloudflare }), /Cloudflare/);
});

test("only a whole offer is tried", async () => {
  assert.equal(isLinkOffer(OFFER, vectors.code), true);
  for (const [offer, code] of [["", vectors.code], [OFFER, "short"], ["bad offer!", vectors.code], [OFFER, vectors.code.toUpperCase()]]) {
    assert.equal(isLinkOffer(offer, code), false);
  }
  await assert.rejects(linkPhone({ offer: "x", code: "y", fetchImpl: async () => assert.fail("nothing is sent") }), /isn't complete/);
});

test("the phone names itself plainly", () => {
  assert.equal(phoneName("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)"), "iPhone");
  assert.equal(phoneName("Mozilla/5.0 (iPad; CPU OS 18_0 like Mac OS X)"), "iPad");
});

test("the sealed access the brain sends opens to the same pair here", async () => {
  const opened = await openSealed(bytesOfHex(vectors.key_hex), vectors.sealed_access);
  assert.equal(new TextDecoder().decode(opened), vectors.sealed_plain);
});

test("the iPhone proves it seals, so the request for sealing can't be stripped on the way", async () => {
  const arty = fakeArty();
  const settings = await linkPhone({ offer: OFFER, code: vectors.code, fetchImpl: arty.fetchImpl, secret: phoneSecret });
  assert.equal(arty.calls[1].body.proof, vectors.phone_sealing_proof);
  assert.equal(arty.calls[1].body.seal, 1);
  assert.deepEqual(settings, { apiKey: vectors.device_key, ...accessOf(JSON.parse(vectors.sealed_plain)) });
});

test("sealed access changed on the way is refused and nothing is saved", async () => {
  const flipped = vectors.sealed_access.slice(0, 40) + (vectors.sealed_access[40] === "0" ? "1" : "0") + vectors.sealed_access.slice(41);
  for (const sealed of [flipped, vectors.sealed_access.slice(0, 60), "not hex"]) {
    const arty = fakeArty({ sealed });
    await assert.rejects(linkPhone({ offer: OFFER, code: vectors.code, fetchImpl: arty.fetchImpl, secret: phoneSecret }),
      (error) => error instanceof LinkError && /changed on the way/.test(error.message));
  }
});

test("an access pair sent in the clear is refused and nothing is saved", async () => {
  const arty = fakeArty({ access: "clear" });
  await assert.rejects(linkPhone({ offer: OFFER, code: vectors.code, fetchImpl: arty.fetchImpl, secret: phoneSecret }),
    (error) => error instanceof LinkError && /changed on the way/.test(error.message));
});

function accessOf(pair) {
  return { accessId: pair.id, accessSecret: pair.secret };
}
