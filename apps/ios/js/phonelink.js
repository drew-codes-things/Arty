export const LINK_CONTEXT = "arty-phone-link-v1";
export const DEVICE_KEY_PREFIX = "arty_dev_";
export const PHONE = "app";
export const PHONE_SEALING = "app-sealed-1";
export const ARTY = "arty";

const PRIME = BigInt("0x"
  + "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD129024E088A67CC74020BBEA63B139B22514A08798E3404DD"
  + "EF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7ED"
  + "EE386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3DC2007CB8A163BF0598DA48361C55D39A69163FA8FD24CF5F"
  + "83655D23DCA3AD961C62F356208552BB9ED529077096966D670C354E4ABC9804F1746C08CA18217C32905E462E36CE3B"
  + "E39E772C180E86039B2783A2EC07A28FB5C55DF06F4C52C9DE2BCBF6955817183995497CEA956AE515D2261898FA0510"
  + "15728E5A8AACAA68FFFFFFFFFFFFFFFF");
const GENERATOR = 2n;
const ORDER = (PRIME - 1n) / 2n;
const ELEMENT_BYTES = 256;
const SHARE_FORMAT = /^[0-9a-f]{512}$/;
const HASH_BLOCKS_PER_ELEMENT = 5;
const SECRET_BYTES = ELEMENT_BYTES + 32;
const OFFER_FORMAT = /^[A-Za-z0-9_-]{8,64}$/;
const CODE_FORMAT = /^[0-9a-f]{32}$/;
const LINK_TIMEOUT_MS = 30000;
const SEALED_ACCESS_VERSION = 1;
const SEAL_NONCE_BYTES = 16;
const SEAL_TAG_BYTES = 32;
const SEAL_BLOCK_BYTES = 32;
const SEAL_COUNTER_BYTES = 4;
const SEALED_FORMAT = /^(?:[0-9a-f]{2})+$/;
const UNREACHABLE = "Arty couldn't be reached to link this iPhone. Open Arty from its usual address and try again. "
  + "Away from home, the link page needs to be open in Cloudflare (see docs/connecting-a-phone.md).";
const NOT_THIS_ARTY = "That answer didn't come from the Arty that showed the code, so nothing was saved. Show a new code and scan again.";
const CHANGED_ON_THE_WAY = "Arty's answer to the link was changed on the way, so nothing was saved. Show a new code and scan again.";

const encoder = new TextEncoder();

export class LinkError extends Error {}

function joined(...parts) {
  const bytes = parts.map((part) => (typeof part === "string" ? encoder.encode(part) : part));
  const out = new Uint8Array(bytes.reduce((total, part) => total + part.length, 0));
  let at = 0;
  for (const part of bytes) {
    out.set(part, at);
    at += part.length;
  }
  return out;
}

function hexOf(bytes) {
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function bytesOfHex(hex) {
  const out = new Uint8Array(hex.length / 2);
  for (let index = 0; index < out.length; index += 1) out[index] = parseInt(hex.slice(index * 2, index * 2 + 2), 16);
  return out;
}

function numberOf(bytes) {
  return bytes.length ? BigInt(`0x${hexOf(bytes)}`) : 0n;
}

function elementBytes(element) {
  return bytesOfHex(element.toString(16).padStart(ELEMENT_BYTES * 2, "0"));
}

function base64Url(bytes) {
  return btoa(String.fromCharCode(...bytes)).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
}

export function modPow(base, exponent, modulus) {
  let result = 1n;
  let square = base % modulus;
  let remaining = exponent;
  while (remaining > 0n) {
    if (remaining & 1n) result = (result * square) % modulus;
    square = (square * square) % modulus;
    remaining >>= 1n;
  }
  return result;
}

async function sha512(bytes) {
  return new Uint8Array(await crypto.subtle.digest("SHA-512", bytes));
}

async function hmacSha256(key, message) {
  const imported = await crypto.subtle.importKey("raw", key, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return new Uint8Array(await crypto.subtle.sign("HMAC", imported, message));
}

async function hashedElement(label) {
  const blocks = [];
  for (let index = 0; index < HASH_BLOCKS_PER_ELEMENT; index += 1) {
    blocks.push(await sha512(joined(LINK_CONTEXT, "|", label, new Uint8Array([index]))));
  }
  return modPow(numberOf(joined(...blocks)) % PRIME, 2n, PRIME);
}

let blindsMade = null;

export function blinds() {
  blindsMade ??= Promise.all([hashedElement("M"), hashedElement("N")]).then(([phone, arty]) => ({ [PHONE]: phone, [ARTY]: arty }));
  return blindsMade;
}

export async function codeScalar(code) {
  return numberOf(await sha512(joined(LINK_CONTEXT, "|code|", code))) % ORDER;
}

export function newSecret(randomBytes = (bytes) => crypto.getRandomValues(bytes)) {
  return (numberOf(randomBytes(new Uint8Array(SECRET_BYTES))) % (ORDER - 1n)) + 1n;
}

export async function shareHex(secret, code, role) {
  const blind = (await blinds())[role];
  const element = (modPow(GENERATOR, secret, PRIME) * modPow(blind, await codeScalar(code), PRIME)) % PRIME;
  return hexOf(elementBytes(element));
}

export function elementFromHex(text) {
  if (typeof text !== "string" || !SHARE_FORMAT.test(text)) throw new LinkError("Arty's answer to the link wasn't the right shape.");
  const element = BigInt(`0x${text}`);
  const isInGroup = element > 1n && element < PRIME - 1n && modPow(element, ORDER, PRIME) === 1n;
  if (!isInGroup) throw new LinkError("Arty's answer to the link wasn't a value this link can use.");
  return element;
}

export async function sharedKey(secret, code, role, phoneShare, artyShare) {
  const theirs = elementFromHex(role === PHONE ? artyShare : phoneShare);
  elementFromHex(role === PHONE ? phoneShare : artyShare);
  const scalar = await codeScalar(code);
  const otherBlind = (await blinds())[role === PHONE ? ARTY : PHONE];
  const unblinded = (theirs * modPow(otherBlind, ORDER - scalar, PRIME)) % PRIME;
  const shared = modPow(unblinded, secret, PRIME);
  const transcript = joined(LINK_CONTEXT, bytesOfHex(phoneShare), bytesOfHex(artyShare), elementBytes(shared), elementBytes(scalar));
  return new Uint8Array(await crypto.subtle.digest("SHA-256", transcript));
}

export async function proof(key, role) {
  return hexOf(await hmacSha256(key, joined(LINK_CONTEXT, "|proof|", role)));
}

export async function deviceKey(key) {
  return DEVICE_KEY_PREFIX + base64Url(await hmacSha256(key, joined(LINK_CONTEXT, "|token")));
}

async function sealKey(key, label) {
  return hmacSha256(key, joined(LINK_CONTEXT, label));
}

async function keystream(key, nonce, length) {
  const streamKey = await sealKey(key, "|seal-stream");
  const out = new Uint8Array(length);
  for (let block = 0; block * SEAL_BLOCK_BYTES < length; block += 1) {
    const counter = new Uint8Array(SEAL_COUNTER_BYTES);
    new DataView(counter.buffer).setUint32(0, block);
    const piece = await hmacSha256(streamKey, joined(nonce, counter));
    out.set(piece.slice(0, Math.min(SEAL_BLOCK_BYTES, length - block * SEAL_BLOCK_BYTES)), block * SEAL_BLOCK_BYTES);
  }
  return out;
}

function sameBytes(first, second) {
  let difference = first.length ^ second.length;
  for (let index = 0; index < Math.min(first.length, second.length); index += 1) difference |= first[index] ^ second[index];
  return difference === 0;
}

export async function openSealed(key, sealed) {
  if (typeof sealed !== "string" || !SEALED_FORMAT.test(sealed)) throw new LinkError(CHANGED_ON_THE_WAY);
  const raw = bytesOfHex(sealed);
  if (raw.length < SEAL_NONCE_BYTES + SEAL_TAG_BYTES) throw new LinkError(CHANGED_ON_THE_WAY);
  const nonce = raw.slice(0, SEAL_NONCE_BYTES);
  const body = raw.slice(SEAL_NONCE_BYTES, raw.length - SEAL_TAG_BYTES);
  const tag = raw.slice(raw.length - SEAL_TAG_BYTES);
  if (!sameBytes(tag, await hmacSha256(await sealKey(key, "|seal-tag"), joined(nonce, body)))) throw new LinkError(CHANGED_ON_THE_WAY);
  const stream = await keystream(key, nonce, body.length);
  return body.map((byte, index) => byte ^ stream[index]);
}

async function accessFrom(finished, key) {
  if (finished.sealed_access === undefined) {
    if (finished.access !== undefined && finished.access !== null) throw new LinkError(CHANGED_ON_THE_WAY);
    return {};
  }
  const opened = new TextDecoder().decode(await openSealed(key, finished.sealed_access));
  try {
    const access = JSON.parse(opened);
    return access && typeof access === "object" ? access : {};
  } catch {
    throw new LinkError(CHANGED_ON_THE_WAY);
  }
}

export function isLinkOffer(offer, code) {
  return OFFER_FORMAT.test(String(offer ?? "")) && CODE_FORMAT.test(String(code ?? ""));
}

export function phoneName(userAgent = globalThis.navigator?.userAgent ?? "") {
  return /iPad/.test(userAgent) ? "iPad" : "iPhone";
}

async function postStep(fetchImpl, base, step, body) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), LINK_TIMEOUT_MS);
  let response;
  try {
    response = await fetchImpl(`${base}/link/phone/${step}`, {
      method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" }, body: JSON.stringify(body),
      signal: controller.signal, redirect: "manual", cache: "no-store",
    });
  } catch {
    throw new LinkError(UNREACHABLE);
  } finally {
    clearTimeout(timer);
  }
  const answer = await response.json().catch(() => null);
  if (response.status === 200 && answer) return answer;
  const message = answer?.error?.message;
  throw new LinkError(typeof message === "string" && message ? message : UNREACHABLE);
}

export async function linkPhone({ offer, code, base = "", name = phoneName(), fetchImpl = (...args) => globalThis.fetch(...args),
  secret = newSecret() }) {
  if (!isLinkOffer(offer, code)) throw new LinkError("That link code isn't complete. Show a new one in Arty Desktop.");
  const home = base.replace(/\/+$/, "");
  const myShare = await shareHex(secret, code, PHONE);
  const begun = await postStep(fetchImpl, home, "begin", { offer, name, share: myShare });
  const key = await sharedKey(secret, code, PHONE, myShare, begun.share);
  if (typeof begun.proof !== "string" || begun.proof !== await proof(key, ARTY)) throw new LinkError(NOT_THIS_ARTY);
  const finished = await postStep(fetchImpl, home, "finish",
    { offer, session: begun.session, proof: await proof(key, PHONE_SEALING), seal: SEALED_ACCESS_VERSION });
  const access = await accessFrom(finished, key);
  return {
    apiKey: await deviceKey(key),
    accessId: typeof access.id === "string" ? access.id : "",
    accessSecret: typeof access.secret === "string" ? access.secret : "",
  };
}
