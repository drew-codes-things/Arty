import { fill, formatClock, formatDay, formatMoment, fromUnixSeconds, h, sectionHead, showToast, twoStepButton, withBusy } from "./dom.js";
import { renderMarkdown } from "./markdown.js";
import { RESULT_PREVIEW_CHARS, capitalised, resultPreview, taskActions } from "./tasks.js";

const LECTURE_REFRESH_MS = 15000;
const RECORDING_TYPES = ["audio/mp4", "audio/webm"];

function loading(container, title) {
  fill(container, sectionHead(title), h("p", { class: "notice" }, "Loading..."));
}

function failed(container, title, error, retry) {
  fill(container, sectionHead(title), h("p", { class: "notice error-text" }, error.message),
    h("button", { type: "button", class: "button", onclick: retry }, "Try again"));
}

function markdownBlock(text) {
  const block = h("div", { class: "card-body markdown" });
  block.innerHTML = renderMarkdown(text);
  return block;
}

function field(label, input) {
  return h("label", {}, label, input);
}

function simpleForm(fields, submitLabel, onSubmit) {
  const submit = h("button", { type: "submit", class: "button primary" }, submitLabel);
  const form = h("form", { class: "form" }, fields, submit);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const values = Object.fromEntries([...new FormData(form)].map(([key, value]) => [key, String(value).trim()]));
    withBusy(submit, async () => {
      if (await onSubmit(values) !== false) form.reset();
    });
  });
  return form;
}

export async function renderTasks(container, api) {
  const reload = () => renderTasks(container, api);
  loading(container, "Tasks");
  let tasks;
  try {
    tasks = await api.tasks();
  } catch (error) {
    return failed(container, "Tasks", error, reload);
  }
  const form = simpleForm(
    field("New background task", h("textarea", { name: "instructions", rows: "2", required: true, placeholder: "e.g. Find three good Proxmox backup guides and summarise them" })),
    "Start",
    async ({ instructions }) => {
      await api.createTask(instructions);
      showToast("Task started");
      reload();
    },
  );
  fill(container, sectionHead("Tasks", h("button", { type: "button", class: "button small", onclick: reload }, "Refresh")),
    form, tasks.length ? tasks.map((task) => taskCard(task, api, reload)) : h("p", { class: "notice" }, "No tasks yet."));
  return undefined;
}

function taskCard(task, api, reload) {
  const actions = taskActions(task).map(({ action, label, primary }) => h("button", {
    type: "button", class: `button small${primary ? " primary" : ""}`,
    onclick: (event) => withBusy(event.currentTarget, async () => {
      showToast(await api.decideTask(task.id, action) || `${label}: sent`);
      reload();
    }),
  }, label));
  let expanded = false;
  const result = task.result ? h("div", { class: "card-body" }, resultPreview(task.result)) : null;
  const toggle = task.result && task.result.length > RESULT_PREVIEW_CHARS
    ? h("button", { type: "button", class: "link-button accent" }, "Show all") : null;
  toggle?.addEventListener("click", () => {
    expanded = !expanded;
    result.replaceWith(expanded ? markdownBlock(task.result) : h("div", { class: "card-body" }, resultPreview(task.result)));
    toggle.textContent = expanded ? "Show less" : "Show all";
  });
  return h("article", { class: `card status-${task.status}` },
    h("div", { class: "card-meta" }, h("span", {}, `#${task.id} · ${capitalised(task.status)}`), h("span", {}, formatMoment(fromUnixSeconds(task.updated)))),
    h("div", { class: "card-title" }, task.title || task.instructions),
    task.status === "waiting" && task.waiting?.message ? h("div", { class: "waiting-message" }, task.waiting.message) : null,
    result, toggle,
    actions.length ? h("div", { class: "row" }, actions) : null);
}

export async function renderSchedules(container, api) {
  const reload = () => renderSchedules(container, api);
  loading(container, "Schedules");
  let schedules;
  try {
    schedules = await api.schedules();
  } catch (error) {
    return failed(container, "Schedules", error, reload);
  }
  const form = simpleForm([
    field("What should Arty do?", h("input", { type: "text", name: "instructions", required: true, placeholder: "e.g. Tell me the weather" })),
    field("When", h("input", { type: "text", name: "when", required: true, placeholder: "e.g. weekdays 08:00" })),
  ], "Add", async ({ instructions, when }) => {
    showToast(await api.createSchedule(instructions, when) || "Added");
    reload();
  });
  const cards = schedules.map((schedule) => h("article", { class: "card" },
    h("div", { class: "card-title" }, schedule.title || schedule.instructions),
    schedule.description ? h("div", { class: "card-body" }, schedule.description) : null,
    schedule.next_run ? h("div", { class: "card-meta" }, `Next: ${formatMoment(fromUnixSeconds(schedule.next_run))}`) : null,
    h("div", { class: "row" }, twoStepButton("Cancel", "Tap again to cancel", async () => {
      await api.cancelSchedule(schedule.id).catch((error) => showToast(error.message));
      reload();
    }))));
  fill(container, sectionHead("Schedules"), form, cards.length ? cards : h("p", { class: "notice" }, "Nothing scheduled."));
  return undefined;
}

export async function renderCalendar(container, api) {
  const reload = () => renderCalendar(container, api);
  loading(container, "Calendar");
  let events;
  try {
    events = await api.calendar();
  } catch (error) {
    return failed(container, "Calendar", error, reload);
  }
  const form = simpleForm([
    field("What", h("input", { type: "text", name: "title", required: true, placeholder: "e.g. Dentist" })),
    field("When", h("input", { type: "text", name: "when", required: true, placeholder: "e.g. Tuesday 3pm, every Monday 6pm" })),
    field("Where (optional)", h("input", { type: "text", name: "location" })),
  ], "Add event", async ({ title, when, location }) => {
    showToast(await api.addEvent(title, when, location) || "Added");
    reload();
  });
  const byDay = new Map();
  for (const event of events) {
    const start = new Date(event.start);
    const day = Number.isNaN(start.getTime()) ? "Undated" : formatDay(start);
    if (!byDay.has(day)) byDay.set(day, []);
    byDay.get(day).push(eventCard(event, start, api, reload));
  }
  const days = [...byDay].map(([day, cards]) => [h("h3", { class: "kicker day" }, day), cards]);
  fill(container, sectionHead("Calendar", h("button", { type: "button", class: "button small", onclick: reload }, "Refresh")),
    form, days.length ? days : h("p", { class: "notice" }, "Nothing in the next 30 days."));
  return undefined;
}

function eventCard(event, start, api, reload) {
  const when = event.all_day ? "All day" : Number.isNaN(start.getTime()) ? event.start : formatClock(start);
  const remove = event.id == null ? null : twoStepButton("Remove", "Tap again to remove", async () => {
    await api.deleteEvent(event.id).catch((error) => showToast(error.message));
    reload();
  });
  return h("article", { class: "card" },
    h("div", { class: "card-meta" }, h("span", {}, `${when}${event.repeats ? " · repeats" : ""}`), h("span", {}, event.calendar || "Arty")),
    h("div", { class: "card-title" }, event.title),
    event.location ? h("div", { class: "card-body" }, event.location) : null,
    remove ? h("div", { class: "row" }, remove) : null);
}

export async function renderMemory(container, api) {
  const reload = () => renderMemory(container, api);
  loading(container, "Memory");
  let entities;
  try {
    entities = await api.memory();
  } catch (error) {
    return failed(container, "Memory", error, reload);
  }
  const list = h("div");
  const search = h("input", { type: "search", placeholder: "Search memory", "aria-label": "Search memory" });
  const show = () => {
    const query = search.value.trim().toLowerCase();
    const matches = entities.filter((entity) => !query || [entity.name, entity.entityType, ...(entity.observations ?? [])]
      .some((text) => String(text ?? "").toLowerCase().includes(query)));
    list.replaceChildren(...(matches.length ? matches.map((entity) => h("article", { class: "card memory-entity" },
      h("div", { class: "card-meta" }, entity.entityType || ""),
      h("h3", {}, entity.name),
      h("ul", {}, (entity.observations ?? []).map((observation) => h("li", {}, observation))))) : [h("p", { class: "notice" }, "Nothing found.")]));
  };
  search.addEventListener("input", show);
  fill(container, sectionHead("Memory"), search, h("div", { class: "notice" }, `${entities.length} things Arty remembers.`), list);
  show();
  return undefined;
}

export async function renderLectures(container, api) {
  const reload = () => renderLectures(container, api);
  loading(container, "Lectures");
  let lectures;
  try {
    lectures = await api.lectures();
  } catch (error) {
    return failed(container, "Lectures", error, reload);
  }
  const titleInput = h("input", { type: "text", name: "title", placeholder: "e.g. Networking week 6" });
  const picker = h("input", { type: "file", accept: "audio/*,video/*", hidden: true });
  const status = h("p", { class: "notice" }, "Record a lesson, or add a recording, and Arty writes notes and revision cards from it.");
  const send = async (file) => {
    status.textContent = `Sending ${file.name}...`;
    try {
      const uploaded = await api.upload(file);
      await api.startLecture(uploaded.fileId, titleInput.value.trim() || file.name);
      showToast("Sent to Arty");
      reload();
    } catch (error) {
      status.textContent = error.message;
      status.classList.add("error-text");
    }
  };
  picker.addEventListener("change", () => picker.files[0] && send(picker.files[0]));
  const recorder = lectureRecorder(status, send);
  const cards = lectures.map((lecture) => lectureCard(lecture, api));
  fill(container, sectionHead("Lectures", h("button", { type: "button", class: "button small", onclick: reload }, "Refresh")),
    h("div", { class: "form" }, field("Title", titleInput), status,
      h("div", { class: "row" }, recorder, h("button", { type: "button", class: "button", onclick: () => picker.click() }, "Add a recording")), picker),
    cards.length ? cards : h("p", { class: "notice" }, "No lectures yet."));
  if (lectures.some((lecture) => !["done", "failed"].includes(lecture.status))) {
    setTimeout(() => container.isConnected && !container.hidden && reload(), LECTURE_REFRESH_MS);
  }
  return undefined;
}

function lectureRecorder(status, send) {
  const button = h("button", { type: "button", class: "button primary" }, "Record");
  if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
    button.disabled = true;
    return button;
  }
  let recorder = null;
  button.addEventListener("click", async () => {
    if (recorder) return recorder.stop();
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (error) {
      status.textContent = `The microphone isn't available: ${error.message}`;
      return undefined;
    }
    const mimeType = RECORDING_TYPES.find((type) => MediaRecorder.isTypeSupported(type)) ?? "";
    const chunks = [];
    recorder = new MediaRecorder(stream, mimeType ? { mimeType } : {});
    recorder.addEventListener("dataavailable", (event) => chunks.push(event.data));
    recorder.addEventListener("stop", () => {
      stream.getTracks().forEach((track) => track.stop());
      const type = recorder.mimeType || mimeType || "audio/mp4";
      recorder = null;
      button.textContent = "Record";
      send(new File(chunks, `lecture-${Date.now()}.${type.includes("webm") ? "webm" : "m4a"}`, { type }));
    });
    recorder.start();
    button.textContent = "Stop and send";
    status.textContent = "Recording. Keep Arty open with the screen on until you stop.";
    return undefined;
  });
  return button;
}

function lectureCard(lecture, api) {
  const body = h("div");
  const open = lecture.status === "done" ? h("button", {
    type: "button", class: "link-button accent",
    onclick: (event) => withBusy(event.currentTarget, async () => {
      const full = await api.lecture(lecture.id);
      body.replaceChildren(markdownBlock(full.notes || "No notes."));
      event.currentTarget?.remove();
    }),
  }, "Read the notes") : null;
  return h("article", { class: "card" },
    h("div", { class: "card-meta" }, h("span", {}, capitalised(lecture.status)), h("span", {}, formatMoment(fromUnixSeconds(lecture.created)))),
    h("div", { class: "card-title" }, lecture.title),
    lecture.cards ? h("div", { class: "card-body" }, `${lecture.cards} revision cards. Say "quiz me" in chat to use them.`) : null,
    lecture.error ? h("div", { class: "card-body error-text" }, lecture.error) : null,
    open, body);
}

function deviceUse(device) {
  const lastUsed = formatMoment(fromUnixSeconds(device.last_used));
  return lastUsed ? `Last used ${lastUsed}` : "Never used";
}

export async function renderDevices(container, api) {
  const reload = () => renderDevices(container, api);
  loading(container, "Devices");
  let devices;
  try {
    devices = await api.devices();
  } catch (error) {
    return failed(container, "Devices", error, reload);
  }
  const cards = devices.map((device) => h("article", { class: "card" },
    h("div", { class: "card-title" }, device.name || "Unnamed device"),
    h("div", { class: "card-meta" }, `Approved ${formatMoment(fromUnixSeconds(device.created))} from ${device.address || "an unknown address"}`),
    h("div", { class: "card-meta" }, deviceUse(device)),
    h("div", { class: "row" }, twoStepButton("Turn off", "Tap again to turn it off", async () => {
      try {
        await api.revokeDevice(device.id);
        showToast("Device turned off");
      } catch (error) {
        showToast(error.message);
      }
      return reload();
    }))));
  fill(container, sectionHead("Devices"),
    h("p", { class: "notice" }, "Devices you approved with a code. Turning one off stops its key working straight away."),
    cards.length ? cards : h("p", { class: "notice" }, "No devices approved yet."));
  return undefined;
}
