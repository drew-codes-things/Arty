'use strict';

const PAIRING_CODE = /^[0-9a-f]{32}$/;
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

showCurrent();
