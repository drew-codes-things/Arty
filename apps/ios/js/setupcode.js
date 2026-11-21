import { isLinkOffer } from "./phonelink.js";

export const SETUP_CODE_VERSION = 1;
export const LINK_CODE_VERSION = 2;
const NOT_A_SETUP_CODE = "That isn't an Arty setup code. On the computer, open Arty's tray menu and choose Link a phone.";

export class SetupCodeError extends Error {}

function readObject(scanned) {
  try {
    const value = JSON.parse(String(scanned ?? "").trim());
    if (value && typeof value === "object" && !Array.isArray(value)) return value;
  } catch {
    throw new SetupCodeError(NOT_A_SETUP_CODE);
  }
  throw new SetupCodeError(NOT_A_SETUP_CODE);
}

function field(code, name) {
  const value = code[name];
  return typeof value === "string" ? value.trim() : "";
}

function versionOf(code) {
  const version = Number.isInteger(code.arty) ? code.arty : 0;
  if (version < SETUP_CODE_VERSION) throw new SetupCodeError(NOT_A_SETUP_CODE);
  if (version > LINK_CODE_VERSION) throw new SetupCodeError("This setup code is from a newer Arty. Reload this app, then scan it again.");
  return version;
}

function linkOffer(code) {
  const link = { offer: field(code, "offer"), code: field(code, "code") };
  if (!isLinkOffer(link.offer, link.code)) throw new SetupCodeError("This link code is damaged. Show a new one in Arty Desktop.");
  return link;
}

export function readScannedCode(scanned) {
  const code = readObject(scanned);
  if (versionOf(code) === LINK_CODE_VERSION) return { link: linkOffer(code) };
  return { settings: parseSetupCode(scanned) };
}

export function parseSetupCode(scanned) {
  const code = readObject(scanned);
  if (versionOf(code) !== SETUP_CODE_VERSION) throw new SetupCodeError("This is a link code: scan it from the first screen.");
  const settings = { apiKey: field(code, "key"), accessId: field(code, "id"), accessSecret: field(code, "secret") };
  if (!settings.apiKey) throw new SetupCodeError("The setup code has no app key in it.");
  if (Boolean(settings.accessId) !== Boolean(settings.accessSecret)) {
    throw new SetupCodeError("The setup code has only half of the Cloudflare Access token.");
  }
  return settings;
}
