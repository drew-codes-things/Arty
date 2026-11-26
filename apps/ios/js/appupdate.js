import { APP_VERSION } from "./version.js";

export const MANIFEST_PATH = "/download/manifest.json";
export const RELEASE_PAGE_PATH = "/download/";
export const CLIENT_KEY = "ios";
export const FIRST_CHECK_DELAY_MS = 10 * 1000;
export const CHECK_EVERY_MS = 24 * 60 * 60 * 1000;
export const REQUEST_TIMEOUT_MS = 5000;
export const MAX_MANIFEST_BYTES = 256 * 1024;
const SECURE_SCHEME = "https:";
const VERSION_TEXT = /^(?:[a-z]+-)?v?(\d+(?:\.\d+){0,3})$/i;

export function versionParts(text) {
  const found = VERSION_TEXT.exec(String(text ?? "").trim());
  return found ? found[1].split(".").map(Number) : null;
}

export function isNewer(latest, current) {
  const newer = versionParts(latest);
  const mine = versionParts(current);
  if (!newer || !mine) return false;
  for (let index = 0; index < Math.max(newer.length, mine.length); index += 1) {
    const difference = (newer[index] || 0) - (mine[index] || 0);
    if (difference !== 0) return difference > 0;
  }
  return false;
}

export function latestVersion(manifest, client = CLIENT_KEY) {
  const entry = manifest && typeof manifest === "object" ? manifest.latest?.[client] : null;
  const parts = entry && typeof entry === "object" && typeof entry.version === "string" ? versionParts(entry.version) : null;
  return parts ? parts.join(".") : null;
}

export function noticeFor({ latest, current = APP_VERSION, dismissed, enabled }) {
  if (!enabled || !latest || latest === dismissed || !isNewer(latest, current)) return null;
  return { version: latest, text: `Version ${latest} is available` };
}

export async function readCapped(response, maxBytes = MAX_MANIFEST_BYTES) {
  const declared = Number(response.headers?.get?.("content-length"));
  if (Number.isFinite(declared) && declared > maxBytes) throw new Error(`the manifest is ${declared} bytes, over ${maxBytes}`);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let text = "";
  let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > maxBytes) {
      await reader.cancel();
      throw new Error(`the manifest is over ${maxBytes} bytes`);
    }
    text += decoder.decode(value, { stream: true });
  }
  return text + decoder.decode();
}

export function manifestAddress(location) {
  if (location?.protocol !== SECURE_SCHEME) return null;
  return new URL(MANIFEST_PATH, location.origin).href;
}

export async function fetchLatest({ fetch, location }) {
  const address = manifestAddress(location);
  if (!address) throw new Error("the app is not on https, so it does not check");
  const response = await fetch(address, {
    method: "GET", redirect: "error", credentials: "omit", cache: "no-store",
    headers: { Accept: "application/json" }, signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
  });
  if (!response.ok) throw new Error(`the manifest answered ${response.status}`);
  let manifest;
  try {
    manifest = JSON.parse(await readCapped(response));
  } catch (error) {
    if (error instanceof SyntaxError) throw new Error("the manifest is not valid JSON");
    throw error;
  }
  const latest = latestVersion(manifest);
  if (!latest) throw new Error(`the manifest names no ${CLIENT_KEY} version`);
  return latest;
}

export function createUpdateChecker({ fetch, location, settings, show, log, timers = { setTimeout, clearTimeout } }) {
  let latest = null;
  let timer = null;

  const refresh = () => show(noticeFor({ latest, ...settings() }));

  async function check() {
    if (!settings().enabled) return;
    try {
      latest = await fetchLatest({ fetch, location });
    } catch (error) {
      log(`Arty: update check skipped (${error.message})`);
      return;
    }
    refresh();
  }

  function schedule(delay) {
    timer = timers.setTimeout(async () => {
      await check();
      schedule(CHECK_EVERY_MS);
    }, delay);
  }

  return { start: () => schedule(FIRST_CHECK_DELAY_MS), stop: () => timers.clearTimeout(timer), check, refresh };
}
