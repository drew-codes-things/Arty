import { h } from "./dom.js";

export const ANSWER_PAUSE_MS = 1000;
export const NEW_QUESTION = "New question";
const WAITING_CLASS = "answer-waiting";

export function answerGuard(clock = () => Date.now()) {
  let seenAt = null;
  return {
    see() {
      if (seenAt === null) seenAt = clock();
    },
    isReady() {
      return seenAt !== null && clock() - seenAt >= ANSWER_PAUSE_MS;
    },
  };
}

function whenSeen(element, onSeen) {
  if (typeof IntersectionObserver !== "function") {
    onSeen();
    return;
  }
  const observer = new IntersectionObserver((entries) => {
    if (!entries.some((entry) => entry.isIntersecting)) return;
    observer.disconnect();
    onSeen();
  });
  observer.observe(element);
}

export function flashNewQuestion(card, buttons, guard) {
  const flash = h("span", { class: "new-question", role: "status" }, NEW_QUESTION);
  card.classList.add(WAITING_CLASS);
  card.append(flash);
  buttons.forEach((button) => button.setAttribute("aria-disabled", "true"));
  whenSeen(card, () => {
    guard.see();
    setTimeout(() => {
      card.classList.remove(WAITING_CLASS);
      flash.remove();
      buttons.forEach((button) => button.removeAttribute("aria-disabled"));
    }, ANSWER_PAUSE_MS);
  });
  return card;
}
