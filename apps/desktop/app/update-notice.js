'use strict';

const RELEASE_API_URL = 'https://api.github.com/repos/drew-codes-things/Arty/releases/latest';
const RELEASE_PAGE_URL = 'https://github.com/drew-codes-things/Arty/releases/latest';
const FIRST_CHECK_DELAY_MS = 60 * 1000;
const CHECK_EVERY_MS = 24 * 60 * 60 * 1000;
const REQUEST_TIMEOUT_MS = 5000;
const MAX_RELEASE_BYTES = 256 * 1024;
const SECURE_SCHEME = 'https:';
const VERSION_TEXT = /^(?:[a-z]+-)?v?(\d+(?:\.\d+){0,3})$/i;
const RELEASE_TAG = /^v\d/i;

function versionParts(text) {
  const found = VERSION_TEXT.exec(String(text ?? '').trim());
  return found ? found[1].split('.').map(Number) : null;
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

function releaseVersion(release) {
  const tag = release && typeof release === 'object' ? release.tag_name : null;
  const parts = typeof tag === 'string' && RELEASE_TAG.test(tag) ? versionParts(tag) : null;
  return parts ? parts.join('.') : null;
}

function noticeFor({ latest, current, dismissed, enabled }) {
  if (!enabled || !latest || latest === dismissed || !isNewer(latest, current)) return null;
  return { version: latest, text: `Version ${latest} is available` };
}

async function readCapped(response, maxBytes = MAX_RELEASE_BYTES) {
  const declared = Number(response.headers?.get?.('content-length'));
  if (Number.isFinite(declared) && declared > maxBytes) throw new Error(`the release answer is ${declared} bytes, over ${maxBytes}`);
  const reader = response.body.getReader();
  const chunks = [];
  let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > maxBytes) {
      await reader.cancel();
      throw new Error(`the release answer is over ${maxBytes} bytes`);
    }
    chunks.push(Buffer.from(value));
  }
  return Buffer.concat(chunks).toString('utf8');
}

async function fetchLatest({ fetch, current, url = RELEASE_API_URL }) {
  if (new URL(url).protocol !== SECURE_SCHEME) throw new Error(`refusing ${url}: only https is used`);
  const response = await fetch(url, {
    method: 'GET',
    redirect: 'error',
    credentials: 'omit',
    cache: 'no-store',
    headers: { 'User-Agent': `Arty/${current}`, Accept: 'application/vnd.github+json' },
    signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
  });
  if (!response.ok) throw new Error(`the release answered ${response.status}`);
  let release;
  try {
    release = JSON.parse(await readCapped(response));
  } catch (error) {
    if (error instanceof SyntaxError) throw new Error('the release answer is not valid JSON');
    throw error;
  }
  const latest = releaseVersion(release);
  if (!latest) throw new Error('the latest release has no v tag');
  return latest;
}

function createUpdateChecker({ fetch, current, settings, show, log, timers = { setTimeout, clearTimeout } }) {
  let latest = null;
  let timer = null;

  const refresh = () => show(noticeFor({ latest, current, ...settings() }));

  async function check() {
    if (!settings().enabled) return;
    try {
      latest = await fetchLatest({ fetch, current });
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
    timer?.unref?.();
  }

  return {
    start: () => schedule(FIRST_CHECK_DELAY_MS),
    stop: () => timers.clearTimeout(timer),
    check,
    refresh,
  };
}

module.exports = {
  RELEASE_API_URL, RELEASE_PAGE_URL, FIRST_CHECK_DELAY_MS, CHECK_EVERY_MS, MAX_RELEASE_BYTES, REQUEST_TIMEOUT_MS,
  versionParts, isNewer, releaseVersion, noticeFor, readCapped, fetchLatest, createUpdateChecker,
};
