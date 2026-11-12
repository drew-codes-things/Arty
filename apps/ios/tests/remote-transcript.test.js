import assert from "node:assert/strict";
import { test } from "node:test";
import {
  FINISHED_AFTER_MS, MAX_KEPT_ITEMS, activityOf, addEvent, addNote, answeredLine, diffTone, isNearBottom, markdownBlocks,
  newTranscript, permissionPrompt, readableInput, sessionDetails, stepLine, toolSummary, trimTranscript,
  SHARED_FROM_HERE, addedSince, sessionLine, sessionTitle,
} from "../js/remote.js";

function fed(...events) {
  const transcript = newTranscript();
  events.forEach((event, index) => addEvent(transcript, { seq: index + 1, ...event }));
  return transcript;
}

function call(tool, input) {
  return { type: "tool_call", tool, text: typeof input === "string" ? input : JSON.stringify(input) };
}

test("people's messages are right-hand bubbles naming who sent them", () => {
  const { items } = fed({ type: "user", text: "fix it" }, { type: "remote_message", text: "run tests", by: "Phone" });
  assert.deepEqual(items.map(({ kind, who, text }) => ({ kind, who, text })),
    [{ kind: "user", who: "On the computer", text: "fix it" }, { kind: "user", who: "Phone", text: "run tests" }]);
});

test("a reply streamed in pieces is one message", () => {
  const transcript = newTranscript();
  const first = addEvent(transcript, { type: "assistant", text: "All " });
  const second = addEvent(transcript, { type: "assistant", text: "green." });
  assert.equal(first.isNew, true);
  assert.equal(second.isNew, false);
  assert.equal(second.item, first.item);
  assert.deepEqual(transcript.items.map(({ kind, text }) => ({ kind, text })), [{ kind: "assistant", text: "All green." }]);
});

test("tool steps in a row are one quiet group, each with what it was given and what came back", () => {
  const { items } = fed(call("read_file", { path: "src/app.py" }), { type: "tool_result", tool: "read_file", text: "1\tprint()" },
    call("search", { pattern: "TODO" }), { type: "tool_result", tool: "search", text: "Error: bad pattern" },
    { type: "assistant", text: "Found it." }, call("run_command", { command: "pytest -q" }));
  assert.deepEqual(items.map((item) => item.kind), ["tools", "assistant", "tools"]);
  assert.deepEqual(items[0].steps.map(({ tool, target, output, failed }) => ({ tool, target, output, failed })), [
    { tool: "read_file", target: "app.py", output: "1\tprint()", failed: false },
    { tool: "search", target: "TODO", output: "Error: bad pattern", failed: true }]);
  assert.equal(items[2].steps[0].output, null);
});

test("a result with no call before it still shows", () => {
  const { items } = fed({ type: "tool_result", tool: "run_command", text: "ok" });
  assert.equal(items[0].kind, "tools");
  assert.equal(items[0].steps[0].output, "ok");
});

test("tool groups are summed up in plain words", () => {
  const steps = (...calls) => fed(...calls).items[0].steps;
  assert.equal(toolSummary(steps(call("run_command", { command: "ls" }))), "Ran a command");
  assert.equal(toolSummary(steps(call("edit_file", { path: "a/app.py" }))), "Edited app.py");
  assert.equal(toolSummary(steps(call("read_file", { path: "a.py" }), call("read_file", { path: "b.py" }), call("read_file", { path: "a.py" }),
    call("search", { pattern: "x" }))), "Read 2 files, searched the project");
  assert.equal(toolSummary(steps(call("run_command", { command: "a" }), call("run_command", { command: "b" }))), "Ran 2 commands");
  assert.equal(toolSummary(steps(call("mcp__github__open_issue", {}))), "Used open_issue from github");
  assert.equal(toolSummary(steps(call("something_new", "plain words"))), "Used something_new");
});

test("each step has its own line and readable details, never raw JSON", () => {
  const [command, edit, other] = fed(call("run_command", { command: "pytest -q" }),
    call("edit_file", { path: "app.py", old_text: "a = 1", new_text: "a = 2" }), call("todo_write", { items: ["one", "two"] })).items[0].steps;
  assert.equal(stepLine(command), "Ran a command: pytest -q");
  assert.equal(stepLine(edit), "Edited app.py");
  assert.equal(readableInput(command), "$ pytest -q");
  assert.equal(readableInput(edit), "path: app.py\n- a = 1\n+ a = 2");
  assert.equal(readableInput(other), "items: one, two");
});

test("changed lines are coloured as added, removed or a position marker", () => {
  assert.deepEqual(["+ new", "- old", "@@ -1 +1 @@", "--- a.py", "+++ a.py", "same"].map(diffTone),
    ["added", "removed", "marker", "", "", ""]);
});

test("an answered request becomes one line saying who answered, how and what it was", () => {
  const { items } = fed({ type: "permission", request_id: "r1", text: "Run command: pytest\n$ pytest" },
    { type: "permission_answered", request_id: "r1", allow: true, by: "Phone" },
    { type: "permission_answered", request_id: "r2", allow: false, by: "the computer" });
  assert.deepEqual(items.map(({ kind, text }) => ({ kind, text })), [
    { kind: "note", text: "Allowed by Phone: Run command: pytest" },
    { kind: "note", text: "Denied on the computer" }]);
  assert.equal(answeredLine({ allow: false }, ""), "Denied on the computer");
});

test("a request is shown as its question with the rest as a preview", () => {
  assert.deepEqual(permissionPrompt({ text: "Edit app.py?\n--- app.py\n+++ app.py\n+x" }),
    { heading: "Edit app.py?", preview: "--- app.py\n+++ app.py\n+x" });
  assert.deepEqual(permissionPrompt({ tool: "bash", text: "rm -rf build" }), { heading: "Arty wants to use bash: rm -rf build", preview: "" });
  assert.deepEqual(permissionPrompt({}), { heading: "Arty wants to use a tool", preview: "" });
});

test("status lines, problems and the end are notes", () => {
  const { items } = fed({ type: "status", text: "Mode: plan" }, { type: "status", mode: "plan" }, { type: "error", text: "Stopped by Phone." },
    { type: "end" }, { type: "something new" });
  assert.deepEqual(items.map(({ kind, text, problem }) => ({ kind, text, problem })), [
    { kind: "note", text: "Mode: plan", problem: false },
    { kind: "note", text: "Stopped by Phone.", problem: true },
    { kind: "note", text: "The session ended on the computer.", problem: false }]);
});

test("only the newest items are kept, and the caller learns how many went", () => {
  const transcript = newTranscript();
  for (let index = 0; index < MAX_KEPT_ITEMS + 3; index += 1) addEvent(transcript, { type: "user", text: `line ${index}` });
  assert.equal(trimTranscript(transcript), 3);
  assert.equal(transcript.items.length, MAX_KEPT_ITEMS);
  assert.equal(transcript.items[0].text, "line 3");
  assert.equal(trimTranscript(transcript), 0);
  const note = addNote(transcript, "Some lines were missed.");
  assert.equal(transcript.items.at(-1), note);
});

test("the activity line says what Arty is doing now, and when it is finished or waiting", () => {
  const activity = (events, options) => activityOf(fed(...events), options);
  assert.deepEqual(activity([]), { phase: "idle", text: "Waiting for a message" });
  assert.deepEqual(activity([{ type: "user", text: "go" }]), { phase: "working", text: "Thinking" });
  assert.deepEqual(activity([call("run_command", { command: "pytest -q" })]), { phase: "working", text: "Running pytest -q" });
  assert.deepEqual(activity([call("read_file", { path: "x/app.py" })]), { phase: "working", text: "Reading app.py" });
  assert.deepEqual(activity([{ type: "tool_result", tool: "read_file", text: "" }]), { phase: "working", text: "Thinking" });
  assert.deepEqual(activity([{ type: "assistant", text: "Hi" }], { quietMs: 0 }), { phase: "working", text: "Writing a reply" });
  assert.deepEqual(activity([{ type: "assistant", text: "Hi" }], { quietMs: FINISHED_AFTER_MS }), { phase: "finished", text: "Finished. Your turn." });
  assert.deepEqual(activity([{ type: "assistant", text: "Hi" }, { type: "status", text: "Mode: plan" }], { quietMs: FINISHED_AFTER_MS }).phase, "finished");
  assert.deepEqual(activity([{ type: "error", text: "Stopped." }]), { phase: "finished", text: "Stopped. Your turn." });
  assert.deepEqual(activity([{ type: "user", text: "go" }], { waiting: 2 }), { phase: "waiting", text: "Waiting for you: 2 requests to allow or deny" });
  assert.deepEqual(activity([{ type: "user", text: "go" }], { waiting: 1 }), { phase: "waiting", text: "Waiting for you to allow or deny a step" });
  assert.deepEqual(activity([{ type: "user", text: "go" }], { ended: true }), { phase: "ended", text: "This session has ended" });
});

test("the view only follows new lines when it is already at the bottom", () => {
  assert.equal(isNearBottom({ scrollTop: 552, scrollHeight: 1000, clientHeight: 400 }), true);
  assert.equal(isNearBottom({ scrollTop: 100, scrollHeight: 1000, clientHeight: 400 }), false);
  assert.equal(isNearBottom({}), true);
  assert.equal(isNearBottom(null), true);
});

test("the header details name the folder, model and mode", () => {
  assert.equal(sessionDetails({ folder: "arty-cli", model: "arty/qwen3:14b", mode: "plan" }), "arty-cli · arty/qwen3:14b · plan mode");
  assert.equal(sessionDetails({}), "default mode");
});

test("markdown becomes a safe tree of text: html stays text and only web links are links", () => {
  const blocks = markdownBlocks("# Done\n\n<img src=x onerror=alert(1)> **bold** and `code`\n[bad](javascript:alert(1)) [good](https://example.com).\n\n- one\n- two\n1. first\n\n```\n<script>x</script>\n```\n> quoted");
  assert.deepEqual(blocks, [
    { type: "heading", spans: [{ type: "text", text: "Done" }] },
    { type: "paragraph", lines: [
      [{ type: "text", text: "<img src=x onerror=alert(1)> " }, { type: "strong", text: "bold" }, { type: "text", text: " and " }, { type: "code", text: "code" }],
      [{ type: "text", text: "[bad](javascript:alert(1)) " }, { type: "link", text: "good", href: "https://example.com" }, { type: "text", text: "." }]] },
    { type: "list", ordered: false, items: [[{ type: "text", text: "one" }], [{ type: "text", text: "two" }]] },
    { type: "list", ordered: true, items: [[{ type: "text", text: "first" }]] },
    { type: "code", text: "<script>x</script>" },
    { type: "quote", lines: [[{ type: "text", text: "quoted" }]] }]);
});

test("bare web addresses become links without the full stop, and emphasis needs a word edge", () => {
  assert.deepEqual(markdownBlocks("see https://example.com/a. 2*3*4 and *this*")[0].lines[0], [
    { type: "text", text: "see " }, { type: "link", text: "https://example.com/a", href: "https://example.com/a" },
    { type: "text", text: ". 2*3*4 and " }, { type: "em", text: "this" }]);
  assert.deepEqual(markdownBlocks(""), []);
});

test("a terminal is listed by its own title, with the computer beside it and no repeated folder", () => {
  const named = { title: "greeter 2", name: "VivaDrew", folder: "greeter", model: "arty/qwen3:14b", mode: "plan" };
  assert.equal(sessionTitle(named), "greeter 2");
  assert.equal(sessionLine(named), "greeter 2 · VivaDrew · arty/qwen3:14b · plan mode");
  assert.equal(sessionLine({ name: "VivaDrew", folder: "greeter" }), "VivaDrew · greeter · default mode");
});

test("the conversation from before sharing shows in full, then one line marks where sharing began", () => {
  const transcript = fed(
    { type: "user", text: "fix the greet test", history: true },
    { type: "assistant", text: "Fixed.", history: true },
    { type: "user", text: "now the docs" },
  );
  assert.deepEqual(transcript.items.map((item) => item.kind), ["user", "assistant", "note", "user"]);
  assert.equal(transcript.items[2].text, SHARED_FROM_HERE);
  assert.deepEqual(addedSince(transcript, 3).map((item) => item.kind), ["note", "user"]);
});

test("earlier conversation does not count as activity", () => {
  const transcript = fed({ type: "user", text: "fix it", history: true }, { type: "tool_call", tool: "read_file", text: "{}", history: true });
  assert.deepEqual(activityOf(transcript), { phase: "idle", text: "Waiting for a message" });
});

test("a compacted conversation shows as a marker holding the summary and the chat carries on", () => {
  const transcript = fed({ type: "assistant", text: "Working" }, { type: "compacted", text: "We fixed greet()." }, { type: "assistant", text: "Carrying on." });
  assert.deepEqual(transcript.items.map((item) => item.kind), ["assistant", "compacted", "assistant"]);
  assert.equal(transcript.items[1].text, "We fixed greet().");
  assert.ok(MAX_KEPT_ITEMS >= 2000);
});
