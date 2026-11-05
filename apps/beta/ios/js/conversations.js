export const TITLE_LENGTH = 40;
export const HISTORY_MESSAGES = 14;
export const HISTORY_STEP = 8;
export const MAX_CONVERSATIONS = 100;
export const DEFAULT_MODEL = "assistant-core";

const STORAGE_KEY = "arty.conversations";

export function recentWindow(messages, size, step, isUser) {
  if (size <= 0 || step <= 0) throw new RangeError("size and step must be positive");
  if (messages.length <= size) return messages;
  let start = Math.floor((messages.length - size) / step) * step;
  while (start < messages.length && !isUser(messages[start])) start += 1;
  return messages.slice(start);
}

export function historyToSend(messages) {
  const sendable = messages.filter((message) => !message.failed);
  return recentWindow(sendable, HISTORY_MESSAGES, HISTORY_STEP, (message) => message.role === "user")
    .map(({ role, content, attachments }) => ({ role, content, attachments: attachments ?? [] }));
}

export function titleFor(text, attachments = []) {
  const source = text.trim() || attachments[0]?.name || "New chat";
  return source.slice(0, TITLE_LENGTH);
}

export class ConversationStore {
  constructor(storage, { now = Date.now, newId = () => globalThis.crypto.randomUUID() } = {}) {
    this.storage = storage;
    this.now = now;
    this.newId = newId;
  }

  readAll() {
    try {
      const parsed = JSON.parse(this.storage.getItem(STORAGE_KEY) ?? "[]");
      return Array.isArray(parsed) ? parsed : [];
    } catch {
      return [];
    }
  }

  writeAll(conversations) {
    const newestFirst = [...conversations].sort((a, b) => b.updated - a.updated).slice(0, MAX_CONVERSATIONS);
    this.storage.setItem(STORAGE_KEY, JSON.stringify(newestFirst));
  }

  list() {
    return this.readAll().sort((a, b) => b.updated - a.updated);
  }

  get(id) {
    return this.readAll().find((conversation) => conversation.id === id) ?? null;
  }

  create(model = DEFAULT_MODEL) {
    return { id: this.newId(), title: "New chat", model, messages: [], updated: this.now() };
  }

  save(conversation) {
    const saved = { ...conversation, updated: this.now() };
    this.writeAll([saved, ...this.readAll().filter((existing) => existing.id !== saved.id)]);
    return saved;
  }

  remove(id) {
    this.writeAll(this.readAll().filter((conversation) => conversation.id !== id));
  }
}
