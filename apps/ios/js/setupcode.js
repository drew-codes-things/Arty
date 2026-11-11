export const SETUP_CODE_VERSION = 1;
const NOT_A_SETUP_CODE = "That isn't an Arty setup code. On the computer, open Arty's tray menu and choose Phone setup code.";

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

export function parseSetupCode(scanned) {
  const code = readObject(scanned);
  const version = Number.isInteger(code.arty) ? code.arty : 0;
  if (version < SETUP_CODE_VERSION) throw new SetupCodeError(NOT_A_SETUP_CODE);
  if (version > SETUP_CODE_VERSION) throw new SetupCodeError("This setup code is from a newer Arty. Reload this app, then scan it again.");
  const settings = { apiKey: field(code, "key"), accessId: field(code, "id"), accessSecret: field(code, "secret") };
  if (!settings.apiKey) throw new SetupCodeError("The setup code has no app key in it.");
  if (Boolean(settings.accessId) !== Boolean(settings.accessSecret)) {
    throw new SetupCodeError("The setup code has only half of the Cloudflare Access token.");
  }
  return settings;
}
