const SESSION_ID = /^[0-9a-f]{16}$/;
const UNNAMED_COMPUTER = "A computer";
const DEFAULT_MODE = "default";
const ON_THE_COMPUTER = "On the computer";
const THE_COMPUTER = "the computer";
const SESSION_ENDED = "The session ended on the computer.";
export const SHARED_FROM_HERE = "Sharing started here. Everything above is the earlier conversation.";
export const COMPACTED_LINE = "Conversation compacted: Arty carries on from this summary";
export const NO_SUMMARY_SHOWN = "No summary was kept.";
export const MAX_KEPT_ITEMS = 2000;
export const FINISHED_AFTER_MS = 4000;
const MAX_REMEMBERED_QUESTIONS = 100;
const NEAR_BOTTOM_PX = 48;
const MAX_TARGET_CHARS = 80;
const FAILED_RESULT = /^(Error|Not done)/;
const STEP_TARGETS = ["path", "pattern", "command", "url", "query", "task", "job", "name"];
const MCP_TOOL = /^mcp__(.+?)__(.+)$/;
const WEB_ADDRESS = /^https?:[/][/]/i;
const TRAILING_PUNCTUATION = /[.,;:!?)]+$/;
const FENCE = /^\s*```/;
const HEADING = /^\s*#{1,6}\s+(.*)$/;
const QUOTE = /^\s*>\s?(.*)$/;
const BULLET = /^\s*[-*•]\s+(.*)$/;
const NUMBERED = /^\s*\d+[.)]\s+(.*)$/;
const INLINE = /`([^`]+)`|\*\*([^*]+)\*\*|\[([^\]]+)\]\(([^)\s]+)\)|(https?:[/][/][^\s<>"'`]+)|(^|[^\w*])\*([^*\s](?:[^*]*[^*\s])?)\*(?![\w*])/g;

export function sessionTitle(session) {
  return String(session?.title || computerName(session));
}

export function sessionDetails(session) {
  const parts = [];
  if (session?.title && session?.name) parts.push(String(session.name));
  if (session?.folder && !sessionTitle(session).startsWith(String(session.folder))) parts.push(String(session.folder));
  if (session?.model) parts.push(String(session.model));
  parts.push(`${session?.mode || DEFAULT_MODE} mode`);
  return parts.join(" · ");
}

export function computerName(session) {
  return String(session?.name || UNNAMED_COMPUTER);
}

export function sessionLine(session) {
  return `${sessionTitle(session)} · ${sessionDetails(session)}`;
}

function fileAction(done, doing) {
  return {
    group: done,
    files: true,
    one: (target) => `${done} ${target || "a file"}`,
    many: (count) => `${done} ${count} files`,
    doing: (target) => `${doing} ${target || "a file"}`,
  };
}

function plainAction(one, many, doing) {
  return { one: () => one, many, doing: (target) => (target ? `${doing} ${target}` : doing) };
}

const ACTIONS = {
  read_file: fileAction("Read", "Reading"),
  write_file: fileAction("Wrote", "Writing"),
  edit_file: fileAction("Edited", "Editing"),
  multi_edit: fileAction("Edited", "Editing"),
  notebook_edit: fileAction("Edited", "Editing"),
  run_command: plainAction("Ran a command", (count) => `Ran ${count} commands`, "Running"),
  command_output: plainAction("Checked a running command", (count) => `Checked running commands ${count} times`, "Reading the output of job"),
  stop_command: plainAction("Stopped a command", (count) => `Stopped ${count} commands`, "Stopping job"),
  list_files: plainAction("Listed files", (count) => `Listed files ${count} times`, "Listing"),
  search: plainAction("Searched the project", (count) => `Searched the project ${count} times`, "Searching for"),
  web_fetch: plainAction("Fetched a web page", (count) => `Fetched ${count} web pages`, "Fetching"),
  web_search: plainAction("Searched the web", (count) => `Searched the web ${count} times`, "Searching the web for"),
  todo_write: plainAction("Updated the to-do list", () => "Updated the to-do list", "Updating the to-do list"),
  explore: plainAction("Explored the project", (count) => `Explored the project ${count} times`, "Exploring:"),
  helper: plainAction("Asked a helper", (count) => `Asked helpers ${count} times`, "Handing work to"),
  skill: plainAction("Loaded a skill", (count) => `Loaded ${count} skills`, "Loading the skill"),
  present_plan: plainAction("Shared a plan", (count) => `Shared ${count} plans`, "Writing up a plan"),
};

function unknownAction(tool) {
  const mcp = tool.match(MCP_TOOL);
  const label = mcp ? `${mcp[2]} from ${mcp[1]}` : tool || "a tool";
  return { one: () => `Used ${label}`, many: (count) => `Used ${label} ${count} times`, doing: () => `Using ${label}` };
}

function actionOf(tool) {
  return Object.hasOwn(ACTIONS, tool) ? ACTIONS[tool] : unknownAction(tool);
}

function oneLine(text) {
  const flat = String(text).replace(/\s+/g, " ").trim();
  return flat.length > MAX_TARGET_CHARS ? `${flat.slice(0, MAX_TARGET_CHARS - 3)}...` : flat;
}

function fileName(path) {
  return String(path ?? "").split(/[\\/]/).filter(Boolean).at(-1) ?? "";
}

function parsedInput(text) {
  try {
    const value = JSON.parse(text);
    return value && typeof value === "object" && !Array.isArray(value) ? value : null;
  } catch {
    return null;
  }
}

function targetOf(tool, args) {
  if (!args) return "";
  if (actionOf(tool).files) return fileName(args.path);
  const key = STEP_TARGETS.find((name) => typeof args[name] === "string" && args[name].trim());
  return key ? oneLine(args[key]) : "";
}

function newStep(tool, input) {
  const args = parsedInput(input);
  return { tool, input, args, target: targetOf(tool, args), output: null, failed: false };
}

function lowerFirst(text) {
  return text.charAt(0).toLowerCase() + text.slice(1);
}

export function toolSummary(steps) {
  const groups = new Map();
  for (const step of steps) {
    const action = actionOf(step.tool);
    const key = action.group ?? step.tool;
    if (!groups.has(key)) groups.set(key, { action, targets: new Set(), count: 0 });
    const group = groups.get(key);
    group.count += 1;
    group.targets.add(step.target);
  }
  return [...groups.values()].map(({ action, targets, count }) => {
    const total = action.files ? targets.size : count;
    return total === 1 ? action.one([...targets][0]) : action.many(total);
  }).map((phrase, index) => (index ? lowerFirst(phrase) : phrase)).join(", ");
}

export function stepLine(step) {
  const action = actionOf(step.tool);
  if (action.files || !step.target) return action.one(step.target);
  return `${action.one(step.target)}: ${step.target}`;
}

function changeLines(before, after) {
  const marked = (text, mark) => (typeof text === "string" && text ? text.split("\n").map((line) => `${mark} ${line}`) : []);
  return [...marked(before, "-"), ...marked(after, "+")].join("\n");
}

function shownValue(value) {
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.map((entry) => (entry && typeof entry === "object" ? Object.values(entry).join(" ") : String(entry))).join(", ");
  return JSON.stringify(value);
}

export function readableInput(step) {
  const { args } = step;
  if (!args) return step.input;
  if (typeof args.command === "string" && step.tool === "run_command") return `$ ${args.command}`;
  const path = typeof args.path === "string" ? [`path: ${args.path}`] : [];
  if (typeof args.old_text === "string" || typeof args.new_text === "string") return [...path, changeLines(args.old_text, args.new_text)].join("\n");
  if (Array.isArray(args.edits)) return [...path, ...args.edits.map((edit) => changeLines(edit?.old_text, edit?.new_text))].join("\n");
  if (typeof args.content === "string") return [...path, args.content].join("\n");
  return Object.entries(args).map(([key, value]) => `${key}: ${shownValue(value)}`).join("\n");
}

export function diffTone(line) {
  if (line.startsWith("+++") || line.startsWith("---")) return "";
  if (line.startsWith("+")) return "added";
  if (line.startsWith("-")) return "removed";
  return line.startsWith("@@") ? "marker" : "";
}

export function newTranscript() {
  return { items: [], questions: new Map(), last: null, nextId: 1, inHistory: false };
}

export function addedSince(transcript, firstNewId) {
  const start = transcript.items.findLastIndex((item) => item.id < firstNewId) + 1;
  return transcript.items.slice(start);
}

function markSharingStart(transcript, event) {
  if (event?.history) transcript.inHistory = true;
  else if (transcript.inHistory) {
    transcript.inHistory = false;
    addNote(transcript, SHARED_FROM_HERE);
  }
}

function pushed(transcript, fields) {
  const item = { id: transcript.nextId, ...fields };
  transcript.nextId += 1;
  transcript.items.push(item);
  return { item, isNew: true };
}

export function addNote(transcript, text, problem = false) {
  return pushed(transcript, { kind: "note", text, problem }).item;
}

function lastItem(transcript, kind) {
  const item = transcript.items.at(-1);
  return item?.kind === kind ? item : null;
}

function addStep(transcript, step) {
  const group = lastItem(transcript, "tools");
  if (!group) return pushed(transcript, { kind: "tools", steps: [step] });
  group.steps.push(step);
  return { item: group, isNew: false };
}

function addResult(transcript, tool, text) {
  const group = lastItem(transcript, "tools");
  const open = group?.steps.filter((step) => step.output === null) ?? [];
  const waitingStep = open.findLast((candidate) => candidate.tool === tool) ?? open.at(-1);
  const change = waitingStep ? { item: group, isNew: false } : addStep(transcript, newStep(tool, ""));
  const step = waitingStep ?? change.item.steps.at(-1);
  step.output = text;
  step.failed = FAILED_RESULT.test(text);
  return change;
}

function rememberQuestion(transcript, requestId, text) {
  transcript.questions.delete(requestId);
  transcript.questions.set(requestId, String(text).split("\n")[0]);
  while (transcript.questions.size > MAX_REMEMBERED_QUESTIONS) transcript.questions.delete(transcript.questions.keys().next().value);
}

export function answeredLine(event, question) {
  const by = String(event?.by || THE_COMPUTER);
  const who = by === THE_COMPUTER ? "on the computer" : `by ${by}`;
  return `${event?.allow ? "Allowed" : "Denied"} ${who}${question ? `: ${question}` : ""}`;
}

function eventChange(transcript, event) {
  const text = String(event?.text ?? "");
  const tool = String(event?.tool ?? "");
  switch (event?.type) {
    case "user": return pushed(transcript, { kind: "user", who: ON_THE_COMPUTER, text });
    case "remote_message": return pushed(transcript, { kind: "user", who: String(event.by || "Someone"), text });
    case "assistant": {
      const reply = lastItem(transcript, "assistant");
      if (!reply) return pushed(transcript, { kind: "assistant", text });
      reply.text += text;
      return { item: reply, isNew: false };
    }
    case "tool_call": return addStep(transcript, newStep(tool, text));
    case "tool_result": return addResult(transcript, tool, text);
    case "permission":
      if (event.request_id) rememberQuestion(transcript, String(event.request_id), text);
      return null;
    case "permission_answered":
      return pushed(transcript, { kind: "note", text: answeredLine(event, transcript.questions.get(String(event.request_id))), problem: false });
    case "compacted": return pushed(transcript, { kind: "compacted", text });
    case "status": return text ? pushed(transcript, { kind: "note", text, problem: false }) : null;
    case "error": return pushed(transcript, { kind: "note", text: text || "Something went wrong on the computer.", problem: true });
    case "end": return pushed(transcript, { kind: "note", text: SESSION_ENDED, problem: false });
    default: return null;
  }
}

const MOVES_ACTIVITY = new Set(["user", "remote_message", "assistant", "tool_call", "tool_result", "permission", "permission_answered", "error", "end"]);

export function addEvent(transcript, event) {
  markSharingStart(transcript, event);
  const change = eventChange(transcript, event);
  if (MOVES_ACTIVITY.has(event?.type) && !event.history) {
    transcript.last = { type: event.type, step: event.type === "tool_call" ? change.item.steps.at(-1) : null };
  }
  return change;
}

export function trimTranscript(transcript, keep = MAX_KEPT_ITEMS) {
  const extra = Math.max(transcript.items.length - keep, 0);
  transcript.items.splice(0, extra);
  return extra;
}

export function activityOf(transcript, { ended = false, waiting = 0, quietMs = 0 } = {}) {
  if (ended || transcript.last?.type === "end") return { phase: "ended", text: "This session has ended" };
  if (waiting === 1) return { phase: "waiting", text: "Waiting for you to allow or deny a step" };
  if (waiting > 1) return { phase: "waiting", text: `Waiting for you: ${waiting} requests to allow or deny` };
  const last = transcript.last;
  if (!last) return { phase: "idle", text: "Waiting for a message" };
  if (last.type === "tool_call") return { phase: "working", text: actionOf(last.step.tool).doing(last.step.target) };
  if (last.type === "error") return { phase: "finished", text: "Stopped. Your turn." };
  if (last.type !== "assistant") return { phase: "working", text: "Thinking" };
  return quietMs >= FINISHED_AFTER_MS ? { phase: "finished", text: "Finished. Your turn." } : { phase: "working", text: "Writing a reply" };
}

export function isNearBottom(metrics) {
  const { scrollTop, scrollHeight, clientHeight } = metrics ?? {};
  if (![scrollTop, scrollHeight, clientHeight].every(Number.isFinite)) return true;
  return scrollHeight - scrollTop - clientHeight <= NEAR_BOTTOM_PX;
}

function inlineSpans(text) {
  const spans = [];
  const addText = (value) => {
    if (!value) return;
    const previous = spans.at(-1);
    if (previous?.type === "text") previous.text += value;
    else spans.push({ type: "text", text: value });
  };
  let last = 0;
  for (const match of text.matchAll(INLINE)) {
    const [whole, code, strong, label, href, bare, before, em] = match;
    addText(text.slice(last, match.index));
    last = match.index + whole.length;
    if (code !== undefined) spans.push({ type: "code", text: code });
    else if (strong !== undefined) spans.push({ type: "strong", text: strong });
    else if (label !== undefined && WEB_ADDRESS.test(href)) spans.push({ type: "link", text: label, href });
    else if (label !== undefined) addText(whole);
    else if (bare !== undefined) {
      const trailing = bare.match(TRAILING_PUNCTUATION)?.[0] ?? "";
      const address = bare.slice(0, bare.length - trailing.length);
      spans.push({ type: "link", text: address, href: address });
      addText(trailing);
    } else {
      addText(before);
      spans.push({ type: "em", text: em });
    }
  }
  addText(text.slice(last));
  return spans;
}

function startsBlock(line) {
  return FENCE.test(line) || HEADING.test(line) || QUOTE.test(line) || BULLET.test(line) || NUMBERED.test(line);
}

function groupedLines(lines, start, pattern) {
  const found = [];
  let next = start;
  while (next < lines.length && pattern.test(lines[next])) found.push(inlineSpans(lines[next++].match(pattern)[1]));
  return { found, next };
}

export function markdownBlocks(markdown) {
  const lines = String(markdown ?? "").split(/\r?\n/);
  const blocks = [];
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    if (FENCE.test(line)) {
      const code = [];
      index += 1;
      while (index < lines.length && !FENCE.test(lines[index])) code.push(lines[index++]);
      index += 1;
      blocks.push({ type: "code", text: code.join("\n") });
      continue;
    }
    const heading = line.match(HEADING);
    if (heading) {
      blocks.push({ type: "heading", spans: inlineSpans(heading[1]) });
      index += 1;
      continue;
    }
    const list = [[QUOTE, null], [BULLET, false], [NUMBERED, true]].find(([pattern]) => pattern.test(line));
    if (list) {
      const { found, next } = groupedLines(lines, index, list[0]);
      blocks.push(list[1] === null ? { type: "quote", lines: found } : { type: "list", ordered: list[1], items: found });
      index = next;
      continue;
    }
    if (!line.trim()) {
      index += 1;
      continue;
    }
    const paragraph = [];
    while (index < lines.length && lines[index].trim() && !startsBlock(lines[index])) paragraph.push(inlineSpans(lines[index++]));
    blocks.push({ type: "paragraph", lines: paragraph });
  }
  return blocks;
}

export function permissionText(request) {
  const tool = request?.tool ? String(request.tool) : "";
  if (!tool) return request?.text ? String(request.text) : "Arty wants to use a tool";
  return `Arty wants to use ${tool}${request?.text ? `: ${request.text}` : ""}`;
}

export function permissionPrompt(request) {
  const [question, ...rest] = String(request?.text ?? "").split("\n");
  return { heading: permissionText({ tool: request?.tool, text: question }), preview: rest.join("\n").trim() };
}

export function openRequests(waiting, events) {
  const open = new Map((waiting ?? []).map((request) => [request.request_id, request]));
  for (const event of events ?? []) {
    if (event.type === "permission" && event.request_id) open.set(event.request_id, event);
    if (event.type === "permission_answered") open.delete(event.request_id);
  }
  return [...open.values()];
}

export const FIRST_POLL = Object.freeze({ after: 0, wait: 0 });

export function nextPoll(previous, read, waitSeconds) {
  return { after: Number(read?.next) || previous.after, wait: waitSeconds };
}

export function sender(post, field, onProblem) {
  let sending = false;
  return async () => {
    const text = field.value.trim();
    if (!text || sending) return;
    sending = true;
    field.value = "";
    try {
      await post(text);
    } catch (error) {
      if (!field.value) field.value = text;
      onProblem(error.message);
    } finally {
      sending = false;
    }
  };
}

export function isSessionId(value) {
  return SESSION_ID.test(String(value ?? ""));
}
