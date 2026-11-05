const TOAST_MS = 4000;
let toastTimer = null;

export function h(tag, props = {}, ...children) {
  const element = document.createElement(tag);
  for (const [key, value] of Object.entries(props ?? {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") element.className = value;
    else if (key === "dataset") Object.assign(element.dataset, value);
    else if (key.startsWith("on") && typeof value === "function") element.addEventListener(key.slice(2).toLowerCase(), value);
    else if (key === "value") element.value = value;
    else element.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    element.append(child instanceof Node ? child : String(child));
  }
  return element;
}

export function fill(container, ...children) {
  container.replaceChildren(...children.flat(Infinity).filter((child) => child !== null && child !== undefined && child !== false));
}

export function showToast(message) {
  const toast = document.getElementById("toast");
  toast.textContent = message;
  toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toast.hidden = true; }, TOAST_MS);
}

const dayFormat = new Intl.DateTimeFormat("en-GB", { weekday: "short", day: "numeric", month: "short" });
const timeFormat = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit" });

export function formatMoment(date) {
  if (!(date instanceof Date) || Number.isNaN(date.getTime())) return "";
  return `${dayFormat.format(date)}, ${timeFormat.format(date)}`;
}

export function fromUnixSeconds(seconds) {
  return seconds ? new Date(seconds * 1000) : null;
}

export function formatDay(date) {
  return dayFormat.format(date);
}

export function formatClock(date) {
  return timeFormat.format(date);
}

export function twoStepButton(label, confirmLabel, onConfirm, className = "link-button") {
  const button = h("button", { type: "button", class: className }, label);
  let armed = false;
  button.addEventListener("click", async () => {
    if (!armed) {
      armed = true;
      button.textContent = confirmLabel;
      button.classList.add("confirm");
      setTimeout(() => {
        armed = false;
        button.textContent = label;
        button.classList.remove("confirm");
      }, TOAST_MS);
      return;
    }
    button.disabled = true;
    await onConfirm();
  });
  return button;
}

export async function withBusy(button, work) {
  button.disabled = true;
  try {
    return await work();
  } catch (error) {
    showToast(error.message);
    return undefined;
  } finally {
    button.disabled = false;
  }
}

export function sectionHead(title, ...actions) {
  return h("div", { class: "section-head" }, h("h2", {}, title), h("div", { class: "row" }, actions));
}
