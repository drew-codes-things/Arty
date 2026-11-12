import { ConversationStore, historyToSend, randomId, titleFor } from "./conversations.js";
import { h, showToast } from "./dom.js";
import { fileNameFromPath, mediaKind } from "./links.js";
import { renderMarkdown, speakableText } from "./markdown.js";
import { phoneLink, splitPhoneAction } from "./phone.js";
import { loadPreferences, savePreferences } from "./settings.js";

const THINKING_TICK_MS = 1000;
const SPEECH_LANGUAGE = "en-GB";
const MAX_COMPOSER_HEIGHT = 160;

const sendIcon = () => h("span", { "aria-hidden": "true" }, "↑");

export class ChatView {
  constructor({ api, storage, container, titleElement, onConversationsChanged }) {
    this.api = api;
    this.storage = storage;
    this.store = new ConversationStore(storage);
    this.titleElement = titleElement;
    this.onConversationsChanged = onConversationsChanged;
    this.pendingFiles = [];
    this.turn = null;
    this.models = [];
    this.tick = null;
    this.buildLayout(container);
  }

  buildLayout(container) {
    this.modelSelect = h("select", { id: "model", "aria-label": "Model", onchange: () => this.chooseModel(this.modelSelect.value) });
    this.messageList = h("div", { class: "messages", "aria-live": "polite" });
    this.fileInput = h("input", { type: "file", multiple: true, hidden: true, onchange: () => this.attach([...this.fileInput.files]) });
    this.textInput = h("textarea", { rows: "1", placeholder: "Message Arty", "aria-label": "Message", oninput: () => this.resizeComposer() });
    this.sendButton = h("button", { type: "submit", class: "send", "aria-label": "Send" }, sendIcon());
    this.pendingList = h("div", { class: "pending" });
    const attachButton = h("button", { type: "button", class: "icon-button", "aria-label": "Attach files", onclick: () => this.fileInput.click() },
      h("span", { "aria-hidden": "true" }, "+"));
    const composer = h("form", { class: "composer" }, this.pendingList, h("div", { class: "composer-row" }, attachButton, this.textInput, this.sendButton), this.fileInput);
    composer.addEventListener("submit", (event) => {
      event.preventDefault();
      this.send();
    });
    container.replaceChildren(h("div", { class: "model-row" }, h("label", { for: "model" }, "Model"), this.modelSelect), this.messageList, composer);
  }

  start() {
    const { lastChat } = loadPreferences(this.storage);
    this.current = (lastChat && this.store.get(lastChat)) || this.store.create();
    this.render();
    this.loadModels();
    this.resumePendingTurn();
  }

  async loadModels() {
    try {
      this.models = await this.api.models();
    } catch (error) {
      showToast(`Couldn't load the models: ${error.message}`);
    }
    this.renderModels();
  }

  renderModels() {
    const options = this.models.map((model) => h("option", { value: model.id }, model.name));
    if (!this.models.some((model) => model.id === this.current.model)) {
      options.unshift(h("option", { value: this.current.model }, `${this.current.model}${this.models.length ? " (not available)" : ""}`));
    }
    this.modelSelect.replaceChildren(...options);
    this.modelSelect.value = this.current.model;
  }

  chooseModel(model) {
    this.current = { ...this.current, model };
    if (this.current.messages.length) this.current = this.store.save(this.current);
  }

  conversations() {
    return this.store.list();
  }

  open(id) {
    const found = this.store.get(id);
    if (!found) return;
    this.current = found;
    savePreferences(this.storage, { lastChat: id });
    this.render();
  }

  newChat() {
    this.current = this.store.create(this.current?.model);
    this.render();
  }

  remove(id) {
    this.store.remove(id);
    if (this.current.id === id) this.newChat();
    this.onConversationsChanged();
  }

  get busy() {
    return this.turn !== null || this.pendingFiles.some((file) => file.uploading);
  }

  attach(files) {
    this.fileInput.value = "";
    for (const file of files) {
      const pending = { key: randomId(), name: file.name, uploading: true };
      this.pendingFiles.push(pending);
      this.api.upload(file)
        .then((attachment) => Object.assign(pending, { attachment, uploading: false }))
        .catch((error) => Object.assign(pending, { error: error.message, uploading: false }))
        .finally(() => this.renderPending());
    }
    this.renderPending();
  }

  renderPending() {
    this.pendingList.replaceChildren(...this.pendingFiles.map((file) => h("span", { class: `chip${file.error ? " error" : ""}` },
      file.uploading ? `Uploading ${file.name}...` : file.error ? `${file.name}: ${file.error}` : file.name,
      h("button", { type: "button", "aria-label": `Remove ${file.name}`, onclick: () => this.dropPending(file.key) }, "×"))));
    this.sendButton.disabled = this.busy;
  }

  dropPending(key) {
    this.pendingFiles = this.pendingFiles.filter((file) => file.key !== key);
    this.renderPending();
  }

  resizeComposer() {
    this.textInput.style.height = "auto";
    this.textInput.style.height = `${Math.min(this.textInput.scrollHeight, MAX_COMPOSER_HEIGHT)}px`;
  }

  send() {
    const text = this.textInput.value.trim();
    const attachments = this.pendingFiles.map((file) => file.attachment).filter(Boolean);
    if (this.busy || (!text && attachments.length === 0)) return;
    this.unlockSpeech();
    const title = this.current.messages.length ? this.current.title : titleFor(text, attachments);
    this.current = this.store.save({
      ...this.current, title, messages: [...this.current.messages, { role: "user", content: text, attachments, sent: Date.now() }],
    });
    savePreferences(this.storage, { lastChat: this.current.id });
    this.textInput.value = "";
    this.resizeComposer();
    this.pendingFiles = [];
    this.renderPending();
    this.onConversationsChanged();
    this.startTurn(this.current);
  }

  retry() {
    const messages = [...this.current.messages];
    if (messages.at(-1)?.failed) messages.pop();
    this.current = this.store.save({ ...this.current, messages });
    this.startTurn(this.current);
  }

  async startTurn(conversation) {
    this.beginTurn(conversation.id);
    try {
      const jobId = await this.api.startChat(historyToSend(conversation.messages), conversation.model);
      const stored = this.store.get(conversation.id);
      if (stored) this.store.save({ ...stored, pendingJobId: jobId });
      await this.awaitReply(conversation.id, jobId);
    } catch (error) {
      this.finishTurn(conversation.id, { role: "assistant", content: error.message, failed: true });
    }
  }

  beginTurn(conversationId) {
    this.turn = { conversationId, partial: "", started: Date.now() };
    clearInterval(this.tick);
    this.tick = setInterval(() => this.updateElapsed(), THINKING_TICK_MS);
    this.render();
  }

  async awaitReply(conversationId, jobId) {
    const result = await this.api.waitForAnswer(jobId, {
      onPartial: (partial) => {
        this.turn.partial = partial;
        if (this.current.id === conversationId) this.render();
      },
    });
    const { text, action } = result.failed ? { text: result.text, action: null } : splitPhoneAction(result.text);
    this.finishTurn(conversationId, { role: "assistant", content: text, failed: result.failed, tools: result.toolsUsed ?? [], phone: action });
  }

  resumePendingTurn() {
    const waiting = this.store.list().find((conversation) => conversation.pendingJobId);
    if (!waiting || this.turn) return;
    this.beginTurn(waiting.id);
    this.awaitReply(waiting.id, waiting.pendingJobId);
  }

  finishTurn(conversationId, reply) {
    clearInterval(this.tick);
    this.turn = null;
    const stored = this.store.get(conversationId);
    if (!stored) return this.render();
    const updated = this.store.save({ ...stored, messages: [...stored.messages, { ...reply, sent: Date.now() }], pendingJobId: null });
    if (this.current.id === conversationId) this.current = updated;
    this.onConversationsChanged();
    this.render();
    if (!reply.failed && loadPreferences(this.storage).readAloud) this.speak(reply.content);
  }

  updateElapsed() {
    const label = this.messageList.querySelector(".thinking .elapsed");
    if (label && this.turn) label.textContent = `Thinking... ${Math.round((Date.now() - this.turn.started) / 1000)}s`;
  }

  render() {
    this.titleElement.textContent = this.current.messages.length ? this.current.title : "Arty";
    this.renderModels();
    const showingTurn = this.turn?.conversationId === this.current.id;
    const items = this.current.messages.map((message, index) => this.messageElement(message, index === this.current.messages.length - 1));
    if (showingTurn) items.push(this.thinkingElement());
    if (items.length === 0) items.push(this.emptyState());
    this.messageList.replaceChildren(...items);
    this.sendButton.disabled = this.busy;
    this.updateElapsed();
    requestAnimationFrame(() => { this.messageList.scrollTop = this.messageList.scrollHeight; });
  }

  emptyState() {
    return h("div", { class: "empty" }, h("img", { src: "icons/icon-180.png", alt: "" }),
      h("p", {}, "Ask Arty anything. Tap the microphone on the keyboard to talk instead of typing."));
  }

  thinkingElement() {
    const partial = h("div", { class: "bubble" });
    partial.innerHTML = renderMarkdown(splitPhoneAction(this.turn.partial).text);
    return h("div", { class: "thinking" },
      h("span", { class: "dots", "aria-hidden": "true" }, h("i"), h("i"), h("i")),
      h("span", { class: "elapsed" }, "Thinking..."),
      this.turn.partial ? partial : null);
  }

  messageElement(message, isLast) {
    if (message.role === "user") {
      return h("div", { class: "message user" },
        message.content ? h("div", { class: "bubble" }, message.content) : null,
        message.attachments?.length ? h("div", { class: "attachment-list" }, message.attachments.map((file) => h("span", { class: "chip" }, file.name))) : null);
    }
    const bubble = h("div", { class: "bubble" });
    bubble.innerHTML = renderMarkdown(message.content);
    this.hydrateFiles(bubble);
    const actions = [
      h("button", { type: "button", class: "link-button", onclick: () => this.speak(message.content) }, "Read aloud"),
      h("button", { type: "button", class: "link-button", onclick: () => this.copy(message.content) }, "Copy"),
    ];
    if (message.failed && isLast && !this.turn) actions.push(h("button", { type: "button", class: "link-button accent", onclick: () => this.retry() }, "Retry"));
    return h("div", { class: `message assistant${message.failed ? " failed" : ""}` },
      bubble,
      message.phone ? this.phoneActionElement(message.phone) : null,
      message.tools?.length ? h("div", { class: "message-tools" }, message.tools.map((tool) => h("span", { class: "chip" }, tool))) : null,
      h("div", { class: "message-actions" }, actions));
  }

  phoneActionElement(action) {
    const link = phoneLink(action);
    if (!link) return h("p", { class: "notice" }, `This app can't look up contacts: open Phone to call ${action.who}.`);
    const label = action.kind === "call" ? `Call ${action.who}` : `Text ${action.who}`;
    return h("div", { class: "message-actions" }, h("a", { class: "link-button accent", href: link }, label));
  }

  hydrateFiles(element) {
    for (const link of element.querySelectorAll("a.arty-file")) {
      link.addEventListener("click", (event) => {
        event.preventDefault();
        this.openFile(link.dataset.path, link);
      });
    }
    for (const image of element.querySelectorAll("img.arty-file-image")) {
      this.api.download(image.dataset.path)
        .then((blob) => { image.src = URL.createObjectURL(blob); })
        .catch((error) => image.replaceWith(h("span", { class: "error-text" }, error.message)));
    }
  }

  async openFile(path, link) {
    const name = fileNameFromPath(path);
    let blob;
    try {
      blob = await this.api.download(path);
    } catch (error) {
      return showToast(error.message);
    }
    const url = URL.createObjectURL(blob);
    const kind = mediaKind(path, blob.type.startsWith("image/"));
    if (kind === "picture") return link.replaceWith(h("img", { src: url, alt: name }));
    if (kind === "video") return link.replaceWith(h("video", { src: url, controls: true, playsinline: true }));
    const file = new File([blob], name, { type: blob.type });
    try {
      if (navigator.canShare?.({ files: [file] })) return await navigator.share({ files: [file] });
    } catch (error) {
      if (error.name === "AbortError") return undefined;
    }
    h("a", { href: url, download: name }).click();
    return undefined;
  }

  async copy(text) {
    try {
      await navigator.clipboard.writeText(text);
      showToast("Copied");
    } catch (error) {
      showToast(`Couldn't copy: ${error.message}`);
    }
  }

  unlockSpeech() {
    if (!loadPreferences(this.storage).readAloud || !("speechSynthesis" in window)) return;
    window.speechSynthesis.speak(new SpeechSynthesisUtterance(""));
  }

  speak(markdown) {
    if (!("speechSynthesis" in window)) return showToast("This browser can't read aloud.");
    const utterance = new SpeechSynthesisUtterance(speakableText(markdown));
    utterance.lang = SPEECH_LANGUAGE;
    utterance.voice = window.speechSynthesis.getVoices().find((voice) => voice.lang === SPEECH_LANGUAGE) ?? null;
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(utterance);
    return undefined;
  }
}
