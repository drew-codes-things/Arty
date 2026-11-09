const test = require('node:test');
const assert = require('node:assert');
const path = require('path');
const platform = require('../platform');

test('the bundled Python is where each system keeps it', () => {
  assert.strictEqual(platform.pythonExecutable('/r', true, 'linux'), path.join('/r', 'python', 'bin', 'python3'));
  assert.strictEqual(platform.pythonExecutable('/r', true, 'win32'), path.join('/r', 'python', 'python.exe'));
});

test('a development run uses the virtual environment of its system', () => {
  assert.strictEqual(platform.pythonExecutable('/r', false, 'linux'), path.join('/r', '.venv', 'bin', 'python'));
  assert.strictEqual(platform.pythonExecutable('/r', false, 'win32'), path.join('/r', '.venv', 'Scripts', 'python.exe'));
});

test('each system has its own requirements file and setup command', () => {
  assert.strictEqual(platform.requirementsName('linux'), 'requirements-linux.txt');
  assert.strictEqual(platform.requirementsName('win32'), 'requirements-windows.txt');
  assert.strictEqual(platform.setupCommand('linux'), './setup-linux.sh');
  assert.strictEqual(platform.setupCommand('win32'), 'setup-windows.ps1');
});

test('only Linux starts with the PC through an autostart file', () => {
  assert.ok(!platform.usesLoginItems('linux'));
  assert.ok(platform.usesLoginItems('win32'));
  assert.ok(platform.usesLoginItems('darwin'));
});
