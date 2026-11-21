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

function browserState(browser) {
  const packaging = browser.packaging === 'native' ? '' : ` (${browser.packaging})`;
  const profiles = browser.profiles > 1 ? ` (${browser.profiles} profiles)` : '';
  const name = `${browser.name}${packaging}${profiles}`;
  if (browser.connected) return `${name}: connected.`;
  if (browser.native === 'on') return `${name}: connects by itself when it is open.`;
  if (browser.native === 'unavailable') return `${name}: can't connect by itself, so paste the pairing code below into its Arty extension.`;
  return `${name}: not connected. Press Connect.`;
}

function canConnectByItself(browser) {
  return !browser.connected && browser.native !== 'unavailable';
}

function browserRow(browser, onConnect) {
  const row = document.createElement('div');
  row.className = 'site';
  const name = document.createElement('span');
  name.className = 'site-name';
  name.textContent = browserState(browser);
  row.append(name);
  if (onConnect && canConnectByItself(browser)) {
    const connect = document.createElement('button');
    connect.type = 'button';
    connect.className = 'add';
    connect.textContent = 'Connect';
    connect.addEventListener('click', () => onConnect(browser.name));
    row.append(connect);
  }
  return row;
}

function fillBrowserList(list, browsers, onConnect) {
  if (!browsers.length) {
    const empty = document.createElement('p');
    empty.className = 'connect-help';
    empty.textContent = 'No browser has the Arty extension yet. Install it from the Arty release page, then open this again.';
    list.replaceChildren(empty);
    return;
  }
  list.replaceChildren(...browsers.map((browser) => browserRow(browser, onConnect)));
}

function linkStateLine(state) {
  if (state.linked && state.automatic) return `Linked with ${state.name}. It linked by itself (same user on this computer).`;
  if (state.linked) return `Linked with ${state.name}.`;
  if (state.links_by_itself) return 'Not linked yet. Start starfire Desktop on this computer and it links by itself, or make a code.';
  return 'Not linked yet.';
}

function linkAutomaticLabel(automatic) {
  return automatic ? 'Stop linking by itself' : 'Link by itself';
}

function automaticButtonLabel(automatic) {
  return automatic ? 'Stop connecting browsers by themselves' : 'Connect browsers by themselves';
}
