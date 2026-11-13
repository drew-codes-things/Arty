import { h } from "./dom.js";
import { loadPreferences, savePreferences } from "./settings.js";

export const NOTIFICATION_CATEGORIES = [
  { key: "email", title: "Email", description: "Important new emails Arty spots" },
  { key: "server", title: "Server and homelab", description: "Services going down or coming back, homelab alerts" },
  { key: "tasks", title: "Background tasks", description: "Tasks finishing, failing or needing you" },
  { key: "approvals", title: "Approvals", description: "When Arty needs your OK before it does something, like fixing a server",
    warningWhenOff: "Arty will wait silently for your answers. Check Updates or Tasks for them." },
  { key: "reminders", title: "Reminders and deadlines", description: "Reminders you set and deadlines coming up" },
  { key: "calendar", title: "Calendar", description: "Events about to start" },
  { key: "lectures", title: "Lectures", description: "Lecture notes ready, or a lecture that failed" },
  { key: "arty", title: "Arty's own notices", description: "Model switches, things expiring soon, briefings" },
  { key: "other", title: "Other", description: "Anything else sent to you through Arty" },
];

export const STILL_IN_UPDATES_NOTE = "A switched-off kind still appears in Updates. It just doesn't pop up or count on the Updates badge.";

const CATEGORY_KEYS = new Set(NOTIFICATION_CATEGORIES.map((category) => category.key));
const CATEGORY_BY_OLD_KIND = { approval: "approvals", reminder: "reminders", alert: "server" };
const OTHER = "other";

export function categoryOf(item) {
  const named = typeof item?.category === "string" ? item.category.trim().toLowerCase() : "";
  if (CATEGORY_KEYS.has(named)) return named;
  return CATEGORY_BY_OLD_KIND[item?.kind] ?? OTHER;
}

export function isAlerting(item, quietCategories) {
  return !quietCategories.includes(categoryOf(item));
}

export function withCategorySwitched(quietCategories, key, on) {
  const others = quietCategories.filter((quiet) => quiet !== key);
  return on ? others : [...others, key];
}

function categorySwitch(storage, category) {
  const isOn = !loadPreferences(storage).quietCategories.includes(category.key);
  const warning = category.warningWhenOff
    ? h("small", { class: "switch-warning", hidden: isOn }, category.warningWhenOff)
    : null;
  const input = h("input", { type: "checkbox", checked: isOn, "aria-label": category.title,
    onchange: () => {
      const { quietCategories } = loadPreferences(storage);
      savePreferences(storage, { quietCategories: withCategorySwitched(quietCategories, category.key, input.checked) });
      if (warning) warning.hidden = input.checked;
    } });
  return h("label", { class: "toggle described" },
    h("span", { class: "toggle-text" }, h("span", {}, category.title), h("small", {}, category.description), warning),
    input);
}

export function renderNotificationSwitches(storage) {
  return [
    h("h3", { class: "kicker" }, "Notifications"),
    ...NOTIFICATION_CATEGORIES.map((category) => categorySwitch(storage, category)),
    h("p", { class: "notice" }, STILL_IN_UPDATES_NOTE),
  ];
}
