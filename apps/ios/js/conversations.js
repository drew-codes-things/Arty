export const TITLE_LENGTH = 40;
export const HISTORY_MESSAGES = 14;
export const HISTORY_STEP = 8;
export const MAX_CONVERSATIONS = 100;
export const DEFAULT_MODEL = "Arty";
export const RETIRED_MAIN_MODEL = "assistant-core";

export const CONVERSATIONS_KEY = "arty.conversations";
const ID_BYTES = 16;

export function randomId(source = globalThis.crypto) {
  if (typeof source.randomUUID === "function") return source.randomUUID();
  return [...source.getRandomValues(new Uint8Array(ID_BYTES))].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

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

function withCurrentModelName(conversation) {
  return conversation?.model === RETIRED_MAIN_MODEL ? { ...conversation, model: DEFAULT_MODEL } : conversation;
}

export class ConversationStore {
  constructor(storage, { now = Date.now, newId = randomId } = {}) {
    this.storage = storage;
    this.now = now;
    this.newId = newId;
  }

  readAll() {
    try {
      const parsed = JSON.parse(this.storage.getItem(CONVERSATIONS_KEY) ?? "[]");
      return Array.isArray(parsed) ? parsed.map(withCurrentModelName) : [];
    } catch {
      return [];
    }
  }

  writeAll(conversations) {
    const newestFirst = [...conversations].sort((a, b) => b.updated - a.updated).slice(0, MAX_CONVERSATIONS);
    this.storage.setItem(CONVERSATIONS_KEY, JSON.stringify(newestFirst));
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
