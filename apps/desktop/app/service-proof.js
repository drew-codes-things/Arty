'use strict';
const crypto = require('crypto');

const CONTEXT = 'arty-service:';
const NONCE_BYTES = 16;

function newNonce() {
  return crypto.randomBytes(NONCE_BYTES).toString('hex');
}

function expectedProof(token, nonce) {
  return crypto.createHmac('sha256', token).update(`${CONTEXT}${nonce}`).digest('hex');
}

function proofMatches(proof, token, nonce) {
  if (!token || typeof proof !== 'string') return false;
  const expected = Buffer.from(expectedProof(token, nonce));
  const given = Buffer.from(proof);
  return given.length === expected.length && crypto.timingSafeEqual(given, expected);
}

function trustAfterCheck({ proven, answered, childAlive, trusted }) {
  if (proven) return true;
  if (answered || !childAlive) return false;
  return trusted;
}

function createPortWarning() {
  let shown = false;
  return {
    shouldShow() {
      if (shown) return false;
      shown = true;
      return true;
    },
    reset() { shown = false; },
  };
}

module.exports = { newNonce, expectedProof, proofMatches, trustAfterCheck, createPortWarning };
