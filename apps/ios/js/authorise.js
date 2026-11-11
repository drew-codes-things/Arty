import { h } from "./dom.js";
import { qrImage } from "./qr.js";

const CODE_SHAPE = /^[A-Z2-9]{4}-?[A-Z2-9]{4}$/;
export const AUTHORISE_PARAM = "authorise";
const ANSWER_FAILED = "Check the connection and try again.";
const WEB_PROTOCOLS = new Set(["http:", "https:"]);
const STARFIRE_CODE_PARAM = "code";

export function readAuthoriseCode(search) {
  const raw = new URLSearchParams(search ?? "").get(AUTHORISE_PARAM);
  const code = String(raw ?? "").trim().toUpperCase();
  if (!CODE_SHAPE.test(code)) return null;
  const letters = code.replace("-", "");
  return `${letters.slice(0, 4)}-${letters.slice(4)}`;
}

export function withoutAuthoriseCode(href) {
  const url = new URL(href);
  url.searchParams.delete(AUTHORISE_PARAM);
  return url.pathname + (url.search || "") + url.hash;
}

export function setupHint(search) {
  const code = readAuthoriseCode(search);
  if (!code) return null;
  return `A computer is waiting for you to approve it (code ${code}). Connect Arty here first and the approval opens `
    + "straight after. Arty on your home screen keeps its own settings, so this page needs connecting too.";
}

export function approveElsewhereLink(approvePage, code) {
  if (!CODE_SHAPE.test(String(code ?? ""))) return null;
  let page;
  try {
    page = new URL(String(approvePage ?? ""));
  } catch {
    return null;
  }
  if (!WEB_PROTOCOLS.has(page.protocol)) return null;
  page.searchParams.set(STARFIRE_CODE_PARAM, code);
  return page.toString();
}

export async function renderApproveElsewhere(container, { code, link, onPasteKey }) {
  const qrSlot = h("div", { class: "qr-slot" });
  container.replaceChildren(
    h("img", { src: "icons/icon-180.png", alt: "" }),
    h("h1", {}, "Approve this computer"),
    h("p", { class: "code" }, code),
    h("p", {}, "This browser has no Arty key, so approve it in starfire: open the link, or scan the code with your phone, "
      + "sign in as an admin and approve the computer showing this code."),
    qrSlot,
    h("a", { class: "button primary", href: link, target: "_blank", rel: "noopener noreferrer" }, "Approve in starfire"),
    h("button", { type: "button", class: "link-button", onclick: onPasteKey }, "Paste an API key instead"),
  );
  container.hidden = false;
  try {
    qrSlot.replaceChildren(await qrImage(link, `QR code that opens starfire's approve page for code ${code}`));
  } catch (error) {
    qrSlot.replaceChildren(h("p", { class: "notice error-text" }, error.message));
  }
}

export function deviceSummary(pending) {
  const name = String(pending?.name || "A device");
  const address = pending?.address ? `, asking from ${pending.address},` : "";
  return `A device calling itself \u201c${name}\u201d${address} wants its own key for Arty.`;
}

function lettersOf(code) {
  return String(code ?? "").toUpperCase().replace(/[^A-Z0-9]/g, "");
}

export function codesMatch(expected, typed) {
  const wanted = lettersOf(expected);
  return wanted.length > 0 && lettersOf(typed) === wanted;
}

export function renderAuthorise(container, api, code, onClose) {
  const status = h("p", { class: "notice" }, "Checking the request...");
  const buttons = h("div", { class: "row" });
  const close = () => {
    container.hidden = true;
    container.replaceChildren();
    onClose();
  };
  const doneButton = () => h("button", { type: "button", class: "button", onclick: close }, "Done");
  const answer = async (approve) => {
    buttons.replaceChildren();
    try {
      await api.answerDevice(code, approve);
    } catch (error) {
      status.textContent = `${error.message} ${ANSWER_FAILED}`;
      buttons.replaceChildren(h("button", { type: "button", class: "button primary", onclick: () => answer(approve) }, "Retry"),
        doneButton());
      return;
    }
    status.textContent = approve ? "Approved. Go back to the terminal: it's connected." : "Turned down. Nothing was shared.";
    buttons.replaceChildren(doneButton());
  };
  container.hidden = false;
  container.replaceChildren(h("div", { class: "sheet" },
    h("h2", {}, "Approve a device"),
    h("p", { class: "code" }, code),
    status,
    h("p", { class: "notice" }, "Only approve this if you started it yourself. Type the code your terminal shows: Approve only works when it matches."),
    buttons));
  api.pendingDevice(code).then((pending) => {
    status.textContent = deviceSummary(pending);
    const approve = h("button", { type: "button", class: "button primary", disabled: true, onclick: () => answer(true) }, "Approve");
    const typed = h("input", {
      type: "text", class: "input", autocomplete: "off", autocapitalize: "characters", spellcheck: "false",
      placeholder: "Code from your terminal", "aria-label": "Type the code your terminal shows",
      oninput: (event) => { approve.disabled = !codesMatch(code, event.target.value); },
    });
    buttons.replaceChildren(typed, approve,
      h("button", { type: "button", class: "button", onclick: () => answer(false) }, "Deny"));
  }).catch((error) => {
    status.textContent = error.status === 404 ? "That code has run out or was never made. Start again in the terminal." : error.message;
    buttons.replaceChildren(h("button", { type: "button", class: "button", onclick: close }, "Close"));
  });
}
