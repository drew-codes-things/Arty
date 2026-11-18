'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const context = vm.createContext({});
vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'renderer', 'turnstatus.js'), 'utf8'), context);
const call = (name, ...args) => JSON.parse(JSON.stringify(context[name](...args)));

const CODE_REPLY = {
  role: 'assistant', content: 'Fixed.', arty: {
    model: 'qwen3:14b', role: 'code', auto: true, context_window: 24576, steps: 2,
    usage: { prompt_tokens: 1200, completion_tokens: 60, context_tokens: 1260, cached_tokens: 1100, tokens_per_second: 27.4 },
  },
};

test('a reply shows its model, and the role when Arty Auto chose it', () => {
  assert.equal(context.replyModelLabel(CODE_REPLY.arty), 'qwen3:14b · Arty Auto: code');
  assert.equal(context.replyModelLabel({ model: 'qwen3:14b', auto: false, role: null }), 'qwen3:14b');
  assert.equal(context.replyModelLabel(undefined), '');
});

test('the status counts turns and steps and reads the latest reply\'s numbers', () => {
  const messages = [
    { role: 'user', content: 'hi' }, { role: 'assistant', content: 'Hi', arty: { model: 'qwen3:14b', steps: 1 } },
    { role: 'user', content: 'fix it' }, CODE_REPLY, { role: 'user', content: 'thanks' },
  ];
  const stats = call('chatStats', messages);
  assert.deepEqual(stats, { turns: 3, steps: 3, tokensPerSecond: 27.4, tokens: 1260, cacheHit: 91, contextUsed: 5 });
  assert.equal(context.statusLine(stats), '3 turns 3 steps · 27 tok/s · 1.3K tok · Cache hit 91%');
});

test('unknown numbers are hidden rather than shown as zero', () => {
  assert.deepEqual(call('chatStats', [{ role: 'user', content: 'hi' }, { role: 'assistant', content: 'Hi' }]), { turns: 1 });
  assert.equal(context.statusLine({ turns: 1 }), '1 turn');
});

test('counts shorten and 100 percent only shows when it is whole', () => {
  assert.deepEqual([999, 1260, 108000, 1500000].map(context.shortCount), ['999', '1.3K', '108K', '1.5M']);
  assert.equal(context.wholePercent(9999, 10000), 99);
  assert.equal(context.wholePercent(1, 0), null);
  assert.deepEqual([10, 75, 95].map(context.contextLevel), ['ok', 'warn', 'full']);
});

test('memory search matches names, types and facts and a new fact is about the busiest person', () => {
  const entities = [
    { name: 'Ella', entityType: 'person', observations: ["Rowan's sister"] },
    { name: 'Rowan', entityType: 'person', observations: ['Lives in Bristol', 'Plays five-a-side'] },
    { name: 'Garden', entityType: 'topic', observations: ['Has a pond'] },
  ];
  assert.deepEqual(call('matchingEntities', entities, 'BRISTOL').map((entity) => entity.name), ['Rowan']);
  assert.equal(call('matchingEntities', entities, ' ').length, 3);
  assert.equal(context.likelyOwner(entities), 'Rowan');
  assert.equal(context.likelyOwner([]), '');
});
