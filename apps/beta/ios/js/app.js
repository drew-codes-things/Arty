import { ChatView } from "./chat.js";
import { formatMoment, h, sectionHead, twoStepButton } from "./dom.js";
import { renderCalendar, renderLectures, renderMemory, renderSchedules, renderTasks } from "./manage.js";
import { renderSetup } from "./setup.js";
import { ArtyApi } from "./api.js";
import { forgetSettings, loadPreferences, loadSettings, savePreferences } from "./settings.js";
import { UpdatesFeed } from "./updates.js";

const storage = window.localStorage;
const byId = (id) => document.getElementById(id);
const VIEWS = ["chat", "updates", "tasks", "calendar", "more"];
const MORE_PAGES = { schedules: renderSchedules, lectures: renderLectures, memory: renderMemory };

const state = { api: null, chat: null, updates: null, view: "chat" };

function showView(name) {
  state.view = name;
  for (const view of VIEWS) byId(`view-${view}`).hidden = view !== name;
  for (const tab of document.querySelectorAll(".tab")) {
    if (tab.dataset.view === name) tab.setAttribute("aria-current", "page");
    else tab.removeAttribute("aria-current");
  }
  const container = byId(`view-${name}`);
  if (name !== "chat") byId("title").textContent = container.dataset.title ?? "Arty";
  if (name === "chat") state.chat.render();
  if (name === "updates") state.updates.render();
  if (name === "tasks") renderTasks(container, state.api);
  if (name === "calendar") renderCalendar(container, state.api);
  if (name === "more") renderMore(container);
}

function renderMore(container) {
  const page = (key, label) => h("li", {}, h("button", { type: "button", onclick: () => openMorePage(container, key, label) }, label));
  const readAloud = h("input", { type: "checkbox", checked: loadPreferences(storage).readAloud,
    onchange: () => savePreferences(storage, { readAloud: readAloud.checked }) });
  const settings = loadSettings(storage);
  container.replaceChildren(
    sectionHead("More"),
    h("ul", { class: "menu" }, page("schedules", "Schedules"), page("lectures", "Lectures"), page("memory", "Memory")),
    h("h3", { class: "kicker" }, "This iPhone"),
    h("label", { class: "toggle" }, h("span", {}, "Read Arty's replies aloud"), readAloud),
    h("p", { class: "notice" }, settings?.accessId ? "Connected with the app key and the Cloudflare access token." : "Connected with the app key only (home network)."),
    twoStepButton("Forget the keys on this iPhone", "Tap again to forget them", async () => {
      forgetSettings(storage);
      window.location.reload();
    }, "button"),
  );
}

function openMorePage(container, key, label) {
  byId("title").textContent = label;
  const back = h("button", { type: "button", class: "link-button accent", onclick: () => showView("more") }, "‹ More");
  const page = h("div");
  container.replaceChildren(back, page);
  MORE_PAGES[key](page, state.api);
}

function renderChatList() {
  const current = state.chat.current?.id;
  const items = state.chat.conversations().map((conversation) => h("li", { class: conversation.id === current ? "current" : "" },
    h("button", { type: "button", class: "open-chat", onclick: () => {
      state.chat.open(conversation.id);
      closeDrawer();
      showView("chat");
    } }, h("span", {}, conversation.title), h("small", {}, formatMoment(new Date(conversation.updated)))),
    twoStepButton("Delete", "Delete?", async () => {
      state.chat.remove(conversation.id);
      renderChatList();
    }, "delete-chat")));
  byId("chat-list").replaceChildren(...(items.length ? items : [h("li", {}, h("p", { class: "notice" }, "No chats yet."))]));
}

function openDrawer() {
  renderChatList();
  byId("drawer").hidden = false;
}

function closeDrawer() {
  byId("drawer").hidden = true;
}

function startApp(api) {
  state.api = api;
  byId("setup").hidden = true;
  byId("shell").hidden = false;
  state.chat = new ChatView({
    api, storage, container: byId("view-chat"), titleElement: byId("title"),
    onConversationsChanged: () => { if (!byId("drawer").hidden) renderChatList(); },
  });
  state.updates = new UpdatesFeed({
    api, storage, container: byId("view-updates"), badge: byId("updates-badge"), isShowing: () => state.view === "updates" && !document.hidden,
  });
  for (const tab of document.querySelectorAll(".tab")) tab.addEventListener("click", () => showView(tab.dataset.view));
  byId("open-chats").addEventListener("click", openDrawer);
  byId("close-chats").addEventListener("click", closeDrawer);
  byId("drawer-scrim").addEventListener("click", closeDrawer);
  byId("new-chat").addEventListener("click", () => {
    state.chat.newChat();
    showView("chat");
  });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) return state.updates.stop();
    return state.updates.start();
  });
  state.chat.start();
  state.updates.start();
}

function registerOfflineSupport() {
  if (!("serviceWorker" in navigator) || !window.isSecureContext) return;
  navigator.serviceWorker.register("sw.js", { scope: "./" })
    .catch((error) => console.warn("Arty works, but not offline:", error.message));
}

function boot() {
  for (const [name, title] of [["updates", "Updates"], ["tasks", "Tasks"], ["calendar", "Calendar"], ["more", "More"]]) byId(`view-${name}`).dataset.title = title;
  registerOfflineSupport();
  const settings = loadSettings(storage);
  if (settings) return startApp(new ArtyApi(settings));
  return renderSetup(byId("setup"), storage, (api) => startApp(api));
}

boot();
