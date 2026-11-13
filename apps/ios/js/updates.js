import { answerGuard, flashNewQuestion } from "./answerguard.js";
import { fill, formatMoment, fromUnixSeconds, h, sectionHead, showToast, withBusy } from "./dom.js";
import { isAlerting } from "./notifications.js";
import { loadPreferences, savePreferences } from "./settings.js";
import { approvalButtons } from "./tasks.js";

const LONG_POLL_SECONDS = 50;
const MIN_BACKOFF_MS = 5000;
const MAX_BACKOFF_MS = 300000;
const KEEP_UPDATES = 100;

export class UpdatesFeed {
  constructor({ api, storage, container, badge, isShowing }) {
    this.api = api;
    this.storage = storage;
    this.container = container;
    this.badge = badge;
    this.isShowing = isShowing;
    this.items = [];
    this.waitingTaskIds = new Set();
    this.last = 0;
    this.backoff = MIN_BACKOFF_MS;
    this.running = false;
    this.generation = 0;
  }

  async start() {
    if (this.running) return;
    this.running = true;
    const generation = ++this.generation;
    try {
      const first = await this.api.announcements(0, 0);
      this.items = first.announcements.reverse();
      this.last = first.last;
      await this.refreshWaitingTasks();
      this.render();
    } catch (error) {
      this.renderError(error);
    }
    this.poll(generation);
  }

  stop() {
    this.running = false;
    this.generation += 1;
  }

  async poll(generation) {
    while (this.running && generation === this.generation) {
      try {
        const feed = await this.api.announcements(this.last, LONG_POLL_SECONDS);
        if (generation !== this.generation) return;
        this.backoff = MIN_BACKOFF_MS;
        this.last = feed.last;
        if (feed.announcements.length) this.receive(feed.announcements);
      } catch {
        await new Promise((resolve) => setTimeout(resolve, this.backoff));
        this.backoff = Math.min(this.backoff * 2, MAX_BACKOFF_MS);
      }
    }
  }

  async receive(announcements) {
    this.items = [...announcements.slice().reverse(), ...this.items].slice(0, KEEP_UPDATES);
    if (announcements.some((item) => item.task_id != null)) await this.refreshWaitingTasks();
    const { quietCategories } = loadPreferences(this.storage);
    const newest = announcements.filter((item) => isAlerting(item, quietCategories)).at(-1);
    if (newest && !this.isShowing()) showToast(`${newest.title || "Arty"}: ${newest.message}`.slice(0, 160));
    this.render();
  }

  async refreshWaitingTasks() {
    try {
      const tasks = await this.api.tasks();
      this.waitingTaskIds = new Set(tasks.filter((task) => task.status === "waiting").map((task) => task.id));
    } catch {
      this.waitingTaskIds = new Set();
    }
  }

  unseenCount() {
    const { lastSeenUpdate, quietCategories } = loadPreferences(this.storage);
    return this.items.filter((item) => item.id > lastSeenUpdate && isAlerting(item, quietCategories)).length;
  }

  markSeen() {
    const newest = this.items[0]?.id ?? 0;
    if (newest > loadPreferences(this.storage).lastSeenUpdate) savePreferences(this.storage, { lastSeenUpdate: newest });
    this.renderBadge();
  }

  renderBadge() {
    const unseen = this.unseenCount();
    this.badge.hidden = unseen === 0;
    this.badge.textContent = unseen > 99 ? "99+" : String(unseen);
  }

  renderError(error) {
    fill(this.container, sectionHead("Updates"), h("p", { class: "notice error-text" }, error.message));
  }

  render() {
    if (this.isShowing()) this.markSeen();
    this.renderBadge();
    const cards = this.items.map((item) => this.card(item));
    fill(this.container, sectionHead("Updates"),
      h("p", { class: "notice" }, "Reminders, briefings, task results and alerts from Arty. They arrive here while the app is open."),
      cards.length ? cards : h("p", { class: "notice" }, "Nothing yet."));
  }

  card(item) {
    const guard = answerGuard();
    const buttons = approvalButtons(item, this.waitingTaskIds).map(({ action, label }) => h("button", {
      type: "button", class: `button small${action === "approve" ? " primary" : ""}`,
      onclick: (event) => {
        if (!guard.isReady()) return undefined;
        return withBusy(event.currentTarget, async () => {
          showToast(await this.api.decideTask(item.task_id, action) || `${label}: sent`);
          this.waitingTaskIds.delete(item.task_id);
          this.render();
        });
      },
    }, label));
    const card = h("article", { class: `card kind-${item.kind}` },
      h("div", { class: "card-meta" }, h("span", {}, item.kind), h("span", {}, formatMoment(fromUnixSeconds(item.created)))),
      item.title ? h("div", { class: "card-title" }, item.title) : null,
      h("div", { class: "card-body" }, item.message),
      buttons.length ? h("div", { class: "row" }, buttons) : null);
    return buttons.length ? flashNewQuestion(card, buttons, guard) : card;
  }
}
