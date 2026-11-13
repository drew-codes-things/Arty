import { answerGuard, flashNewQuestion } from "./answerguard.js";
import { fill, h, sectionHead, showToast } from "./dom.js";
import {
  COMPACTED_LINE, NO_SUMMARY_SHOWN, addedSince, sessionTitle,
  FINISHED_AFTER_MS, FIRST_POLL, activityOf, addEvent, addNote, diffTone, isNearBottom, markdownBlocks, newTranscript,
  nextPoll, openRequests, permissionPrompt, readableInput, sender, sessionDetails, sessionLine, stepLine, toolSummary, trimTranscript,
} from "./remote.js";

const WAIT_SECONDS = 20;
const LIST_REFRESH_MS = 5000;
const RETRY_MS = 3000;
const FORBIDDEN = 403;
const ENDED_STATUSES = new Set([404, 410]);
const NOT_ALLOWED = "This key can't control Arty CLI. Turn on Arty CLI for this device in starfire's Arty devices page.";
const MISSED_NOTE = "Some lines were missed while this phone was behind. Showing from where the computer is now.";
const ENDED_REASON = "This session has ended on the computer, so it can't take messages.";
const MAX_OPEN_REQUESTS = 20;
const BUSY_PHASES = new Set(["working", "waiting"]);

let shown = 0;

function pause(milliseconds) {
  return new Promise((resolve) => { setTimeout(resolve, milliseconds); });
}

function claimScreen(page) {
  shown += 1;
  const mine = shown;
  return () => mine === shown && page.isConnected;
}

function spanNode(span) {
  if (span.type === "text") return span.text;
  if (span.type === "link") return h("a", { href: span.href, target: "_blank", rel: "noopener noreferrer" }, span.text);
  return h(span.type, {}, span.text);
}

function lineNodes(lines) {
  return lines.map((spans, index) => [index ? h("br") : null, spans.map(spanNode)]);
}

function markdownNodes(text) {
  return markdownBlocks(text).map((block) => {
    if (block.type === "code") return h("pre", {}, h("code", {}, block.text));
    if (block.type === "heading") return h("h4", {}, block.spans.map(spanNode));
    if (block.type === "quote") return h("blockquote", {}, lineNodes(block.lines));
    if (block.type === "list") return h(block.ordered ? "ol" : "ul", {}, block.items.map((spans) => h("li", {}, spans.map(spanNode))));
    return h("p", {}, lineNodes(block.lines));
  });
}

function previewNodes(text) {
  return String(text).split("\n").map((line, index) => {
    const tone = diffTone(line);
    const shownLine = index ? `\n${line}` : line;
    return tone ? h("span", { class: `diff-${tone}` }, shownLine) : shownLine;
  });
}

function stepView() {
  const line = h("span");
  const input = h("pre", { class: "remote-step-input" });
  const output = h("pre", { class: "remote-step-output" });
  const element = h("details", { class: "remote-step" }, h("summary", {}, line), input, output);
  let shownInput = null;
  const update = (step) => {
    line.replaceChildren(step.failed ? `${stepLine(step)} (failed)` : stepLine(step));
    element.className = step.failed ? "remote-step failed" : "remote-step";
    const readable = readableInput(step);
    if (readable !== shownInput) input.replaceChildren(...previewNodes(readable));
    shownInput = readable;
    input.hidden = !readable;
    output.replaceChildren(step.output ?? "Still running...");
  };
  return { element, update };
}

function toolsView() {
  const summary = h("span", { class: "remote-tools-summary" });
  const steps = h("div", { class: "remote-steps" });
  const element = h("details", { class: "remote-tools" }, h("summary", {}, summary), steps);
  const views = [];
  const update = (item) => {
    summary.replaceChildren(toolSummary(item.steps));
    item.steps.forEach((step, index) => {
      if (!views[index]) {
        views[index] = stepView();
        steps.append(views[index].element);
      }
      views[index].update(step);
    });
  };
  return { element, update };
}

function itemView(item) {
  if (item.kind === "tools") return toolsView();
  if (item.kind === "compacted") {
    const summary = h("div", { class: "remote-steps remote-markdown" });
    const element = h("details", { class: "remote-tools remote-compacted" }, h("summary", {}, COMPACTED_LINE), summary);
    return { element, update: (current) => summary.replaceChildren(...(current.text ? markdownNodes(current.text) : [NO_SUMMARY_SHOWN])) };
  }
  if (item.kind === "note") {
    const element = h("p", { class: item.problem ? "notice remote-note error-text" : "notice remote-note" }, item.text);
    return { element, update: (current) => element.replaceChildren(current.text) };
  }
  if (item.kind === "user") {
    const element = h("div", { class: "message user" }, h("span", { class: "card-meta" }, item.who),
      h("div", { class: "bubble remote-text" }, item.text));
    return { element, update: () => {} };
  }
  const body = h("div", { class: "bubble remote-markdown" });
  const element = h("div", { class: "message assistant" }, h("span", { class: "card-meta" }, "Arty"), body);
  return { element, update: (current) => body.replaceChildren(...markdownNodes(current.text)) };
}

function transcriptView(element) {
  const views = new Map();
  let model = newTranscript();
  const show = ({ item, isNew }) => {
    if (isNew) {
      views.set(item.id, itemView(item));
      element.append(views.get(item.id).element);
    }
    views.get(item.id).update(item);
  };
  const dropOldest = () => {
    const dropped = trimTranscript(model);
    if (!dropped) return;
    element.replaceChildren(...[...element.children].slice(dropped));
    const oldestKept = model.items[0].id;
    for (const id of views.keys()) {
      if (id < oldestKept) views.delete(id);
    }
  };
  return {
    get model() { return model; },
    restart(note) {
      model = newTranscript();
      views.clear();
      element.replaceChildren();
      show({ item: addNote(model, note), isNew: true });
    },
    add(events) {
      for (const event of events ?? []) {
        const firstNewId = model.nextId;
        const change = addEvent(model, event);
        for (const item of addedSince(model, firstNewId)) show({ item, isNew: true });
        if (change && !change.isNew) show(change);
      }
      dropOldest();
    },
  };
}

function permissionCard(api, sessionId, request, answered, readUpTo) {
  const problem = h("p", { class: "notice error-text" });
  problem.hidden = true;
  let sending = false;
  const buttons = [];
  const guard = answerGuard();
  const answer = async (allow) => {
    if (!guard.isReady() || sending || answered.has(request.request_id)) return;
    sending = true;
    buttons.forEach((button) => { button.disabled = true; });
    problem.hidden = true;
    try {
      await api.remoteAnswer(sessionId, request.request_id, allow);
      answered.set(request.request_id, readUpTo());
      card.hidden = true;
      showToast(allow ? "Allowed" : "Said no");
    } catch (error) {
      problem.textContent = error.message;
      problem.hidden = false;
      buttons.forEach((button) => { button.disabled = false; });
      showToast(error.message);
    } finally {
      sending = false;
    }
  };
  buttons.push(h("button", { type: "button", class: "button primary", onclick: () => answer(true) }, "Allow"),
    h("button", { type: "button", class: "button", onclick: () => answer(false) }, "Deny"));
  const { heading, preview } = permissionPrompt(request);
  const card = h("article", { class: "card status-waiting remote-permission" }, h("div", { class: "card-title" }, heading),
    preview ? h("pre", { class: "remote-preview" }, previewNodes(preview)) : null,
    h("div", { class: "row remote-answers" }, ...buttons), problem);
  return flashNewQuestion(card, buttons, guard);
}

function forgetAnswersAskedAgain(events, answered, cards) {
  for (const event of events ?? []) {
    const isAskedAgain = event.type === "permission" && answered.has(event.request_id) && event.seq > answered.get(event.request_id);
    if (!isAskedAgain) continue;
    answered.delete(event.request_id);
    cards.delete(event.request_id);
  }
}

function shownCards(api, sessionId, open, cards, answered, readUpTo) {
  const waitingIds = new Set(open.map((request) => request.request_id));
  for (const requestId of cards.keys()) {
    if (!waitingIds.has(requestId)) cards.delete(requestId);
  }
  for (const requestId of answered.keys()) {
    if (!waitingIds.has(requestId)) answered.delete(requestId);
  }
  return open.filter((request) => !answered.has(request.request_id)).map((request) => {
    if (!cards.has(request.request_id)) cards.set(request.request_id, permissionCard(api, sessionId, request, answered, readUpTo));
    return cards.get(request.request_id);
  });
}

function sessionHeader() {
  const name = h("strong", { class: "remote-name" }, "Connecting...");
  const details = h("span", { class: "remote-details" });
  const state = h("span", { class: "remote-state" });
  const element = h("div", { class: "remote-header" }, name, details, state);
  return {
    element,
    show(session, status, tone = "") {
      if (session) {
        name.replaceChildren(sessionTitle(session));
        details.replaceChildren(sessionDetails(session));
      }
      state.replaceChildren(status);
      state.className = `remote-state ${tone}`.trim();
    },
  };
}

function composerDock(api, sessionId) {
  const jump = h("button", { type: "button", class: "remote-jump" }, "Jump to latest");
  jump.hidden = true;
  const dots = h("span", { class: "dots", "aria-hidden": "true" }, h("i"), h("i"), h("i"));
  const activityText = h("span");
  const activity = h("div", { class: "remote-activity", role: "status" }, dots, activityText);
  const reason = h("p", { class: "notice remote-reason" });
  reason.hidden = true;
  const box = h("textarea", { rows: "1", maxlength: "8000", placeholder: "Message Arty CLI", "aria-label": "Message Arty CLI", enterkeyhint: "send" });
  const send = sender((text) => api.remoteMessage(sessionId, text), box, showToast);
  box.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" || event.shiftKey || event.isComposing) return;
    event.preventDefault();
    send();
  });
  const stop = async () => {
    try {
      await api.remoteInterrupt(sessionId);
      showToast("Asked the computer to stop");
    } catch (error) {
      showToast(error.message);
    }
  };
  const sendButton = h("button", { type: "button", class: "button primary small", onclick: send }, "Send");
  const stopButton = h("button", { type: "button", class: "button small", onclick: stop }, "Stop");
  const element = h("div", { class: "remote-dock" }, jump, activity, reason,
    h("div", { class: "remote-composer" }, box, h("div", { class: "row" }, sendButton, stopButton)));
  return {
    element,
    jump,
    show(now) {
      activityText.replaceChildren(now.text);
      activity.className = `remote-activity phase-${now.phase}`;
      dots.hidden = now.phase !== "working";
      stopButton.hidden = !BUSY_PHASES.has(now.phase);
    },
    end() {
      box.disabled = true;
      box.setAttribute("placeholder", ENDED_REASON);
      sendButton.disabled = true;
      stopButton.hidden = true;
      reason.replaceChildren(ENDED_REASON);
      reason.hidden = false;
    },
  };
}

function bottomFollower(scroller, jump) {
  const isAtBottom = () => isNearBottom(scroller && { scrollTop: scroller.scrollTop, scrollHeight: scroller.scrollHeight, clientHeight: scroller.clientHeight });
  const toBottom = () => {
    if (scroller) scroller.scrollTop = scroller.scrollHeight;
    jump.hidden = true;
  };
  const onScroll = () => {
    if (isAtBottom()) jump.hidden = true;
  };
  jump.addEventListener("click", toBottom);
  scroller?.addEventListener("scroll", onScroll, { passive: true });
  return {
    isAtBottom,
    settle(wasAtBottom, grew) {
      if (!grew) return;
      if (wasAtBottom) toBottom();
      else jump.hidden = false;
    },
    stop() { scroller?.removeEventListener("scroll", onScroll); },
  };
}

export async function followComputer(page, api, sessionId, onBack) {
  const isCurrent = claimScreen(page);
  const header = sessionHeader();
  const transcriptElement = h("div", { class: "remote-transcript" });
  const waiting = h("div", { class: "remote-waiting" });
  const dock = composerDock(api, sessionId);
  const back = h("button", { type: "button", class: "link-button accent", onclick: onBack }, "‹ Computers");
  page.className = "remote-screen";
  fill(page, back, header.element, transcriptElement, waiting, dock.element);
  const transcript = transcriptView(transcriptElement);
  const follower = bottomFollower(page.closest?.(".view") ?? null, dock.jump);
  let poll = FIRST_POLL;
  let open = [];
  let ended = false;
  let waitingCount = 0;
  let lastEventAt = Date.now();
  let recheck = null;
  const cards = new Map();
  const answered = new Map();
  const showActivity = () => dock.show(activityOf(transcript.model, { ended, waiting: waitingCount, quietMs: Date.now() - lastEventAt }));
  showActivity();
  try {
    while (isCurrent()) {
      let read;
      try {
        read = await api.remoteEvents(sessionId, poll.after, poll.wait);
      } catch (error) {
        if (!ENDED_STATUSES.has(error.status)) {
          header.show(null, `Reconnecting: ${error.message}`, "error-text");
          await pause(RETRY_MS);
          continue;
        }
        ended = true;
        header.show(null, "Session ended");
        dock.end();
        showActivity();
        return;
      }
      if (!isCurrent()) return;
      const wasAtBottom = follower.isAtBottom();
      const fresh = read.events ?? [];
      if (read.missed) transcript.restart(MISSED_NOTE);
      if (poll === FIRST_POLL || read.missed) open = read.session?.waiting_for ?? [];
      transcript.add(fresh);
      if (fresh.length) lastEventAt = Date.now();
      open = openRequests(open, fresh).slice(-MAX_OPEN_REQUESTS);
      forgetAnswersAskedAgain(fresh, answered, cards);
      poll = nextPoll(poll, read, WAIT_SECONDS);
      const asking = read.ended ? [] : shownCards(api, sessionId, open, cards, answered, () => poll.after);
      waiting.replaceChildren(...asking);
      waitingCount = asking.length;
      ended = Boolean(read.ended);
      header.show(read.session, ended ? "Session ended" : "Live", ended ? "" : "live");
      showActivity();
      follower.settle(wasAtBottom, fresh.length > 0 || read.missed);
      clearTimeout(recheck);
      recheck = setTimeout(showActivity, FINISHED_AFTER_MS);
      if (ended) {
        dock.end();
        return;
      }
    }
  } finally {
    clearTimeout(recheck);
    follower.stop();
  }
}

export async function renderComputers(container, api) {
  const page = h("div");
  fill(container, sectionHead("Computers"), page);
  const isCurrent = claimScreen(page);
  const reopen = () => renderComputers(container, api);
  while (isCurrent()) {
    let sessions;
    try {
      sessions = await api.remoteSessions();
    } catch (error) {
      fill(page, h("p", { class: "notice error-text" }, error.status === FORBIDDEN ? NOT_ALLOWED : error.message));
      if (error.status === FORBIDDEN) return;
      await pause(RETRY_MS);
      continue;
    }
    if (!isCurrent()) return;
    const cards = sessions.map((session) => h("article", { class: session.waiting_for?.length ? "card status-waiting" : "card" },
      h("div", { class: "card-title" }, sessionLine(session)),
      session.waiting_for?.length ? h("div", { class: "card-meta" }, "Waiting for an answer") : null,
      h("div", { class: "row" }, h("button", { type: "button", class: "button small", onclick: () => {
        fill(container, sectionHead("Computers"), page);
        followComputer(page, api, session.id, reopen);
      } }, "Open"))));
    fill(page, h("p", { class: "notice" }, "Computers sharing Arty CLI. Run /remote in Arty CLI to share one."),
      cards.length ? cards : h("p", { class: "notice" }, "No computer is sharing Arty CLI right now."));
    await pause(LIST_REFRESH_MS);
  }
}
