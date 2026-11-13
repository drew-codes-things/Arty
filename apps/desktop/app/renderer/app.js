'use strict';
// Arty Desktop's window. Everything goes through the local service on 127.0.0.1:8765
// (server/arty_server.py), which talks to Arty's brain and runs things on this computer.

const HEALTH_EVERY_MS = 15000;
const CALL_CHECK_TIMEOUT_MS = 5000;
const WAKE_TIMEOUT_S = 120;

const $ = (id) => document.getElementById(id);
const orb = $('orb');
const stateLabel = $('state');
const log = $('log');
const input = $('input');
const mic = $('mic');
const send = $('send');

// The stream in progress: a reply, the mic, or the wake word waiting for "Arty".
// kind is 'chat' | 'listen' | 'wake'; engaged turns true once a wake stream heard its name.
let current = null;
let wakeOn = readSetting('wake', false);
let readOn = readSetting('read', false);
let lastNotification = null;
let thinkingTimer = null;

const speech = { queue: [], playing: null, played: 0 };
const CALL_TURN_GAP_MS = 400;
const callState = { on: false, muted: false, startedAt: 0, timer: null };

function readSetting(key, fallback) {
  try {
    const value = localStorage.getItem(key);
    return value === null ? fallback : value === 'true';
  } catch { return fallback; }
}

function saveSetting(key, value) {
  try { localStorage.setItem(key, String(value)); } catch { /* private storage off: fine */ }
}

// ---------- the orb and status ----------

function setState(state, label) {
  orb.dataset.state = state;
  stateLabel.textContent = label;
  clearInterval(thinkingTimer);
  thinkingTimer = null;
  if (state === 'thinking') {
    const since = Date.now();
    thinkingTimer = setInterval(() => {
      stateLabel.textContent = `${label} ${Math.round((Date.now() - since) / 1000)}s`;
    }, 500);
  }
}

function settle() {
  mic.classList.remove('live');
  if (speech.playing || speech.queue.length) return;
  if (callState.on) {
    setState('idle', callState.muted ? 'On a call · muted' : 'On a call');
    queueCallTurn();
    return;
  }
  if (wakeOn) setState('waking', 'Say “Arty, …”');
  else setState('idle', 'Ready');
}

// ---------- the conversation ----------

function scrollDown() {
  log.scrollTop = log.scrollHeight;
}

function add(kind, text) {
  log.querySelector('.empty')?.remove();
  const element = document.createElement('div');
  element.className = `msg ${kind}`;
  element.textContent = text;
  log.append(element);
  scrollDown();
  return element;
}

const MEDIA_LINK = /(!?)\[([^\]]*)\]\((https?:\/\/[^)\s]+\/arty-files\/[^)\s]+)\)/g;
const VIDEO_FILE = /\.(mp4|webm|mov|mkv)$/i;
const PICTURE_FILE = /\.(png|jpe?g|webp|gif|bmp|avif)$/i;

function mediaKind(link, markedPicture) {
  const path = link.split('?')[0];
  if (VIDEO_FILE.test(path)) return 'video';
  if (markedPicture || PICTURE_FILE.test(path)) return 'picture';
  return null;
}

async function loadMedia(figure, name, link, kind) {
  const caption = figure.querySelector('figcaption');
  try {
    const response = await api(`/media?link=${encodeURIComponent(link)}`);
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || `HTTP ${response.status}`);
    const address = URL.createObjectURL(await response.blob());
    const shown = document.createElement(kind === 'video' ? 'video' : 'img');
    shown.src = address;
    if (kind === 'video') shown.controls = true;
    else {
      shown.alt = name;
      shown.addEventListener('click', () => figure.classList.toggle('full'));
    }
    shown.addEventListener(kind === 'video' ? 'loadedmetadata' : 'load', scrollDown, { once: true });
    const save = document.createElement('a');
    save.href = address;
    save.download = name;
    save.textContent = 'Save';
    figure.querySelector('.media-wait').replaceWith(shown);
    caption.append(save);
  } catch (error) {
    figure.querySelector('.media-wait').textContent = `Couldn't load ${name}: ${error.message}`;
  }
}

function showMedia(element) {
  if (!element) return;
  const found = [];
  const text = element.textContent.replace(MEDIA_LINK, (whole, mark, name, link) => {
    const kind = mediaKind(link, mark === '!');
    if (!kind) return whole;
    found.push({ name: name || 'file', link, kind });
    return '';
  });
  if (!found.length) return;
  element.textContent = text.replace(/\n{3,}/g, '\n\n').trim();
  for (const { name, link, kind } of found) {
    const figure = document.createElement('figure');
    figure.className = 'media';
    const waiting = document.createElement('div');
    waiting.className = 'media-wait';
    waiting.textContent = 'Loading…';
    const caption = document.createElement('figcaption');
    const label = document.createElement('span');
    label.textContent = name;
    caption.append(label);
    figure.append(waiting, caption);
    element.append(figure);
    loadMedia(figure, name, link, kind);
  }
  scrollDown();
}

const ACTION_TAGS = [
  [/^look|screen/i, 'Look'], [/click/i, 'Click'], [/typ/i, 'Type'], [/press|key/i, 'Keys'],
  [/scroll/i, 'Scroll'], [/open/i, 'Open'], [/run|command/i, 'Run'], [/read/i, 'Read'],
  [/writ|sav|edit/i, 'Write'], [/list|files|folder/i, 'Files'], [/clipboard/i, 'Clipboard'],
];

function addActivity(text, fixedTag) {
  const tag = fixedTag || (ACTION_TAGS.find(([pattern]) => pattern.test(text)) || [null, 'Doing'])[1];
  const row = document.createElement('div');
  row.className = 'act';
  const label = document.createElement('b');
  label.textContent = tag;
  const words = document.createElement('span');
  words.textContent = text;
  row.append(label, words);
  log.append(row);
  scrollDown();
}

function addQuestion(id, text) {
  setState('asking', 'Waiting for your OK');
  log.append(stepQuestionCard(text, async (ok) => {
    setState('thinking', 'Working…');
    await api(`/confirm`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id, ok }) })
      .catch(() => add('error', 'Couldn’t send your answer to the local service.'));
  }));
  scrollDown();
}

// ---------- speech ----------

function enqueueSpeech(base64Wav) {
  speech.queue.push(base64Wav);
  if (!speech.playing) playNext();
}

function playNext() {
  const next = speech.queue.shift();
  if (!next) {
    speech.playing = null;
    if (!current || current.finished) settle();
    return;
  }
  setState('speaking', 'Speaking · tap to stop');
  const player = new Audio(`data:audio/wav;base64,${next}`);
  speech.playing = player;
  const advance = () => {
    speech.played += 1;
    playNext();
  };
  player.addEventListener('ended', advance);
  player.addEventListener('error', advance);
  player.play().catch(advance);
}

async function stopSpeaking() {
  speech.queue = [];
  if (speech.playing) {
    speech.playing.pause();
    speech.playing = null;
  }
  await api(`/interrupt`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ chunks: speech.played }),
  }).catch(() => {});
  settle();
}

// ---------- streams ----------

async function readNdjson(response, onEvent) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let newline;
    while ((newline = buffer.indexOf('\n')) >= 0) {
      const line = buffer.slice(0, newline).trim();
      buffer = buffer.slice(newline + 1);
      if (line) onEvent(JSON.parse(line));
    }
  }
}

function handle(event, stream) {
  switch (event.type) {
    case 'armed':
      if (stream.kind === 'wake') setState('waking', 'Say “Arty, …”');
      else { setState('listening', 'Listening…'); mic.classList.add('live'); }
      break;
    case 'wake':
      stream.engaged = true;
      setState('listening', 'Yes?');
      break;
    case 'speech':
      if (stream.kind !== 'wake' || stream.engaged) setState('listening', 'Hearing you…');
      break;
    case 'transcribing':
      setState('thinking', 'Catching that…');
      break;
    case 'transcript':
      stream.engaged = true;
      mic.classList.remove('live');
      add('you', event.text);
      break;
    case 'start':
      stream.replying = true;
      speech.played = 0;
      setState('thinking', 'Thinking…');
      break;
    case 'activity':
      addActivity(event.text);
      setState('thinking', 'Working…');
      break;
    case 'confirm':
      addQuestion(event.id, event.text);
      break;
    case 'chunk':
      if (!stream.reply) stream.reply = add('arty', '');
      // A quoted draft (a post or email to copy) keeps its own lines.
      stream.reply.textContent += (stream.reply.textContent ? (event.quoted ? '\n\n' : ' ') : '') + event.reply;
      scrollDown();
      if (event.audio && !stream.hungUp) enqueueSpeech(event.audio);
      if (event.error) add('error', event.error);
      break;
    case 'cancelled':
      stream.replying = false;
      if (stream.kind !== 'wake' && event.message) stateLabel.textContent = event.message;
      break;
    case 'error':
      stream.replying = false;
      add('error', event.message);
      if (callState.on) hangUp();
      break;
    case 'done':
      stream.replying = false;
      showMedia(stream.reply);
      if (event.reply) notifyIfAway(event.reply);
      break;
    case 'interrupted':
      stream.replying = false;
      break;
    default:
      break;
  }
}

const REPLY_PREVIEW_CHARS = 60;

/** The reply's first few words, ending "…" when there's more. */
function replyPreview(reply) {
  const plain = reply.replace(/[*_`#>]/g, '').replace(/\s+/g, ' ').trim();
  if (plain.length <= REPLY_PREVIEW_CHARS) return plain;
  const space = plain.lastIndexOf(' ', REPLY_PREVIEW_CHARS);
  return `${plain.slice(0, space > REPLY_PREVIEW_CHARS / 2 ? space : REPLY_PREVIEW_CHARS).replace(/[,.;:\s]+$/, '')}…`;
}

let replyNotification = null;

/** "Arty has replied" on the desktop when the window isn't in front; clicking it brings the window up. */
function notifyIfAway(reply) {
  if (document.hasFocus() && !document.hidden) return;
  replyNotification?.close(); // one at a time: the newest reply replaces it
  replyNotification = new Notification('Arty has replied', { body: replyPreview(reply), silent: true });
  replyNotification.onclick = () => window.arty?.show();
}

window.addEventListener('focus', () => replyNotification?.close());

/** Runs one stream from the local service. Only one at a time; a waiting wake-word stream gives way. */
async function run(kind, path, options = {}) {
  if (current && current.kind === 'wake' && !current.engaged) current.controller.abort();
  else if (current) return;
  const stream = { kind, controller: new AbortController(), engaged: kind !== 'wake', replying: false, reply: null, finished: false };
  current = stream;
  input.disabled = kind !== 'wake';
  send.disabled = kind !== 'wake';
  try {
    const response = await api(`${path}`, { ...options, signal: stream.controller.signal });
    await readNdjson(response, (event) => handle(event, stream));
  } catch (error) {
    if (error.name !== 'AbortError') {
      add('error', 'Can’t reach the local Arty service. It restarts by itself; try again in a moment.');
      if (callState.on) hangUp();
    }
  } finally {
    stream.finished = true;
    if (current === stream) current = null;
    if (!current) { // an interrupted wake listener mustn't unlock the box while a reply is running
      input.disabled = false;
      send.disabled = false;
    }
    if (!speech.playing) settle();
    if (kind !== 'wake' || stream.engaged) input.focus();
    if (wakeOn) setTimeout(listenForWake, kind === 'wake' && !stream.engaged ? 0 : 500);
  }
}

function sendText(text, look = false) {
  const message = text.trim();
  if (!message || (current && current.engaged)) return;
  add('you', look ? `(about my screen) ${message}` : message);
  input.value = '';
  autosize();
  run('chat', '/chat/stream', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: message, speak: true, look }),
  });
}

const EXPLAIN_PREVIEW_CHARS = 80;

/** "Explain what I copied": the bubble shows the start of it; Arty gets all of it. */
function explainCopied(copied) {
  if (!copied) {
    add('error', 'Copy (or highlight) something first, then press Ctrl+Alt+E.');
    return;
  }
  if (current && current.engaged) {
    add('error', 'Arty is busy; try again in a moment.');
    return;
  }
  if (speech.playing) stopSpeaking();
  const preview = copied.length > EXPLAIN_PREVIEW_CHARS ? `${copied.slice(0, EXPLAIN_PREVIEW_CHARS)}…` : copied;
  add('you', `(explain what I copied) ${preview}`);
  const text = `Explain this, which I've just copied. If it's an error, say what's wrong and how to fix it; if it's code, what it does.\n\n"""\n${copied}\n"""`;
  run('chat', '/chat/stream', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text, speak: true }),
  });
}

/** Screen awareness: a typed question is sent about the screen; otherwise Arty listens for one. */
function askAboutScreen() {
  if (speech.playing) stopSpeaking();
  if (current && current.engaged) return;
  if (input.value.trim()) return sendText(input.value, true);
  run('listen', '/listen/auto?look=true');
}

function talk() {
  if (speech.playing) return stopSpeaking();
  if (current && current.kind === 'listen') {
    api(`/listen/finish`, { method: 'POST' }).catch(() => {});
    return;
  }
  if (current && current.engaged) return;
  run('listen', '/listen/auto');
}

function listenForWake() {
  if (!wakeOn || callState.on || current || speech.playing) return;
  run('wake', `/wake/listen?timeout=${WAKE_TIMEOUT_S}`);
}

function canTakeCallTurn() {
  return callState.on && !callState.muted && !current && !speech.playing && !speech.queue.length;
}

function queueCallTurn() {
  setTimeout(() => {
    if (canTakeCallTurn()) run('listen', '/listen/auto');
  }, CALL_TURN_GAP_MS);
}

function callDuration() {
  const seconds = Math.floor((Date.now() - callState.startedAt) / 1000);
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
}

function showCallBar(on) {
  $('call-bar').hidden = !on;
  $('call').classList.toggle('on', on);
  $('call-mute').classList.toggle('on', callState.muted);
  $('call-mute').textContent = callState.muted ? 'Unmute' : 'Mute';
  $('call-time').textContent = on ? callDuration() : '0:00';
}

function stopListeningForCall() {
  if (current && current.kind === 'listen') current.controller.abort();
}

function isReplying() {
  return Boolean(current && current.replying);
}

async function endCallLeftOpen() {
  try {
    const response = await api('/call', { signal: AbortSignal.timeout(CALL_CHECK_TIMEOUT_MS) });
    if (!response.ok) throw new Error(`the service answered ${response.status} when asked about the call`);
    const status = await response.json();
    if (!status.on || callState.on) return false;
    const ended = await api('/call/end', { method: 'POST', signal: AbortSignal.timeout(CALL_CHECK_TIMEOUT_MS) });
    if (!ended.ok) throw new Error(`the service answered ${ended.status} when asked to end it`);
    return true;
  } catch (error) {
    console.error(`Arty: couldn't check for a call left open: ${error.message}`);
    return false;
  }
}

async function startCall() {
  if (callState.on) return hangUp();
  if (current && current.engaged) return;
  if (speech.playing) await stopSpeaking();
  Object.assign(callState, { on: true, muted: false, startedAt: Date.now() });
  callState.timer = setInterval(() => { $('call-time').textContent = callDuration(); }, 1000);
  showCallBar(true);
  addActivity('Calling Arty', 'Call');
  run('chat', '/call/start', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ speak: true }),
  });
}

async function hangUp() {
  if (!callState.on) return;
  const lasted = callDuration();
  callState.on = false;
  clearInterval(callState.timer);
  showCallBar(false);
  const replying = isReplying();
  if (current && current.kind !== 'wake') current.hungUp = true;
  if (!replying) stopListeningForCall();
  if (replying || speech.playing || speech.queue.length) await stopSpeaking();
  await api(`/call/end`, { method: 'POST' }).catch(() => {});
  addActivity(`Call ended after ${lasted}`, 'Call');
  settle();
  listenForWake();
}

function toggleMute() {
  if (!callState.on) return;
  callState.muted = !callState.muted;
  if (callState.muted) stopListeningForCall();
  showCallBar(true);
  settle();
}

$('call').addEventListener('click', startCall);
$('call-end').addEventListener('click', hangUp);
$('call-mute').addEventListener('click', toggleMute);

// ---------- header controls ----------

let modelsLoaded = false;

function modelOption(model, current) {
  const option = document.createElement('button');
  option.className = 'model-option';
  option.setAttribute('role', 'option');
  option.setAttribute('aria-selected', String(model.id === current));
  const text = document.createElement('span');
  text.className = 'model-text';
  const title = document.createElement('span');
  title.className = 'model-title';
  title.textContent = model.model || model.name;
  if (model.id === 'Arty') {
    const tag = document.createElement('span');
    tag.className = 'model-tag';
    tag.textContent = 'Default';
    title.append(tag);
  }
  const detail = document.createElement('span');
  detail.className = 'model-detail';
  detail.textContent = model.description || model.name;
  text.append(title, detail);
  const tick = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  tick.setAttribute('viewBox', '0 0 24 24');
  tick.classList.add('model-tick');
  tick.innerHTML = '<path d="M5 12.5l4.5 4.5L19 7.5"/>';
  option.append(text, tick);
  option.addEventListener('click', () => chooseModel(model));
  return option;
}

function showModel(model) {
  $('model-name').textContent = model ? (model.model || model.name) : 'Arty';
}

async function loadModels() {
  try {
    const { models, current } = await (await api(`/models`)).json();
    if (!models.length) return;
    const main = models.find((model) => model.id === 'Arty');
    const shown = models.filter((model) => model.id === 'Arty' || !main?.model || model.model !== main.model);
    $('model-menu').replaceChildren(...shown.map((model) => modelOption(model, current)));
    showModel(models.find((model) => model.id === current) || main);
    modelsLoaded = true;
  } catch { /* shown as offline by refreshHealth */ }
}

function toggleModelMenu(open) {
  $('model-menu').hidden = !open;
  $('model').setAttribute('aria-expanded', String(open));
  if (open) loadModels();
}

async function chooseModel(model) {
  toggleModelMenu(false);
  showModel(model);
  await api(`/model`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: model.id }) })
    .then(loadModels)
    .catch(() => add('error', 'Couldn’t switch model: the local service isn’t answering.'));
}

$('model').addEventListener('click', () => toggleModelMenu($('model-menu').hidden));
document.addEventListener('click', (event) => {
  if (!$('model-menu').hidden && !event.target.closest('.model-picker')) toggleModelMenu(false);
});
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && !$('model-menu').hidden) toggleModelMenu(false);
});

async function refreshHealth() {
  const picker = $('model');
  try {
    const health = await (await api(`/health`)).json();
    picker.classList.remove('offline');
    picker.title = `Which model Arty thinks with (brain at ${new URL(health.brain).host})`;
    if (!modelsLoaded) loadModels();
    $('hands').classList.toggle('on', !!health.computer?.enabled);
    $('hands').title = health.computer?.enabled ? 'Arty can use this computer (click to stop)' : 'Let Arty use this computer';
  } catch {
    picker.classList.add('offline');
    picker.title = 'The local service is starting…';
  }
}

$('hands').addEventListener('click', async () => {
  const enabled = !$('hands').classList.contains('on');
  await api(`/computer`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled }) })
    .catch(() => add('error', 'Couldn’t change that: the local service isn’t answering.'));
  refreshHealth();
});

$('wake').addEventListener('click', () => {
  wakeOn = !wakeOn;
  saveSetting('wake', wakeOn);
  $('wake').classList.toggle('on', wakeOn);
  if (wakeOn) listenForWake();
  else if (current && current.kind === 'wake' && !current.engaged) current.controller.abort();
  settle();
});

$('read').addEventListener('click', () => {
  readOn = !readOn;
  saveSetting('read', readOn);
  $('read').classList.toggle('on', readOn);
});

/** Buttons for a paused background task, answered through the local service to Arty's brain. */
function addTaskButtons(taskId, actions, container = log) {
  container.append(taskAnswerCard(actions, async (action, result) => {
    try {
      const answer = await (await api(`/task/${taskId}/${action}`, { method: 'POST' })).json();
      result.textContent = answer.message || answer.error || 'Done.';
    } catch {
      result.textContent = 'Couldn’t reach the local service.';
    }
  }));
  if (container === log) scrollDown();
}

async function say(text) {
  const { audio } = await (await api(`/say`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text }),
  })).json();
  if (audio) enqueueSpeech(audio);
}

// New notifications from other apps: shown in the log, and read out when READ is on and Arty is free.
async function watchNotifications() {
  for (;;) {
    try {
      const after = lastNotification ?? 0;
      const wait = lastNotification === null ? 0 : 55; // the first call only learns where "now" is
      const { notifications, last } = await (await api(`/events?after=${after}&timeout=${wait}`)).json();
      const isFirstPoll = lastNotification === null;
      lastNotification = last;
      if (isFirstPoll) continue; // older ones are history, not news
      for (const note of notifications) {
        if (note.kind === 'notice') {
          if (!callState.on && !speech.playing && !(current && current.engaged)) {
            run('chat', '/notice/reply', {
              method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: note.notice, speak: true }),
            });
          }
          continue;
        }
        if (note.kind === 'arty') {
          // Arty speaking up by itself: a reminder, briefing, finished job, homelab alert or approval request.
          // It goes to Updates, not the conversation; it's still said out loud.
          newUpdate(note);
          if (!callState.on && !speech.playing && !(current && current.engaged)) await say(note.body || note.summary);
          continue;
        }
        addActivity(`${note.app}: ${note.summary}`, 'Note');
        if (readOn && !callState.on && !speech.playing && !(current && current.engaged)) await say(`${note.app}: ${note.summary}`);
      }
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 5000)); // the service is restarting
    }
  }
}

// ---------- calendar ----------

function dayHeading(isoDay) {
  const day = new Date(`${isoDay}T12:00:00`);
  const today = new Date();
  const tomorrow = new Date(Date.now() + 86400000);
  if (day.toDateString() === today.toDateString()) return 'Today';
  if (day.toDateString() === tomorrow.toDateString()) return 'Tomorrow';
  return day.toLocaleDateString('en-GB', { weekday: 'long', day: 'numeric', month: 'long' });
}

async function showCalendar() {
  const list = $('events');
  list.replaceChildren();
  let events = [];
  try {
    ({ events = [] } = await (await api('/calendar?days=60')).json());
  } catch {
    list.textContent = 'Couldn’t load the calendar.';
    return;
  }
  if (!events.length) {
    const empty = document.createElement('div');
    empty.className = 'empty';
    empty.textContent = 'Nothing in the next 60 days. Add something above, or ask Arty.';
    list.append(empty);
    return;
  }
  let lastDay = '';
  for (const event of events) {
    const day = event.start.slice(0, 10);
    if (day !== lastDay) {
      const heading = document.createElement('div');
      heading.className = 'day';
      heading.textContent = dayHeading(day);
      list.append(heading);
      lastDay = day;
    }
    const row = document.createElement('div');
    row.className = `event${event.calendar !== 'Arty' ? ' google' : ''}`;
    const when = document.createElement('span');
    when.className = 'when';
    when.textContent = event.all_day ? 'all day' : `${event.start.slice(11, 16)}`;
    const what = document.createElement('span');
    what.className = 'what';
    what.textContent = event.title;
    const more = document.createElement('span');
    more.className = 'more';
    more.textContent = [event.location, event.repeats ? 'repeats' : '', event.calendar !== 'Arty' ? event.calendar : ''].filter(Boolean).join(' · ');
    row.append(when, what, more);
    if (event.id) {
      const remove = document.createElement('button');
      remove.className = 'icon-button';
      remove.title = event.repeats ? `Remove ${event.title} (stops it repeating)` : `Remove ${event.title}`;
      remove.textContent = '×';
      remove.addEventListener('click', async () => {
        const removed = await api(`/calendar/${event.id}`, { method: 'DELETE' }).then((r) => r.ok).catch(() => false);
        $('event-status').textContent = removed ? '' : `Couldn’t remove ${event.title}.`;
        $('event-status').hidden = removed;
        showCalendar();
      });
      row.append(remove);
    }
    list.append(row);
  }
}

/** One view at a time: the conversation (null), 'updates' or 'calendar'. */
function showView(view) {
  $('updates').hidden = view !== 'updates';
  $('calendar').hidden = view !== 'calendar';
  log.hidden = view !== null;
  $('upd').classList.toggle('on', view === 'updates');
  $('cal').classList.toggle('on', view === 'calendar');
  if (view === 'updates') showUpdates();
  if (view === 'calendar') showCalendar();
}

$('cal').addEventListener('click', () => showView($('calendar').hidden ? 'calendar' : null));
$('upd').addEventListener('click', () => showView($('updates').hidden ? 'updates' : null));

// ---------- updates ----------

const UPDATE_KINDS = { alert: 'Alert', approval: 'Approval', reminder: 'Reminder', message: 'Update' };
let unseenUpdates = 0;
let updateNotification = null;

function setUnseen(count) {
  unseenUpdates = count;
  $('upd').dataset.count = count ? String(Math.min(count, 99)) : '';
  $('upd').classList.toggle('unseen', count > 0);
}

function updateCard(update, waiting) {
  const card = document.createElement('div');
  card.className = `update ${update.kind || 'message'}`;
  const meta = document.createElement('div');
  meta.className = 'meta';
  const kind = document.createElement('span');
  kind.textContent = UPDATE_KINDS[update.kind] || 'Update';
  const when = document.createElement('span');
  when.textContent = update.created ? new Date(update.created * 1000).toLocaleString('en-GB', { dateStyle: 'short', timeStyle: 'short' }) : '';
  meta.append(kind, when);
  const title = document.createElement('div');
  title.className = 'title';
  title.textContent = update.title;
  card.append(meta, title);
  if (update.message) {
    const body = document.createElement('div');
    body.className = 'body';
    body.textContent = update.message;
    card.append(body);
  }
  if (update.task_id && update.actions?.length && waiting.includes(update.task_id)) addTaskButtons(update.task_id, update.actions, card);
  return card;
}

async function showUpdates() {
  const list = $('update-list');
  setUnseen(0);
  let updates = [];
  let waiting = [];
  try {
    ({ updates = [], waiting = [] } = await (await api('/updates')).json());
  } catch {
    list.textContent = 'Couldn’t load updates: the local service isn’t answering.';
    return;
  }
  list.replaceChildren();
  if (!updates.length) {
    const empty = document.createElement('div');
    empty.className = 'empty';
    empty.textContent = 'Nothing yet. Reminders, briefings, results and alerts from Arty land here.';
    list.append(empty);
    return;
  }
  for (const update of updates.slice().reverse()) list.append(updateCard(update, waiting));
}

/** Something new from Arty: into Updates (refreshed if open, counted if not), and a desktop notification when the window isn't in front. */
function newUpdate(note) {
  if (!$('updates').hidden) showUpdates();
  else setUnseen(unseenUpdates + 1);
  if (document.hasFocus()) return;
  updateNotification?.close();
  updateNotification = new Notification(note.summary || 'Arty', { body: (note.body || '').slice(0, 160), silent: true });
  updateNotification.onclick = () => { window.arty?.show(); showView('updates'); };
}

$('event-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const title = $('event-title').value.trim();
  const when = $('event-when').value.trim();
  if (!title || !when) return;
  let message = '';
  try {
    ({ message = '' } = await (await api('/calendar', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title, when }),
    })).json());
  } catch {
    message = 'Couldn’t reach the calendar.';
  }
  const added = message.startsWith('Added');
  if (added) {
    $('event-title').value = '';
    $('event-when').value = '';
  }
  $('event-status').textContent = added ? '' : (message || 'Couldn’t add that.');
  $('event-status').hidden = added;
  showCalendar();
});

$('forget').addEventListener('click', async () => {
  if (current && current.engaged) return;
  await api(`/reset`, { method: 'POST' }).catch(() => {});
  log.replaceChildren();
  showEmpty();
});

$('hide').addEventListener('click', () => window.arty?.hide());

// ---------- composer ----------

function autosize() {
  input.style.height = 'auto';
  input.style.height = `${Math.min(input.scrollHeight, 120)}px`;
}

input.addEventListener('input', autosize);
input.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    sendText(input.value);
  }
});
$('composer').addEventListener('submit', (event) => {
  event.preventDefault();
  sendText(input.value);
});
mic.addEventListener('click', talk);
$('look').addEventListener('click', askAboutScreen);
orb.addEventListener('click', talk);
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && speech.playing) stopSpeaking();
});
window.arty?.onListen(talk);
window.arty?.onLook(askAboutScreen);
window.arty?.onExplain(explainCopied);
window.arty?.onTransparency((solidity) => {
  document.documentElement.style.setProperty('--glass', String(solidity));
  document.body.classList.toggle('see-through', solidity < 1);
});

// ---------- start ----------

function showEmpty() {
  if (log.querySelector('.empty, .msg')) return;
  const empty = document.createElement('div');
  empty.className = 'empty';
  empty.textContent = 'Ask anything, or tell Arty to do something on this computer.';
  log.append(empty);
}

async function loadHistory() {
  try {
    const { messages } = await (await api(`/history`)).json();
    for (const message of messages) {
      if (typeof message.content !== 'string' || !message.content) continue;
      const element = add(message.role === 'user' ? 'you' : 'arty', message.content);
      if (message.role !== 'user') showMedia(element);
    }
    return true;
  } catch {
    return false;
  }
}

const CONNECT_FIELDS = {
  base_url: 'connect-base', api_key: 'connect-key', remote_url: 'connect-remote',
  access_client_id: 'connect-id', access_client_secret: 'connect-secret',
};
const SERVICE_WAIT_ATTEMPTS = 60;
const SERVICE_RETRY_MS = 1000;
const RESTART_PAUSE_MS = 1500;

const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function readConnection() {
  try {
    const response = await api('/connection');
    return response.ok ? await response.json() : null;
  } catch {
    return null;
  }
}

async function waitForConnection() {
  for (let attempt = 0; attempt < SERVICE_WAIT_ATTEMPTS; attempt += 1) {
    const known = await readConnection();
    if (known) return known;
    await pause(SERVICE_RETRY_MS);
  }
  return null;
}

function connectStatus(text, problem = false) {
  const status = $('connect-status');
  status.textContent = text;
  status.classList.toggle('problem', problem);
}

function showConnect(known, canCancel) {
  $('panel').classList.add('connecting');
  $('connect').hidden = false;
  $('connect-cancel').hidden = !canCancel;
  $('connect-base').value = known?.base_url || '';
  $('connect-remote').value = known?.remote_url || '';
  $('connect-key').value = '';
  $('connect-id').value = '';
  $('connect-secret').value = '';
  connectStatus('');
  $('connect-base').focus();
}

function hideConnect() {
  $('panel').classList.remove('connecting');
  $('connect').hidden = true;
}

function enteredConnection() {
  return Object.fromEntries(Object.entries(CONNECT_FIELDS).map(([name, id]) => [name, $(id).value.trim()]));
}

async function saveConnection(event) {
  event.preventDefault();
  const button = $('connect-save');
  button.disabled = true;
  connectStatus('Checking…');
  try {
    const response = await api('/connection', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(enteredConnection()),
    });
    const answer = await response.json().catch(() => ({}));
    if (!response.ok) {
      connectStatus(answer.error || `Arty's local service answered ${response.status}.`, true);
      return;
    }
    connectStatus(`Connected to ${answer.reached}. Restarting…`);
    await pause(RESTART_PAUSE_MS);
    location.reload();
  } catch (error) {
    connectStatus(`Couldn't reach Arty's local service: ${error.message}`, true);
  } finally {
    button.disabled = false;
  }
}

const SECONDS_PER_MINUTE = 60;
const MILLISECONDS_PER_SECOND = 1000;
let phoneCodeTimer = null;

function phoneCodeStatus(text, problem = false) {
  const status = $('phone-code-status');
  status.textContent = text;
  status.classList.toggle('problem', problem);
}

function hidePhoneCode() {
  clearTimeout(phoneCodeTimer);
  $('panel').classList.remove('showing-code');
  $('phone-code').hidden = true;
  $('phone-code-image').removeAttribute('src');
}

async function showPhoneCode() {
  hidePhoneCode();
  hideChromeSites();
  $('panel').classList.add('showing-code');
  $('phone-code').hidden = false;
  phoneCodeStatus('Making the code…');
  try {
    const response = await api('/phone-setup-code');
    const answer = await response.json().catch(() => ({}));
    if (!response.ok) {
      phoneCodeStatus(answer.error || `Arty's local service answered ${response.status}.`, true);
      return;
    }
    $('phone-code-image').src = answer.image;
    phoneCodeStatus(`For ${answer.address}. It closes by itself after ${Math.round(answer.seconds / SECONDS_PER_MINUTE)} minutes.`);
    phoneCodeTimer = setTimeout(hidePhoneCode, answer.seconds * MILLISECONDS_PER_SECOND);
  } catch (error) {
    phoneCodeStatus(`Couldn't reach Arty's local service: ${error.message}`, true);
  }
}

$('phone-code-close').addEventListener('click', hidePhoneCode);
window.arty?.onPhoneCode(showPhoneCode);

function chromeSitesStatus(text, problem = false) {
  const status = $('chrome-sites-status');
  status.textContent = text;
  status.classList.toggle('problem', problem);
}

function hideChromeSites() {
  $('panel').classList.remove('showing-sites');
  $('chrome-sites').hidden = true;
  $('pairing-code').textContent = '';
}

async function answerOf(response) {
  const answer = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(answer.error || `Arty's local service answered ${response.status}`);
  return answer;
}

async function forgetChromeSite(site) {
  try {
    const { sites } = await answerOf(await api('/browser/sites/forget', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ site }),
    }));
    fillSiteList($('site-list'), sites, forgetChromeSite);
    chromeSitesStatus(`Forgot ${site}.`);
  } catch (error) {
    chromeSitesStatus(`Couldn't forget ${site}: ${error.message}`, true);
  }
}

async function showChromeSites() {
  hidePhoneCode();
  $('panel').classList.add('showing-sites');
  $('chrome-sites').hidden = false;
  chromeSitesStatus('Loading…');
  try {
    const [{ sites }, { code }] = await Promise.all([answerOf(await api('/browser/sites')), answerOf(await api('/browser/pairing'))]);
    fillSiteList($('site-list'), sites, forgetChromeSite);
    $('pairing-code').textContent = code;
    chromeSitesStatus('');
  } catch (error) {
    chromeSitesStatus(`Couldn't reach Arty's local service: ${error.message}`, true);
  }
}

$('chrome-sites-close').addEventListener('click', hideChromeSites);
window.arty?.onChromeSites(showChromeSites);
$('connect-form').addEventListener('submit', saveConnection);
$('connect-cancel').addEventListener('click', hideConnect);
window.arty?.onConnect(async () => showConnect(await readConnection(), true));

async function start() {
  $('wake').classList.toggle('on', wakeOn);
  $('read').classList.toggle('on', readOn);
  settle();
  showEmpty();
  const known = await waitForConnection();
  const staleCallCheck = known ? endCallLeftOpen() : Promise.resolve(false);
  if (known && !known.configured) {
    showConnect(known, false);
    return;
  }
  for (let attempt = 0; attempt < SERVICE_WAIT_ATTEMPTS && !(await loadHistory()); attempt += 1) {
    await pause(SERVICE_RETRY_MS);
  }
  if (!log.querySelector('.msg')) showEmpty();
  if (await staleCallCheck) addActivity('A call was still open from before the window reloaded, so it was ended', 'Call');
  refreshHealth();
  setInterval(refreshHealth, HEALTH_EVERY_MS);
  listenForWake();
  watchNotifications();
}

start();
