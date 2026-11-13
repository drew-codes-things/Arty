const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const folder = path.join(__dirname, '..');

test('the manifest loads as a service worker in Chrome, Edge and Brave and as a background script in Firefox', () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(folder, 'manifest.json'), 'utf8'));
  assert.strictEqual(manifest.background.service_worker, 'background.js');
  assert.deepStrictEqual(manifest.background.scripts, ['background.js']);
  assert.match(manifest.browser_specific_settings.gecko.id, /^[\w.-]+@[\w.-]+$/);
});
