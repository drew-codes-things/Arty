import { h } from "./dom.js";

const PAGER_KEYS = { ArrowLeft: -1, ArrowRight: 1 };
const TYPING_TAGS = new Set(["INPUT", "TEXTAREA", "SELECT"]);

export function createCardPager() {
  let items = [];
  let current = null;
  return {
    sync(next) {
      const previousIndex = Math.max(items.indexOf(current), 0);
      items = [...next];
      if (items.includes(current)) return;
      current = items[Math.min(previousIndex, items.length - 1)] ?? null;
    },
    step(delta) {
      const target = items[items.indexOf(current) + delta];
      if (target !== undefined) current = target;
    },
    isCurrent(item) { return item === current; },
    get count() { return items.length; },
    get label() { return `${items.indexOf(current) + 1} of ${items.length}`; },
    get canGoBack() { return items.indexOf(current) > 0; },
    get canGoForward() { return items.indexOf(current) < items.length - 1; },
  };
}

function isTypingTarget(target) {
  return Boolean(target) && (TYPING_TAGS.has(target.tagName) || target.isContentEditable === true);
}

export function cardDeck(container) {
  const pager = createCardPager();
  let cards = [];
  const previous = h("button", { type: "button", class: "button", "aria-label": "Previous question", onclick: () => turn(-1) }, "<");
  const next = h("button", { type: "button", class: "button", "aria-label": "Next question", onclick: () => turn(1) }, ">");
  const count = h("span", { "aria-live": "polite" });
  const bar = h("div", { class: "card-pager" }, previous, count, next);
  const render = () => {
    pager.sync(cards);
    for (const card of cards) card.hidden = !pager.isCurrent(card);
    bar.hidden = pager.count < 2;
    count.textContent = pager.label;
    previous.setAttribute("aria-disabled", String(!pager.canGoBack));
    next.setAttribute("aria-disabled", String(!pager.canGoForward));
    container.replaceChildren(bar, ...cards);
  };
  const turn = (delta) => {
    pager.step(delta);
    render();
  };
  container.addEventListener("keydown", (event) => {
    const delta = PAGER_KEYS[event.key];
    if (delta === undefined || pager.count < 2 || isTypingTarget(event.target)) return;
    if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
    event.preventDefault();
    turn(delta);
  });
  return {
    show(shown) {
      cards = [...shown];
      render();
    },
    drop(answeredCard) {
      cards = cards.filter((card) => card !== answeredCard);
      render();
    },
  };
}
