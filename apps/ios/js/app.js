import { ChatView } from "./chat.js";
import { renderComputers } from "./computers.js";
import { formatMoment, h, sectionHead, twoStepButton } from "./dom.js";
import { renderCalendar, renderDevices, renderLectures, renderMemory, renderSchedules, renderTasks } from "./manage.js";
import { renderNotificationSwitches } from "./notifications.js";
import { renderSetup } from "./setup.js";
import { approveElsewhereLink, readAuthoriseCode, renderApproveElsewhere, renderAuthorise, setupHint, withoutAuthoriseCode } from "./authorise.js";
import { ArtyApi, approvePage } from "./api.js";
import { forgetEverything, forgetSettings, loadPreferences, loadSettings, savePreferences } from "./settings.js";
import { UpdatesFeed } from "./updates.js";

const storage = window.localStorage;
const byId = (id) => document.getElementById(id);
const VIEWS = ["chat", "updates", "tasks", "calendar", "more"];
const MORE_PAGES = { schedules: renderSchedules, lectures: renderLectures, memory: renderMemory, devices: renderDevices,
  computers: renderComputers };

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
    h("ul", { class: "menu" }, page("schedules", "Schedules"), page("lectures", "Lectures"), page("memory", "Memory"),
      page("devices", "Approved devices"), page("computers", "Computers")),
    h("h3", { class: "kicker" }, "This iPhone"),
    h("label", { class: "toggle" }, h("span", {}, "Read Arty's replies aloud"), readAloud),
    ...renderNotificationSwitches(storage),
    h("p", { class: "notice" }, settings?.accessId ? "Connected with the app key and the Cloudflare access token." : "Connected with the app key only (home network)."),
    twoStepButton("Forget the keys on this iPhone", "Tap again to forget them", async () => {
      forgetSettings(storage);
      window.location.reload();
    }, "button"),
    twoStepButton("Forget everything on this iPhone", "Tap again: keys, chats and settings go", async () => {
      forgetEverything(storage);
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
  offerAuthorise(api);
}

function offerAuthorise(api) {
  const code = readAuthoriseCode(window.location.search);
  if (!code) return;
  window.history.replaceState(null, "", withoutAuthoriseCode(window.location.href));
  renderAuthorise(byId("authorise"), api, code, () => {});
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
  const code = readAuthoriseCode(window.location.search);
  if (code) return offerApproveElsewhere(code);
  return showSetup();
}

function showSetup() {
  renderSetup(byId("setup"), storage, (api) => startApp(api));
  const hint = setupHint(window.location.search);
  if (hint) byId("setup").prepend(h("p", { class: "notice", role: "status" }, hint));
}

async function offerApproveElsewhere(code) {
  const link = approveElsewhereLink(await approvePage(), code);
  if (!link) return showSetup();
  return renderApproveElsewhere(byId("setup"), { code, link, onPasteKey: showSetup });
}

boot();
