import { CONVERSATIONS_KEY } from "./conversations.js";

const SETTINGS_KEY = "arty.connection";
const PREFERENCES_KEY = "arty.preferences";

export function isComplete(settings) {
  if (!settings || !settings.apiKey?.trim()) return false;
  return Boolean(settings.accessId?.trim()) === Boolean(settings.accessSecret?.trim());
}

export function cleanSettings({ apiKey = "", accessId = "", accessSecret = "" }) {
  return { apiKey: apiKey.trim(), accessId: accessId.trim(), accessSecret: accessSecret.trim() };
}

function readJson(storage, key) {
  try {
    return JSON.parse(storage.getItem(key) ?? "null");
  } catch {
    return null;
  }
}

export function loadSettings(storage) {
  const saved = readJson(storage, SETTINGS_KEY);
  return isComplete(saved) ? cleanSettings(saved) : null;
}

export function saveSettings(storage, settings) {
  const cleaned = cleanSettings(settings);
  if (!isComplete(cleaned)) throw new Error("The app key is needed, and the Access id and secret go together.");
  storage.setItem(SETTINGS_KEY, JSON.stringify(cleaned));
  return cleaned;
}

export function forgetSettings(storage) {
  storage.removeItem(SETTINGS_KEY);
}

export function forgetEverything(storage) {
  for (const key of [SETTINGS_KEY, PREFERENCES_KEY, CONVERSATIONS_KEY]) storage.removeItem(key);
}

export function loadPreferences(storage) {
  return { readAloud: false, lastChat: null, lastSeenUpdate: 0, ...(readJson(storage, PREFERENCES_KEY) ?? {}) };
}

export function savePreferences(storage, changes) {
  const updated = { ...loadPreferences(storage), ...changes };
  storage.setItem(PREFERENCES_KEY, JSON.stringify(updated));
  return updated;
}
