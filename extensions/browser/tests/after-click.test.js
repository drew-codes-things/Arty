const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'background.js'), 'utf8');
const VIDEO_TAB = [{ id: 7, active: true, title: 'lofi radio - Videos', url: 'https://video.example/watch?v=1', windowId: 1 }];
const LONGEST_WAIT_THE_TEST_SITS_THROUGH_MS = 2000;
const STOPPED_VIDEO = { kind: 'video', playing: false, muted: false, at: 0, length: 30 };

function loadExtension(inject) {
  const done = [];
  const chrome = {
    alarms: { create: () => {}, onAlarm: { addListener: () => {} } },
    runtime: { getPlatformInfo: async () => ({}), onStartup: { addListener: () => {} }, onInstalled: { addListener: () => {} } },
    storage: { local: { get: async () => ({}) } },
    action: { setBadgeText: async () => {}, setTitle: async () => {} },
    tabs: { get: async (id) => VIDEO_TAB.find((tab) => tab.id === id), query: async () => VIDEO_TAB, onUpdated: { addListener: () => {}, removeListener: () => {} } },
    windows: { update: async () => {} },
    scripting: { executeScript: async ({ func, args }) => { done.push(func.name); return inject(func.name, args); } },
  };
  const background = (callback, ms) => {
    const timer = setTimeout(callback, Math.min(ms, 5));
    if (ms > LONGEST_WAIT_THE_TEST_SITS_THROUGH_MS) timer.unref();
    return timer;
  };
  const context = { chrome, fetch: () => new Promise(() => {}), setTimeout: background, clearTimeout, console, URL, AbortSignal };
  vm.createContext(context);
  vm.runInContext(source, context);
  return { context, done };
}

function runInPage(name, page, args) {
  const start = source.indexOf(`function ${name}(`);
  const end = source.indexOf('\n}\n', start) + 3;
  const context = { ...page };
  vm.createContext(context);
  vm.runInContext(`${source.slice(start, end)}\nresult = ${name}(...${JSON.stringify(args)});`, context);
  return JSON.parse(JSON.stringify(context.result));
}

test('a click says whether a video or sound is playing on the page it led to', async () => {
  const { context, done } = loadExtension((name) => [{ result: name === 'clickItem' ? { label: 'lofi radio' } : [STOPPED_VIDEO] }]);
  const result = await context.run({ action: 'click', item: 59, site: 'video.example' });
  assert.deepStrictEqual(done, ['clickItem', 'playersOnPage']);
  assert.deepStrictEqual(JSON.parse(JSON.stringify(result.media)), [STOPPED_VIDEO]);
});

test('a numbered item that is no longer shown is not clicked, so a stale number cannot repeat an old click', () => {
  let clicked = false;
  const hidden = {
    getBoundingClientRect: () => ({ width: 0, height: 0 }), scrollIntoView: () => {}, click: () => { clicked = true; },
    getAttribute: () => null, innerText: 'lofi radio',
  };
  const page = { document: { querySelector: () => hidden, querySelectorAll: () => [] }, getComputedStyle: () => ({ visibility: 'visible', display: 'block' }) };
  const result = runInPage('clickItem', page, [59, '']);
  assert.match(result.error, /item 59 is not on the page any more/);
  assert.strictEqual(clicked, false);
});

test('players on a page say whether each is playing', () => {
  const video = { tagName: 'VIDEO', paused: false, ended: false, readyState: 4, muted: true, currentTime: 83.4, duration: 180.2 };
  const page = { document: { querySelectorAll: (selector) => (selector === 'video, audio' ? [video] : []) } };
  assert.deepStrictEqual(runInPage('playersOnPage', page, []), [{ kind: 'video', playing: true, starting: false, muted: true, at: 83, length: 180 }]);
});

test('each read numbers its items after the last read, so a number from an older read never matches a new item', async () => {
  const numbered = (first) => ({ title: 'Results', url: 'https://video.example/watch?v=1', text: '', items: [1, 2, 3].map((k) => ({ n: first + k - 1 })) });
  const firsts = [];
  const { context } = loadExtension((name, args) => {
    if (name !== 'readPage') return [{ result: name === 'playersOnPage' ? [] : true }];
    firsts.push(args[1]);
    return [{ result: numbered(args[1]) }];
  });
  await context.run({ action: 'read' });
  await context.run({ action: 'read' });
  assert.strictEqual(firsts.length, 2);
  assert.ok(firsts[1] > firsts[0] + 2, `second read starts at ${firsts[1]}`);
});

test('a video that has been told to play but is still loading counts as starting, not stopped', () => {
  const video = { tagName: 'VIDEO', paused: false, ended: false, readyState: 1, muted: false, currentTime: 0, duration: 600 };
  const page = { document: { querySelectorAll: (selector) => (selector === 'video, audio' ? [video] : []) } };
  const [player] = runInPage('playersOnPage', page, []);
  assert.strictEqual(player.playing, false);
  assert.strictEqual(player.starting, true);
});
