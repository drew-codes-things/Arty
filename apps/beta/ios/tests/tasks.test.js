import assert from "node:assert/strict";
import { test } from "node:test";
import { RESULT_PREVIEW_CHARS, approvalButtons, capitalised, resultPreview, taskActions } from "../js/tasks.js";

const labels = (task) => taskActions(task).map((a) => a.action);

test("tasks get the same buttons as in the phone app", () => {
  assert.deepEqual(labels({ status: "waiting", waiting: { kind: "approval", message: "Delete 3 old backups?" } }), ["approve", "deny", "cancel"]);
  assert.deepEqual(labels({ status: "waiting", waiting: { kind: "user", message: "Plug the drive in" } }), ["done", "cancel"]);
  assert.deepEqual(labels({ status: "running" }), ["cancel"]);
  assert.deepEqual(labels({ status: "queued" }), ["cancel"]);
  assert.deepEqual(labels({ status: "done", result: "ok" }), []);
  assert.deepEqual(labels({ status: "waiting", waiting: null }), []);
});

test("long results are previewed", () => {
  assert.equal(resultPreview("short"), "short");
  assert.equal(resultPreview("x".repeat(RESULT_PREVIEW_CHARS + 1)), `${"x".repeat(RESULT_PREVIEW_CHARS)}…`);
  assert.equal(resultPreview(null), "");
  assert.equal(capitalised("waiting"), "Waiting");
  assert.equal(capitalised(""), "");
});

test("approval buttons only show while the task still waits", () => {
  const update = { kind: "approval", task_id: 4, actions: ["approve", "deny"] };
  assert.deepEqual(approvalButtons(update, new Set([4])), [{ action: "approve", label: "Approve" }, { action: "deny", label: "Deny" }]);
  assert.deepEqual(approvalButtons(update, new Set([5])), []);
  assert.deepEqual(approvalButtons({ kind: "reminder", task_id: null, actions: [] }, new Set([4])), []);
});
