'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const context = vm.createContext({ Intl, Date, Number, Math });
vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'renderer', 'messagestamp.js'), 'utf8'), context);
const stampLabel = (milliseconds, now) => context.stampLabel(milliseconds, now);

const NOW = new Date(2026, 10, 20, 16, 30);
const at = (year, month, day, hour, minute) => new Date(year, month - 1, day, hour, minute).getTime();

test('a message from today says today with its time', () => {
  assert.equal(stampLabel(at(2026, 11, 20, 16, 9), NOW), 'Today 16:09');
});

test('a message from yesterday says yesterday', () => {
  assert.equal(stampLabel(at(2026, 11, 19, 16, 9), NOW), 'Yesterday 16:09');
});

test('a message from three days ago names the weekday', () => {
  assert.equal(stampLabel(at(2026, 11, 17, 16, 9), NOW), 'Tue 16:09');
});

test('six days ago still names the weekday and seven days ago gives the date', () => {
  assert.equal(stampLabel(at(2026, 11, 14, 9, 5), NOW), 'Sat 09:05');
  assert.equal(stampLabel(at(2026, 11, 13, 9, 5), NOW), '13 Nov 09:05');
});

test('a message from ten days ago gives the date', () => {
  assert.equal(stampLabel(at(2026, 11, 10, 16, 9), NOW), '10 Nov 16:09');
});

test('a message from last year gives the year too', () => {
  assert.equal(stampLabel(at(2025, 11, 20, 16, 9), NOW), '20 Nov 2025 16:09');
});

test('a message with no stored time shows nothing', () => {
  assert.equal(stampLabel(undefined, NOW), '');
  assert.equal(stampLabel(null, NOW), '');
  assert.equal(stampLabel(0, NOW), '');
  assert.equal(stampLabel(Number.NaN, NOW), '');
});

test('just before midnight is yesterday and just after is today', () => {
  const justAfterMidnight = new Date(2026, 10, 20, 0, 1);
  assert.equal(stampLabel(at(2026, 11, 19, 23, 59), justAfterMidnight), 'Yesterday 23:59');
  assert.equal(stampLabel(at(2026, 11, 20, 0, 0), justAfterMidnight), 'Today 00:00');
});

test('the same message reads as yesterday once midnight has passed', () => {
  const message = at(2026, 11, 20, 23, 30);
  assert.equal(stampLabel(message, new Date(2026, 10, 20, 23, 59)), 'Today 23:30');
  assert.equal(stampLabel(message, new Date(2026, 10, 21, 0, 1)), 'Yesterday 23:30');
});

test('the new year counts as a different year', () => {
  assert.equal(stampLabel(at(2025, 12, 31, 22, 0), new Date(2026, 0, 15, 10, 0)), '31 Dec 2025 22:00');
});
