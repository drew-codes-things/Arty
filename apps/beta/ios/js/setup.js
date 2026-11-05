import { ArtyApi } from "./api.js";
import { h } from "./dom.js";
import { cleanSettings, isComplete, saveSettings } from "./settings.js";

function secretField(label, name, hint) {
  return h("label", {}, label,
    h("input", { type: "password", name, autocomplete: "off", autocapitalize: "off", spellcheck: "false", placeholder: hint }));
}

export function renderSetup(container, storage, onConnected) {
  const status = h("p", { class: "notice", role: "status" });
  const connect = h("button", { type: "submit", class: "button primary" }, "Connect");
  const form = h("form", { class: "form" },
    secretField("App key", "apiKey", "ARTY_API_KEY"),
    secretField("Cloudflare Access id", "accessId", "needed away from home"),
    secretField("Cloudflare Access secret", "accessSecret", "needed away from home"),
    connect, status);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const candidate = cleanSettings(Object.fromEntries(new FormData(form)));
    if (!isComplete(candidate)) {
      status.textContent = "Enter the app key. The Access id and secret go together: both or neither.";
      status.classList.add("error-text");
      return;
    }
    connect.disabled = true;
    status.classList.remove("error-text");
    status.textContent = "Checking with Arty...";
    try {
      const api = new ArtyApi(candidate);
      await api.models();
      onConnected(api, saveSettings(storage, candidate));
    } catch (error) {
      status.textContent = error.message;
      status.classList.add("error-text");
    } finally {
      connect.disabled = false;
    }
  });
  container.replaceChildren(
    h("img", { src: "icons/icon-180.png", alt: "" }),
    h("h1", {}, "Arty"),
    h("p", {}, "Enter the same keys as the phone app. They stay on this iPhone and are only sent to Arty."),
    form,
  );
  container.hidden = false;
}
