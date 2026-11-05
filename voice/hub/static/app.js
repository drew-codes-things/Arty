'use strict';

const LABELS = { debian: 'Debian', windows: 'Windows' };
const $ = id => document.getElementById(id);

function detectSystem() {
  const platform = (navigator.userAgentData && navigator.userAgentData.platform) || navigator.userAgent;
  return /win/i.test(platform) ? 'windows' : 'debian';
}

function chooseSystem(system) {
  const other = system === 'windows' ? 'debian' : 'windows';
  $('system').value = system;
  $('downloadBtn').textContent = `Download for ${LABELS[system]}`;
  $('otherLink').textContent = `Download for ${LABELS[other]} instead`;
  $('otherLink').onclick = event => { event.preventDefault(); chooseSystem(other); };
  $(`${system}Steps`).open = true;
  $(`${other}Steps`).open = false;
}

function timeAgo(seconds) {
  if (!seconds) return 'never connected';
  const minutes = Math.round((Date.now() / 1000 - seconds) / 60);
  if (minutes < 2) return 'online now';
  if (minutes < 60) return `seen ${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  return hours < 48 ? `seen ${hours} h ago` : `seen ${Math.round(hours / 24)} days ago`;
}

async function revokeDevice(device) {
  if (!window.confirm(`Remove ${device.name}? It stops working straight away.`)) return;
  const response = await fetch(`/devices/${encodeURIComponent(device.id)}/revoke`, { method: 'POST' });
  if (!response.ok) window.alert(`Couldn't remove it: ${await response.text()}`);
  loadStatus();
}

function renderDevices(devices) {
  const list = $('devices');
  list.replaceChildren();
  for (const device of devices) {
    const item = document.createElement('li');
    const label = document.createElement('span');
    const dot = document.createElement('span');
    const seen = timeAgo(device.last_seen);
    dot.className = 'dot' + (seen === 'online now' ? ' on' : '');
    label.append(dot, `${device.name} · ${device.system} · ${seen}`);
    item.append(label);
    if (device.role === 'pc') {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = 'Remove';
      button.onclick = () => revokeDevice(device);
      item.append(button);
    }
    list.append(item);
  }
}

async function loadStatus() {
  const response = await fetch('/api/status');
  if (response.status === 401 || response.redirected) { window.location.href = '/login'; return; }
  if (!response.ok) throw new Error(`hub said ${response.status}`);
  const status = await response.json();
  const mic = status.mic_error ? `mic problem: ${status.mic_error}` :
    `mic in use by ${status.mic_listeners} listener(s)`;
  $('status').textContent = `${mic} · ${status.speaker_listeners} device(s) playing Arty's voice`;
  renderDevices(status.devices);
  showWake(status.wake_enabled);
}

function showWake(enabled) {
  $('wakeToggle').checked = enabled;
  $('wakeToggle').disabled = false;
  $('wakeNote').textContent = enabled ? 'On: Arty answers to "Hey Arty".' : 'Off: Arty ignores "Hey Arty" until you say "Arty on".';
}

async function setWake(enabled) {
  $('wakeToggle').disabled = true;
  try {
    const response = await fetch('/api/wake', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled }),
    });
    if (!response.ok) throw new Error(await response.text());
    showWake((await response.json()).wake_enabled);
  } catch (error) {
    $('wakeNote').textContent = `Couldn't switch it: ${error.message}`;
    $('wakeToggle').checked = !enabled;
    $('wakeToggle').disabled = false;
  }
}

$('wakeToggle').addEventListener('change', event => setWake(event.target.checked));
const detected = detectSystem();
$('detected').textContent = LABELS[detected];
chooseSystem(detected);
$('downloadForm').addEventListener('submit', () => {
  $('downloadBtn').disabled = true;
  setTimeout(() => { $('downloadBtn').disabled = false; loadStatus(); }, 2500);
});
loadStatus().catch(error => { $('status').textContent = `Couldn't load the status: ${error.message}`; });
setInterval(() => loadStatus().catch(() => {}), 15000);
