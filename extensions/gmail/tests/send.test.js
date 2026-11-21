const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'background.js'), 'utf8');
const APPROVED = { to: ['sam@example.org'], cc: [], subject: 'Late', body: 'Running late.\nSorry!' };
const COMPOSED = {
  ready: true, to: ['sam@example.org'], cc: [], bcc: [], chips: ['sam@example.org'], subject: 'Late',
  body: 'Running late.\n\nSorry!\n', sendButtons: 1,
};

function loadExtension({ shown = COMPOSED, states = [{ sent: true, asked: '', composeOpen: false }], url, rows = [] } = {}) {
  const done = { clicks: 0, opened: [], scripts: [] };
  let clock = 0;
  const tab = { id: 3, status: 'complete', url: url || 'https://mail.google.com/mail/u/0/?view=cm' };
  const answers = {
    pageCompose: () => shown,
    pageClickSend: () => { done.clicks += 1; return true; },
    pageSendState: () => states.shift() || { sent: false, asked: '', composeOpen: true },
    pageMarkStale: () => undefined,
    pageState: () => ({ ready: true }),
    pageRows: () => rows,
  };
  const chrome = {
    alarms: { create: () => {}, onAlarm: { addListener: () => {} } },
    runtime: { getPlatformInfo: () => {}, getURL: (name) => name, onStartup: { addListener: () => {} }, onInstalled: { addListener: () => {} } },
    storage: { session: { get: async () => ({ tabId: 3 }), set: async () => {} } },
    tabs: {
      get: async () => ({ ...tab }),
      query: async () => [tab],
      create: async () => tab,
      update: async (id, change) => { done.opened.push(change.url); if (!url) tab.url = change.url; return tab; },
      reload: async () => {},
    },
    scripting: { executeScript: async ({ func }) => { done.scripts.push(func.name); return [{ result: answers[func.name]() }]; } },
  };
  const context = {
    chrome, console, URL, URLSearchParams,
    fetch: () => new Promise(() => {}),
    setTimeout: (callback) => setTimeout(callback, 0),
    Date: { now: () => (clock += 400) },
  };
  vm.createContext(context);
  vm.runInContext(source, context);
  return { context, done };
}

test('a compose window holding exactly the approved email is sent once and confirmed', async () => {
  const { context, done } = loadExtension();
  const result = await context.run('gmail_send', APPROVED);
  assert.deepStrictEqual(JSON.parse(JSON.stringify(result)), { sent: true, confirmed: 'Gmail said the message was sent' });
  assert.strictEqual(done.clicks, 1);
  const filled = new URL(done.opened[0]);
  assert.strictEqual(filled.searchParams.get('view'), 'cm');
  assert.strictEqual(filled.searchParams.get('to'), 'sam@example.org');
  assert.strictEqual(filled.searchParams.get('su'), 'Late');
  assert.strictEqual(filled.searchParams.get('body'), 'Running late.\nSorry!');
});

for (const [why, changed] of [
  ['another address in To', { to: ['sam@example.org', 'collect@evil.example'] }],
  ['a Bcc nobody approved', { bcc: ['collect@evil.example'] }],
  ['a different subject', { subject: 'Late (fwd)' }],
  ['different text', { body: 'Running late. My password is hunter2.' }],
  ['no Send button', { sendButtons: 0 }],
  ['two Send buttons', { sendButtons: 2 }],
  ['a compose window that never opened', { ready: false }],
]) {
  test(`nothing is sent when the compose window shows ${why}`, async () => {
    const { context, done } = loadExtension({ shown: { ...COMPOSED, ...changed } });
    const result = await context.run('gmail_send', APPROVED);
    assert.match(result.error, /nothing was sent/);
    assert.strictEqual(done.clicks, 0);
  });
}

test('when Gmail shows no To fields the address chips must match exactly', async () => {
  const onlyChips = { ...COMPOSED, to: [], cc: [], chips: ['sam@example.org', 'drew@example.org'] };
  const { context, done } = loadExtension({ shown: onlyChips });
  assert.match((await context.run('gmail_send', APPROVED)).error, /nothing was sent/);
  assert.strictEqual(done.clicks, 0);
});

test('a question from Gmail after Send is reported, not answered', async () => {
  const states = [{ sent: false, asked: 'Send this message without a subject?', composeOpen: true }];
  const { context, done } = loadExtension({ states });
  const result = await context.run('gmail_send', APPROVED);
  assert.match(result.error, /stopped to ask/);
  assert.strictEqual(done.clicks, 1);
});

test('with no toast, the email is only called sent once it is found in Sent', async () => {
  const states = [{ sent: false, asked: '', composeOpen: false }];
  const found = loadExtension({ states: [...states], rows: [{ subject: 'Late' }] });
  assert.deepStrictEqual(JSON.parse(JSON.stringify(await found.context.run('gmail_send', APPROVED))),
    { sent: true, confirmed: 'found in Sent' });
  const missing = loadExtension({ states: [...states], rows: [] });
  assert.match((await missing.context.run('gmail_send', APPROVED)).error, /look in Sent before trying again/);
  assert.strictEqual(missing.done.clicks, 1);
});

test('no confirmation at all is never treated as sent', async () => {
  const { context, done } = loadExtension({ states: [] });
  const result = await context.run('gmail_send', APPROVED);
  assert.match(result.error, /didn't confirm/);
  assert.strictEqual(done.clicks, 1);
});

test('a signed-out Gmail window sends nothing and asks for a sign-in', async () => {
  const { context, done } = loadExtension({ url: 'https://accounts.google.com/signin' });
  const result = await context.run('gmail_send', APPROVED);
  assert.ok(result.blocked);
  assert.strictEqual(done.clicks, 0);
});

test('an email with no address is refused before Gmail is opened', async () => {
  const { context, done } = loadExtension();
  assert.match((await context.run('gmail_send', { ...APPROVED, to: [] })).error, /nothing was sent/);
  assert.deepStrictEqual(done.opened, []);
});

test('only whitespace differences, such as Gmail turning line breaks into blocks, still match', () => {
  const { context } = loadExtension();
  assert.strictEqual(context.composeMismatch(APPROVED, { ...COMPOSED, body: 'Running  late.\r\n\r\nSorry!' }), '');
  assert.notStrictEqual(context.composeMismatch(APPROVED, { ...COMPOSED, body: 'Running late. Sorry' }), '');
});
