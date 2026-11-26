'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const notice = require('../update-notice');

const appFolder = path.join(__dirname, '..');
const read = (name) => fs.readFileSync(path.join(appFolder, name), 'utf8');

function answer(body, { status = 200, headers = {} } = {}) {
  const bytes = Buffer.from(typeof body === 'string' ? body : JSON.stringify(body));
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (name) => headers[name.toLowerCase()] ?? null },
    body: new Blob([bytes]).stream(),
  };
}

const release = (tag) => ({ tag_name: tag, name: `Arty ${tag}`, assets: [] });

test('versions compare as numbers and tolerate a v or a tag prefix', () => {
  assert.ok(notice.isNewer('1.10.0', '1.9.9'));
  assert.ok(notice.isNewer('v1.7.0', '1.6.1'));
  assert.ok(notice.isNewer('desktop-v1.7', '1.6.1'));
  assert.ok(notice.isNewer('1.7.0.1', '1.7.0'));
  assert.ok(!notice.isNewer('1.7.0', '1.7'));
  assert.ok(!notice.isNewer('1.6.1', '1.7.0'));
  assert.ok(!notice.isNewer('soon', '1.0.0'));
  assert.ok(!notice.isNewer('1.8.0', undefined));
});

test('the notice shows a newer version once and stays away after it is dismissed', () => {
  const shown = notice.noticeFor({ latest: '1.8.0', current: '1.7.0', dismissed: '', enabled: true });
  assert.deepEqual(shown, { version: '1.8.0', text: 'Version 1.8.0 is available' });
  assert.equal(notice.noticeFor({ latest: '1.8.0', current: '1.7.0', dismissed: '1.8.0', enabled: true }), null);
  assert.ok(notice.noticeFor({ latest: '1.9.0', current: '1.7.0', dismissed: '1.8.0', enabled: true }));
  assert.equal(notice.noticeFor({ latest: '1.8.0', current: '1.7.0', dismissed: '', enabled: false }), null);
  assert.equal(notice.noticeFor({ latest: '1.7.0', current: '1.7.0', dismissed: '', enabled: true }), null);
  assert.equal(notice.noticeFor({ latest: null, current: '1.7.0', dismissed: '', enabled: true }), null);
});

test('the latest GitHub release is read defensively and only a v tag counts', () => {
  assert.equal(notice.releaseVersion(release('v1.8.0')), '1.8.0');
  assert.equal(notice.releaseVersion(release('V1.8')), '1.8');
  for (const bad of [null, 'text', [], {}, release('1.8.0'), release('desktop-v1.8.0'), release(18), release('v1.8.0<script>'), { tag_name: null }]) {
    assert.equal(notice.releaseVersion(bad), null);
  }
});

test('the app asks GitHub, never a private server', () => {
  assert.equal(notice.RELEASE_API_URL, 'https://api.github.com/repos/drew-codes-things/Arty/releases/latest');
  assert.equal(notice.RELEASE_PAGE_URL, 'https://github.com/drew-codes-things/Arty/releases/latest');
  const hosts = [...read('update-notice.js').matchAll(/https:\/\/([a-z0-9.-]+)/gi)].map((found) => found[1]);
  assert.deepEqual([...new Set(hosts)].sort(), ['api.github.com', 'github.com']);
});

test('one https request with a five second limit, no cookies, no redirects and only the app name as user agent', async () => {
  const calls = [];
  const fetch = async (url, options) => { calls.push({ url, options }); return answer(release('v1.8.0')); };
  assert.equal(await notice.fetchLatest({ fetch, current: '1.7.0' }), '1.8.0');
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, notice.RELEASE_API_URL);
  assert.equal(calls[0].options.credentials, 'omit');
  assert.equal(calls[0].options.redirect, 'error');
  assert.deepEqual(calls[0].options.headers, { 'User-Agent': 'Arty/1.7.0', Accept: 'application/vnd.github+json' });
  assert.ok(calls[0].options.signal instanceof AbortSignal);
  assert.equal(notice.REQUEST_TIMEOUT_MS, 5000);
});

test('a plain http address is refused before any request', async () => {
  let asked = false;
  const fetch = async () => { asked = true; return answer(release('v9.0.0')); };
  await assert.rejects(notice.fetchLatest({ fetch, current: '1.7.0', url: 'http://api.github.com/repos/drew-codes-things/Arty/releases/latest' }), /only https/);
  assert.equal(asked, false);
});

test('an answer over 256 KB, a failed status (such as the 403 of a rate limit) or bad JSON gives no version', async () => {
  const huge = JSON.stringify({ ...release('v9.0.0'), body: 'x'.repeat(notice.MAX_RELEASE_BYTES) });
  await assert.rejects(notice.fetchLatest({ fetch: async () => answer(huge), current: '1.7.0' }), /over 262144 bytes/);
  await assert.rejects(notice.fetchLatest({ fetch: async () => answer('{}', { headers: { 'content-length': '999999' } }), current: '1.7.0' }), /over/);
  await assert.rejects(notice.fetchLatest({ fetch: async () => answer('', { status: 403 }), current: '1.7.0' }), /403/);
  await assert.rejects(notice.fetchLatest({ fetch: async () => answer('{"latest":'), current: '1.7.0' }), /not valid JSON/);
  await assert.rejects(notice.fetchLatest({ fetch: async () => answer({ tag_name: 'nightly' }), current: '1.7.0' }), /no v tag/);
});

function fakeTimers() {
  const waiting = [];
  return {
    waiting,
    timers: { setTimeout: (callback, delay) => { waiting.push({ callback, delay }); return waiting.length; }, clearTimeout: () => {} },
    async runNext() { await waiting.shift().callback(); },
  };
}

test('the first check comes a minute after start and then once a day', async () => {
  const clock = fakeTimers();
  let requests = 0;
  const shown = [];
  const checker = notice.createUpdateChecker({
    fetch: async () => { requests += 1; return answer(release('v1.8.0')); },
    current: '1.7.0',
    settings: () => ({ enabled: true, dismissed: '' }),
    show: (value) => shown.push(value),
    log: () => {},
    timers: clock.timers,
  });
  checker.start();
  assert.equal(requests, 0);
  assert.equal(clock.waiting[0].delay, notice.FIRST_CHECK_DELAY_MS);
  await clock.runNext();
  assert.equal(requests, 1);
  assert.equal(clock.waiting[0].delay, 24 * 60 * 60 * 1000);
  assert.deepEqual(shown, [{ version: '1.8.0', text: 'Version 1.8.0 is available' }]);
});

test('a failed check logs one line and shows nothing; a switched off check sends nothing', async () => {
  const lines = [];
  const shown = [];
  let enabled = true;
  let requests = 0;
  const checker = notice.createUpdateChecker({
    fetch: async () => { requests += 1; throw new TypeError('fetch failed'); },
    current: '1.7.0',
    settings: () => ({ enabled, dismissed: '' }),
    show: (value) => shown.push(value),
    log: (line) => lines.push(line),
    timers: fakeTimers().timers,
  });
  await checker.check();
  assert.deepEqual(lines, ['Arty: update check skipped (fetch failed)']);
  assert.deepEqual(shown, []);
  enabled = false;
  await checker.check();
  assert.equal(requests, 1);
});

test('the window wires the notice, its dismiss button and the setting in the tray', () => {
  const main = read('main.js');
  const preload = read('preload.js');
  const page = read('renderer/index.html');
  assert.match(main, /require\('\.\/update-notice'\)/);
  assert.match(main, /Tell me about new versions/);
  assert.match(main, /shell\.openExternal\(updateNotice\.RELEASE_PAGE_URL\)/);
  assert.match(preload, /onUpdateNotice/);
  assert.match(preload, /dismissUpdate/);
  assert.match(preload, /openReleasePage/);
  assert.match(page, /id="update-notice"[^>]*hidden/);
  assert.ok(JSON.parse(read('package.json')).build.files.includes('update-notice.js'));
});

test('this release is Arty Desktop 1.7.0', () => {
  assert.equal(JSON.parse(read('package.json')).version, '1.7.0');
});

test('the notice wording has no em dash', () => {
  assert.ok(!read('update-notice.js').includes(String.fromCharCode(0x2014)));
});
