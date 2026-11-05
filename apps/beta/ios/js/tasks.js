export const RESULT_PREVIEW_CHARS = 160;
const ACTIVE_STATUSES = new Set(["queued", "running"]);

export function taskActions(task) {
  if (task.status === "waiting" && task.waiting?.message) {
    const answers = task.waiting.kind === "approval"
      ? [{ action: "approve", label: "Approve", primary: true }, { action: "deny", label: "Deny", primary: false }]
      : [{ action: "done", label: "Done", primary: true }];
    return [...answers, { action: "cancel", label: "Cancel task", primary: false }];
  }
  if (ACTIVE_STATUSES.has(task.status)) return [{ action: "cancel", label: "Cancel", primary: false }];
  return [];
}

export function resultPreview(result) {
  if (!result) return "";
  return result.length > RESULT_PREVIEW_CHARS ? `${result.slice(0, RESULT_PREVIEW_CHARS)}…` : result;
}

export function capitalised(word) {
  return word ? word[0].toUpperCase() + word.slice(1) : "";
}

export function approvalButtons(announcement, waitingTaskIds) {
  if (announcement.task_id == null || !waitingTaskIds.has(announcement.task_id)) return [];
  return (announcement.actions ?? []).map((action) => ({ action, label: capitalised(action) }));
}
