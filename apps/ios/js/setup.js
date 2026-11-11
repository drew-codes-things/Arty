import { ArtyApi } from "./api.js";
import { h } from "./dom.js";
import { scanQrCode } from "./scanner.js";
import { cleanSettings, isComplete, saveSettings } from "./settings.js";
import { parseSetupCode } from "./setupcode.js";

const WHERE_TO_FIND_THEM = [
  "App key: on the machine running the brain, in /srv/ai/secrets/arty-api.txt. Arty on your computer has it too, as api_key in its arty_config.yaml.",
  "Cloudflare Access id and secret: Cloudflare dashboard > Zero Trust > Access controls > Service credentials > Service Tokens. The secret is only shown when a token is made. If it's lost, choose Rotate secret on that token, or make a new token and add it to the Arty API application's Service Auth policy. Arty on your computer has them as access_client_id and access_client_secret.",
  "The full guide is docs/connecting-a-phone.md in Arty's source.",
];

function secretField(label, name, hint) {
  return h("label", {}, label,
    h("input", { type: "password", name, autocomplete: "off", autocapitalize: "off", spellcheck: "false", placeholder: hint }));
}

function showStatus(status, text, isProblem = false) {
  status.textContent = text;
  status.classList.toggle("error-text", isProblem);
}

function fillForm(form, settings) {
  for (const [name, value] of Object.entries(settings)) form.elements.namedItem(name).value = value;
}

export function renderSetup(container, storage, onConnected) {
  const status = h("p", { class: "notice", role: "status" });
  const connect = h("button", { type: "submit", class: "button primary" }, "Connect");
  const scanInstead = h("button", { type: "button", class: "link-button" }, "Scan QR code instead");
  const form = h("form", { class: "form", hidden: true },
    secretField("App key", "apiKey", "ARTY_API_KEY"),
    secretField("Cloudflare Access id", "accessId", "needed away from home"),
    secretField("Cloudflare Access secret", "accessSecret", "needed away from home"),
    h("details", { class: "help" }, h("summary", {}, "Where do I find these?"), WHERE_TO_FIND_THEM.map((line) => h("p", {}, line))),
    connect, scanInstead);
  const scan = h("button", { type: "button", class: "button primary" }, "Scan QR code");
  const typeIn = h("button", { type: "button", class: "button" }, "Enter details manually");
  const choices = h("div", { class: "choices" }, scan, typeIn,
    h("p", { class: "help" }, "The QR code is in Arty on your computer: open its tray menu and choose Phone setup code."));

  const showForm = () => {
    choices.hidden = true;
    form.hidden = false;
  };

  const connectWith = async (candidate) => {
    if (!isComplete(candidate)) {
      showStatus(status, "Enter the app key. The Access id and secret go together: both or neither.", true);
      return;
    }
    connect.disabled = true;
    showStatus(status, "Checking with Arty...");
    try {
      const api = new ArtyApi(candidate);
      await api.models();
      onConnected(api, saveSettings(storage, candidate));
    } catch (error) {
      showStatus(status, error.message, true);
    } finally {
      connect.disabled = false;
    }
  };

  const scanAndConnect = async () => {
    showStatus(status, "");
    try {
      const scanned = await scanQrCode();
      if (scanned === null) return;
      const settings = parseSetupCode(scanned);
      fillForm(form, settings);
      showForm();
      await connectWith(settings);
    } catch (error) {
      showStatus(status, error.message, true);
    }
  };

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    connectWith(cleanSettings(Object.fromEntries(new FormData(form))));
  });
  scan.addEventListener("click", scanAndConnect);
  scanInstead.addEventListener("click", scanAndConnect);
  typeIn.addEventListener("click", showForm);
  container.replaceChildren(
    h("img", { src: "icons/icon-180.png", alt: "" }),
    h("h1", {}, "Arty"),
    h("p", {}, "Scan the setup code from Arty on your computer, or enter the keys yourself. They stay on this iPhone and are only sent to Arty."),
    choices, form, status,
  );
  container.hidden = false;
}
