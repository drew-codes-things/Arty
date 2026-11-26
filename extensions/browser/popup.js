'use strict';

const PAIRING_CODE = /^[0-9a-f]{32}$/;
const RELEASE_PAGE_URL = 'https://github.com/drew-codes-things/Arty/releases/latest';
const form = document.getElementById('pair');
const field = document.getElementById('code');
const status = document.getElementById('status');

async function showCurrent() {
  const stored = await chrome.storage.local.get(['pairing', 'pairedBy']);
  if (!stored.pairing) {
    status.textContent = 'Not paired yet. With Arty Desktop open, this browser pairs by itself, or paste the code.';
    return;
  }
  status.textContent = stored.pairedBy === 'native'
    ? 'Paired automatically with Arty Desktop. Paste a code to change it.'
    : 'Paired. Paste a new code to change it.';
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const pairing = field.value.trim().toLowerCase();
  if (!PAIRING_CODE.test(pairing)) {
    status.textContent = 'That is not a pairing code: it is 32 letters and numbers.';
    return;
  }
  await chrome.storage.local.set({ pairing, pairedBy: 'code' });
  field.value = '';
  status.textContent = 'Saved. Arty Desktop can use this browser now.';
});

const updateNotice = document.getElementById('update-notice');
const updateCheck = document.getElementById('update-check');
let shownUpdate = null;

async function showUpdate() {
  const stored = await chrome.storage.local.get(['updateNotice', 'updateCheck']);
  updateCheck.checked = stored.updateCheck !== false;
  shownUpdate = stored.updateCheck !== false && stored.updateNotice && typeof stored.updateNotice.version === 'string' ? stored.updateNotice : null;
  document.getElementById('update-text').textContent = shownUpdate ? shownUpdate.text : '';
  updateNotice.hidden = !shownUpdate;
}

document.getElementById('update-open').addEventListener('click', () => chrome.tabs.create({ url: RELEASE_PAGE_URL }));
document.getElementById('update-dismiss').addEventListener('click', async () => {
  if (shownUpdate) await chrome.storage.local.set({ dismissedUpdate: shownUpdate.version, updateNotice: null });
  updateNotice.hidden = true;
});
updateCheck.addEventListener('change', async () => {
  await chrome.storage.local.set({ updateCheck: updateCheck.checked });
  if (!updateCheck.checked) updateNotice.hidden = true;
});

showCurrent();
showUpdate();
