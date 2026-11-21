'use strict';

const REMOVE_CONFIRM_MILLISECONDS = 5000;
const MILLISECONDS_PER_SECOND_FOR_DATES = 1000;

function phoneLinkedOn(phone) {
  if (!phone.created) return '';
  return new Date(phone.created * MILLISECONDS_PER_SECOND_FOR_DATES).toLocaleDateString();
}

function phoneDetails(phone) {
  const linked = phoneLinkedOn(phone);
  const used = phone.last_used ? 'used recently' : 'not used yet';
  return linked ? `linked ${linked}, ${used}` : used;
}

function phoneRow(phone, onRemove) {
  const row = document.createElement('div');
  row.className = 'site';
  const name = document.createElement('span');
  name.className = 'site-name';
  name.textContent = `${phone.name} (${phoneDetails(phone)})`;
  const remove = document.createElement('button');
  remove.type = 'button';
  remove.className = 'cancel';
  remove.textContent = 'Remove';
  let armedUntil = 0;
  remove.addEventListener('click', () => {
    if (Date.now() > armedUntil) {
      armedUntil = Date.now() + REMOVE_CONFIRM_MILLISECONDS;
      remove.textContent = 'Tap again to remove';
      return;
    }
    remove.disabled = true;
    onRemove(phone);
  });
  row.append(name, remove);
  return row;
}

function fillPhoneList(list, phones, onRemove) {
  if (!phones.length) {
    const empty = document.createElement('p');
    empty.className = 'connect-help';
    empty.textContent = 'No phones linked yet.';
    list.replaceChildren(empty);
    return;
  }
  list.replaceChildren(...phones.map((phone) => phoneRow(phone, onRemove)));
}

function phoneLinkLine(state) {
  if (state.state === 'linked') return `Linked ${state.name}. The code is used up, so you can close this.`;
  if (state.state === 'gone') return 'This code has run out. Press New code to make another.';
  return '';
}
