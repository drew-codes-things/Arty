const SOURCE_FILE = "update-source.json";
const SITE_MANIFEST_PATH = "/download/manifest.json";
const SITE_RELEASE_PATH = "/download/";
const SECURE_SCHEME = "https:";
const UPDATE_CLIENT = "gmail_extension";
const UPDATE_ALARM = "arty-update-check";
const UPDATE_NOTIFICATION = "arty-update";
const FIRST_UPDATE_CHECK_MINUTES = 1;
const UPDATE_CHECK_EVERY_MINUTES = 24 * 60;
const UPDATE_TIMEOUT_MS = 5000;
const MAX_MANIFEST_BYTES = 256 * 1024;
const VERSION_TEXT = /^(?:[a-z]+-)?v?(\d+(?:\.\d+){0,3})$/i;

function versionParts(text) {
  const found = VERSION_TEXT.exec(String(text ?? "").trim());
  return found ? found[1].split(".").map(Number) : null;
}

function isNewer(latest, current) {
  const newer = versionParts(latest);
  const mine = versionParts(current);
  if (!newer || !mine) return false;
  for (let index = 0; index < Math.max(newer.length, mine.length); index += 1) {
    const difference = (newer[index] || 0) - (mine[index] || 0);
    if (difference !== 0) return difference > 0;
  }
  return false;
}

function latestVersion(manifest) {
  const entry = manifest && typeof manifest === "object" ? manifest.latest?.[UPDATE_CLIENT] : null;
  const parts = entry && typeof entry === "object" && typeof entry.version === "string" ? versionParts(entry.version) : null;
  return parts ? parts.join(".") : null;
}

function noticeFor({ latest, current, dismissed, enabled }) {
  if (!enabled || !latest || latest === dismissed || !isNewer(latest, current)) return null;
  return { version: latest, text: `Version ${latest} is available` };
}

async function readCapped(response) {
  const declared = Number(response.headers?.get?.("content-length"));
  if (Number.isFinite(declared) && declared > MAX_MANIFEST_BYTES) throw new Error(`the manifest is ${declared} bytes, over ${MAX_MANIFEST_BYTES}`);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let text = "";
  let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > MAX_MANIFEST_BYTES) {
      await reader.cancel();
      throw new Error(`the manifest is over ${MAX_MANIFEST_BYTES} bytes`);
    }
    text += decoder.decode(value, { stream: true });
  }
  return text + decoder.decode();
}

async function updateSource() {
  let written;
  try {
    const response = await fetch(chrome.runtime.getURL(SOURCE_FILE), { cache: "no-store" });
    if (!response.ok) throw new Error(`${response.status}`);
    written = await response.json();
  } catch (error) {
    throw new Error(`${SOURCE_FILE} is missing or unreadable (${error.message})`);
  }
  const site = written && typeof written === "object" && typeof written.site === "string" ? written.site.trim() : "";
  if (!site) throw new Error(`${SOURCE_FILE} names no Arty site`);
  const origin = new URL(site);
  if (origin.protocol !== SECURE_SCHEME) throw new Error(`refusing ${site}: only https is used`);
  return { manifest: new URL(SITE_MANIFEST_PATH, origin).href, page: new URL(SITE_RELEASE_PATH, origin).href };
}

async function fetchLatestVersion() {
  const { manifest: address } = await updateSource();
  const response = await fetch(address, {
    method: "GET", redirect: "error", credentials: "omit", cache: "no-store",
    headers: { Accept: "application/json" }, signal: AbortSignal.timeout(UPDATE_TIMEOUT_MS),
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
  if (!latest) throw new Error(`the manifest names no ${UPDATE_CLIENT} version`);
  return latest;
}

async function updateSettings() {
  const stored = await chrome.storage.local.get(["updateCheck", "dismissedUpdate", "latestVersion"]);
  return {
    enabled: stored.updateCheck !== false,
    dismissed: typeof stored.dismissedUpdate === "string" ? stored.dismissedUpdate : "",
    latest: typeof stored.latestVersion === "string" ? stored.latestVersion : null,
  };
}

async function showUpdateNotice() {
  const notice = noticeFor({ ...(await updateSettings()), current: chrome.runtime.getManifest().version });
  if (!notice) {
    await chrome.notifications.clear(UPDATE_NOTIFICATION);
    return null;
  }
  await chrome.notifications.create(UPDATE_NOTIFICATION, {
    type: "basic", iconUrl: "icon128.png", title: "Arty Gmail reader", message: notice.text,
    buttons: [{ title: "Download page" }], requireInteraction: true,
  });
  return notice;
}

async function checkForUpdate() {
  const { enabled } = await updateSettings();
  if (!enabled) return showUpdateNotice();
  try {
    await chrome.storage.local.set({ latestVersion: await fetchLatestVersion() });
  } catch (error) {
    console.info(`Arty: update check skipped (${error.message})`);
    return null;
  }
  return showUpdateNotice();
}

async function dismissUpdate(notificationId, byUser) {
  if (notificationId !== UPDATE_NOTIFICATION || !byUser) return;
  const { latest } = await updateSettings();
  if (latest) await chrome.storage.local.set({ dismissedUpdate: latest });
}

async function openReleasePage(notificationId) {
  if (notificationId !== UPDATE_NOTIFICATION) return;
  try {
    chrome.tabs.create({ url: (await updateSource()).page });
  } catch (error) {
    console.info(`Arty: the download page did not open (${error.message})`);
  }
}

function scheduleUpdateChecks() {
  chrome.alarms.create(UPDATE_ALARM, { delayInMinutes: FIRST_UPDATE_CHECK_MINUTES, periodInMinutes: UPDATE_CHECK_EVERY_MINUTES });
}

function whenUpdateAlarm(alarm) {
  if (alarm?.name === UPDATE_ALARM) checkForUpdate();
}

function whenUpdateSettingsChange(changes, area) {
  if (area === "local" && changes.updateCheck) showUpdateNotice();
}

chrome.alarms.onAlarm.addListener(whenUpdateAlarm);
chrome.runtime.onStartup.addListener(scheduleUpdateChecks);
chrome.runtime.onInstalled.addListener(scheduleUpdateChecks);
chrome.notifications.onButtonClicked.addListener(openReleasePage);
chrome.notifications.onClicked.addListener(openReleasePage);
chrome.notifications.onClosed.addListener(dismissUpdate);
chrome.storage.onChanged.addListener(whenUpdateSettingsChange);
