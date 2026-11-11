const test = require('node:test');
const assert = require('node:assert');
const proof = require('../service-proof');

const TOKEN = 'token-for-the-test';
const NONCE = '00112233445566778899aabbccddeeff';
const PROOF_FROM_THE_SERVICE = '1595e14e468ad0570764971152d859f22ebc14291d012bf85a719d4d612222fc';

test('the window and the local service agree on the proof', () => {
  assert.strictEqual(proof.expectedProof(TOKEN, NONCE), PROOF_FROM_THE_SERVICE);
});

test('only the service holding this launch\'s token passes', () => {
  assert.ok(proof.proofMatches(proof.expectedProof(TOKEN, NONCE), TOKEN, NONCE));
  assert.ok(!proof.proofMatches(proof.expectedProof('another-token', NONCE), TOKEN, NONCE));
  assert.ok(!proof.proofMatches(proof.expectedProof(TOKEN, NONCE), TOKEN, proof.newNonce()));
  assert.ok(!proof.proofMatches(undefined, TOKEN, NONCE));
  assert.ok(!proof.proofMatches('short', TOKEN, NONCE));
  assert.ok(!proof.proofMatches(proof.expectedProof('', NONCE), '', NONCE));
});

test('every check uses a fresh 32 character nonce', () => {
  const first = proof.newNonce();
  assert.match(first, /^[0-9a-f]{32}$/);
  assert.notStrictEqual(first, proof.newNonce());
});
