export const MAX_UPLOAD_BYTES = 95 * 1024 * 1024;
const ARTY_FILE_PATH = /\/arty-files\/[A-Za-z0-9_-]+\/[A-Za-z0-9_-]+\/[^/?#]+$/;
export const CLIENT_NAME = "ios";
export const POLL_MS = 1500;
export const GIVE_UP_MS = 20 * 60 * 1000;

const REQUEST_TIMEOUT_MS = 45000;
const UPLOAD_TIMEOUT_MS = 300000;
const ANNOUNCEMENT_GRACE_SECONDS = 15;
const MAX_ANNOUNCEMENT_WAIT_SECONDS = 55;
const BYTES_PER_MEGABYTE = 1024 * 1024;
const NOT_FOUND = 404;
const NETWORK_FAILURE = 0;
const REFUSED_BY_CLOUDFLARE = 403;

export const RESTARTED_MESSAGE = "Arty restarted before answering. Tap retry to ask again.";
export const GAVE_UP_MESSAGE = "No answer after 20 minutes. Tap retry to ask again.";

export class ArtyError extends Error {
  constructor(status, message) {
    super(message);
    this.name = "ArtyError";
    this.status = status;
  }
}

export function artyFilePath(link) {
  const found = String(link ?? "").split(/[?#]/)[0].match(ARTY_FILE_PATH);
  return found ? found[0] : null;
}

export function messageContent(message) {
  const attachments = message.attachments ?? [];
  if (attachments.length === 0) return message.content;
  const parts = message.content.trim() ? [{ type: "text", text: message.content }] : [];
  for (const file of attachments) parts.push({ type: "file", file: { file_id: file.fileId, filename: file.name } });
  return parts;
}

export function errorMessage(status, text) {
  const fromBrain = brainErrorText(text);
  if (fromBrain) return fromBrain;
  if (status === 401 || status === 403) return "Access refused. The app's keys may have been changed.";
  if (status >= 502 && status <= 530) return "Arty's server isn't answering. The machine running its brain may be off.";
  return `Request failed (HTTP ${status}).`;
}

function brainErrorText(text) {
  try {
    const parsed = JSON.parse(text);
    const message = parsed?.error?.message || parsed?.message;
    return typeof message === "string" && message.trim() ? message : null;
  } catch {
    return null;
  }
}

function realSleep(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

export class ArtyApi {
  constructor(settings, { base = "", fetchImpl = (...args) => globalThis.fetch(...args) } = {}) {
    this.settings = settings;
    this.base = base.replace(/\/+$/, "");
    this.fetchImpl = fetchImpl;
  }

  authHeaders() {
    const headers = { Authorization: `Bearer ${this.settings.apiKey}` };
    if (this.settings.accessId) {
      headers["CF-Access-Client-Id"] = this.settings.accessId;
      headers["CF-Access-Client-Secret"] = this.settings.accessSecret;
    }
    return headers;
  }

  async exchange(method, path, { json, body, headers = {}, timeoutMs = REQUEST_TIMEOUT_MS } = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    const sent = { ...this.authHeaders(), Accept: "application/json", ...headers };
    let payload = body;
    if (json !== undefined) {
      sent["Content-Type"] = "application/json";
      payload = JSON.stringify(json);
    }
    let response;
    try {
      response = await this.fetchImpl(this.base + path, {
        method, headers: sent, body: payload, signal: controller.signal, redirect: "manual", cache: "no-store",
      });
    } catch (error) {
      throw new ArtyError(NETWORK_FAILURE, `Can't reach Arty (${error?.message || "no connection"}). Check your connection.`);
    } finally {
      clearTimeout(timer);
    }
    if (response.type === "opaqueredirect" || (response.status >= 300 && response.status < 400)) {
      throw new ArtyError(REFUSED_BY_CLOUDFLARE, "Cloudflare refused the access id or secret. Check them in Connection.");
    }
    if (response.status >= 400) {
      const text = await response.text().catch(() => "");
      throw new ArtyError(response.status, errorMessage(response.status, text));
    }
    return response;
  }

  async request(method, path, json, timeoutMs) {
    const response = await this.exchange(method, path, { json, timeoutMs });
    const text = await response.text();
    if (!text.trim()) return {};
    try {
      return JSON.parse(text);
    } catch {
      throw new ArtyError(-1, "Arty sent a reply this app doesn't understand.");
    }
  }

  async models() {
    const reply = await this.request("GET", "/v1/models");
    return (reply.data ?? []).map((model) => ({
      id: model.id, name: model.name || model.id, model: model.model || "", description: model.description || "",
    }));
  }

  async startChat(messages, model) {
    const history = messages.map((message) => ({ role: message.role, content: messageContent(message) }));
    const reply = await this.request("POST", "/v1/chat/jobs", { model, messages: history, client: CLIENT_NAME });
    return reply.id;
  }

  async chatJob(id) {
    const job = await this.request("GET", `/v1/chat/jobs/${encodeURIComponent(id)}`);
    return {
      id, status: job.status, answer: job.answer ?? null, toolsUsed: job.tools_used ?? [],
      error: job.error ?? null, partial: job.partial ?? null,
    };
  }

  async waitForAnswer(id, { onPartial = () => {}, isCancelled = () => false, sleep = realSleep, now = Date.now } = {}) {
    const started = now();
    while (now() - started < GIVE_UP_MS) {
      if (isCancelled()) return { cancelled: true };
      let job = null;
      try {
        job = await this.chatJob(id);
      } catch (error) {
        if (error.status === NOT_FOUND) return { failed: true, text: RESTARTED_MESSAGE };
      }
      if (job && job.status === "done") return { failed: false, text: job.answer ?? "", toolsUsed: job.toolsUsed };
      if (job && job.status !== "running") return { failed: true, text: `Arty hit an error: ${job.error}` };
      if (job?.partial) onPartial(job.partial);
      await sleep(POLL_MS);
    }
    return { failed: true, text: GAVE_UP_MESSAGE };
  }

  async upload(file) {
    if (file.size > MAX_UPLOAD_BYTES) {
      throw new ArtyError(413, `That file is too big: Arty takes up to ${MAX_UPLOAD_BYTES / BYTES_PER_MEGABYTE} MB.`);
    }
    const response = await this.exchange("POST", "/v1/files", {
      body: file,
      headers: { "Content-Type": file.type || "application/octet-stream", "X-Filename": encodeURIComponent(file.name) },
      timeoutMs: UPLOAD_TIMEOUT_MS,
    });
    const reply = await response.json();
    return {
      fileId: reply.id, name: reply.name || file.name, mime: reply.mime || file.type,
      kind: reply.kind || "other", size: reply.size ?? file.size,
    };
  }

  async download(link) {
    const path = artyFilePath(link);
    if (!path) throw new ArtyError(-1, "That isn't a file from Arty.");
    try {
      const response = await this.exchange("GET", path, { headers: { Accept: "*/*" }, timeoutMs: UPLOAD_TIMEOUT_MS });
      return await response.blob();
    } catch (error) {
      if (error.status === NOT_FOUND) throw new ArtyError(NOT_FOUND, "That file has expired: Arty keeps files for 24 hours.");
      throw error;
    }
  }

  async announcements(after, waitSeconds = 0) {
    const wait = Math.min(Math.max(waitSeconds, 0), MAX_ANNOUNCEMENT_WAIT_SECONDS);
    const reply = await this.request("GET", `/v1/announcements?after=${after}&wait=${wait}`, undefined,
      (wait + ANNOUNCEMENT_GRACE_SECONDS) * 1000);
    return { announcements: reply.announcements ?? [], last: reply.last ?? after };
  }

  async calendar(days = 30) {
    return (await this.request("GET", `/v1/calendar?days=${days}`)).events ?? [];
  }

  async addEvent(title, when, location = "") {
    return (await this.request("POST", "/v1/calendar", { title, when, location })).message ?? "";
  }

  async deleteEvent(id) {
    await this.request("DELETE", `/v1/calendar/${id}`);
  }

  async tasks(limit = 50) {
    return (await this.request("GET", `/v1/tasks?limit=${limit}`)).tasks ?? [];
  }

  async createTask(instructions, title = "") {
    return this.request("POST", "/v1/tasks", { instructions, title });
  }

  async decideTask(id, action, note = "") {
    return (await this.request("POST", `/v1/tasks/${id}/${encodeURIComponent(action)}`, { message: note })).message ?? "";
  }

  async schedules() {
    return (await this.request("GET", "/v1/schedules")).schedules ?? [];
  }

  async createSchedule(instructions, when, title = "") {
    return (await this.request("POST", "/v1/schedules", { instructions, when, title })).message ?? "";
  }

  async cancelSchedule(id) {
    await this.request("DELETE", `/v1/schedules/${id}`);
  }

  async memory() {
    return (await this.request("GET", "/v1/memory")).entities ?? [];
  }

  async lectures() {
    return (await this.request("GET", "/v1/lectures")).lectures ?? [];
  }

  async lecture(id) {
    return this.request("GET", `/v1/lectures/${id}`);
  }

  async startLecture(fileId, title) {
    return this.request("POST", "/v1/lectures", { file_id: fileId, title });
  }
}
