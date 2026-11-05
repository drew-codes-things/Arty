'use strict';

const messages = {
  wrong: 'Wrong email or password.',
  locked: 'Too many attempts - try again in 15 minutes.',
};
const reason = new URLSearchParams(window.location.search).get('error');
if (messages[reason]) {
  const box = document.getElementById('error');
  box.textContent = messages[reason];
  box.hidden = false;
}
