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

const SERVED = [
  { id: 'Arty', name: 'Arty', model: 'big:35b', description: 'Qwen3.6 35B', private: false, group: 'arty' },
  { id: 'auto', name: 'Arty Auto', model: '', description: 'Picks the model for each message', private: false, group: 'arty' },
  { id: 'arty/big:35b', name: 'Qwen3.6 35B', model: 'big:35b', description: '', private: false, group: 'choose' },
  { id: 'arty/side-model', name: 'Side model', model: 'side-model', description: '', private: true, group: 'choose' },
  { id: 'arty/qwen3:14b', name: 'qwen3:14b', model: 'qwen3:14b', description: '', private: false, group: 'choose' },
  { id: 'arty/remote/remote-a/remote-model-a', name: 'Remote model A', model: 'remote/remote-a/remote-model-a', description: '', private: false, group: 'remote' },
];
const names = (models) => models.map((model) => model.name);

test('the picker has an Arty tab and a Choose a model tab, each with the Remote models underneath', () => {
  const { tabs, remote, remoteName } = call('pickerSections', SERVED);
  assert.deepEqual(tabs.map((tab) => tab.name), ['Arty', 'Choose a model']);
  assert.deepEqual(names(tabs[0].models), ['Arty', 'Arty Auto']);
  assert.deepEqual(names(tabs[1].models), ['Qwen3.6 35B', 'Side model', 'qwen3:14b']);
  assert.equal(remoteName, 'Remote');
  assert.deepEqual(remote.map((model) => model.id), ['arty/remote/remote-a/remote-model-a']);
});

test('the picker opens on the tab that holds the current model, and on Arty for Arty or a remote model', () => {
  assert.equal(context.pickerTabFor(SERVED, 'Arty'), 0);
  assert.equal(context.pickerTabFor(SERVED, 'arty/qwen3:14b'), 1);
  assert.equal(context.pickerTabFor(SERVED, 'arty/remote/remote-a/remote-model-a'), 0);
  assert.equal(context.pickerTabFor(SERVED, 'gone:1b'), 0);
});

test('a key with no Arty entry opens the picker on Choose a model', () => {
  const onlyRemote = SERVED.filter((model) => model.group === 'remote');
  assert.equal(context.pickerTabFor(onlyRemote, 'arty/remote/remote-a/remote-model-a'), 1);
  const drawn = call('drawnPickerTabs', call('pickerSections', onlyRemote).tabs);
  assert.deepEqual(drawn.map((tab) => [tab.index, tab.name]), [[1, 'Choose a model']]);
  assert.deepEqual(call('drawnPickerTabs', call('pickerSections', SERVED).tabs).map((tab) => tab.index), [0, 1]);
});

test('a key allowed only remote models gets an empty Choose a model tab and the Remote models in both', () => {
  const onlyRemote = SERVED.filter((model) => model.id === 'auto' || model.group === 'remote');
  const { tabs, remote } = call('pickerSections', onlyRemote);
  assert.deepEqual(tabs.map((tab) => names(tab.models)), [['Arty Auto'], []]);
  assert.equal(remote.length, 1);
  assert.equal(context.pickerEmptyNote(), 'No models here for this key.');
});

test('a server without groups puts everything after Arty Auto in Choose a model', () => {
  const { tabs, remote } = call('pickerSections', [{ id: 'auto', name: 'Arty Auto' }, { id: 'arty/qwen3:14b', name: 'qwen3:14b' }]);
  assert.deepEqual(tabs.map((tab) => names(tab.models)), [['Arty Auto'], ['qwen3:14b']]);
  assert.deepEqual(remote, []);
});

test('a reply shows the model label, never the raw engine id', () => {
  const big = 'hf.co/owner/Big-35B-A3B-GGUF:UD-IQ4_XS';
  const listed = [{ id: `arty/${big}`, name: 'Arty (big)', model: big, label: 'Big 35B', available: true }];
  assert.equal(context.replyModelLabel({ model: big }, listed), 'Big 35B');
  assert.equal(context.replyModelLabel({ model: big, auto: true, role: 'code' }, listed), 'Big 35B · Arty Auto: code');
  assert.equal(context.replyModelLabel({ model: big }), 'Big-35B-A3B');
});

test('only a staying switch named in a message moves the picker', () => {
  assert.equal(context.movesThePicker({ model: 'qwen3:14b', persist: true, pick: 'arty/qwen3:14b' }), true);
  assert.equal(context.movesThePicker({ model: 'qwen3:14b', auto: false }), false);
  assert.equal(context.movesThePicker({ persist: true }), false);
  assert.equal(context.movesThePicker(undefined), false);
});

test('the window reloads the picker when a reply moved it', () => {
  const app = fs.readFileSync(path.join(__dirname, '..', 'renderer', 'app.js'), 'utf8');
  assert.match(app, /if \(movesThePicker\(event\.arty\)\) loadModels\(\);/);
});
