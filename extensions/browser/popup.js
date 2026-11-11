'use strict';

const PAIRING_CODE = /^[0-9a-f]{32}$/;
const form = document.getElementById('pair');
const field = document.getElementById('code');
const status = document.getElementById('status');

async function showCurrent() {
  const stored = await chrome.storage.local.get('pairing');
  status.textContent = stored.pairing ? 'Paired. Paste a new code to change it.' : 'Not paired yet.';
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const pairing = field.value.trim().toLowerCase();
  if (!PAIRING_CODE.test(pairing)) {
    status.textContent = 'That is not a pairing code: it is 32 letters and numbers.';
    return;
  }
  await chrome.storage.local.set({ pairing });
  field.value = '';
  status.textContent = 'Saved. Arty Desktop can use this browser now.';
});

showCurrent();
