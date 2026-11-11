'use strict';

function siteRow(site, onForget) {
  const row = document.createElement('div');
  row.className = 'site';
  const name = document.createElement('span');
  name.className = 'site-name';
  name.textContent = site;
  const forget = document.createElement('button');
  forget.type = 'button';
  forget.className = 'cancel';
  forget.textContent = 'Forget';
  forget.addEventListener('click', () => onForget(site));
  row.append(name, forget);
  return row;
}

function fillSiteList(list, sites, onForget) {
  if (!sites.length) {
    const empty = document.createElement('p');
    empty.className = 'connect-help';
    empty.textContent = 'No sites yet. Arty asks the first time it reads or uses a site in Chrome.';
    list.replaceChildren(empty);
    return;
  }
  list.replaceChildren(...sites.map((site) => siteRow(site, onForget)));
}
