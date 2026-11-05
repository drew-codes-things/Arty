'use strict';

const problem = new URLSearchParams(window.location.search).get('error');
if (problem) {
  const box = document.getElementById('error');
  box.textContent = `Couldn't create the account: ${problem}.`;
  box.hidden = false;
}
