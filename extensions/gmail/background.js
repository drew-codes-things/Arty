// Arty's Gmail reader. Google signs out a browser that a program controls through Chromium's debugging port,
// so Gmail lives in its own ordinary Google Chrome window, and this extension reads it. It asks the browser agent on
// this computer (127.0.0.1 only) for a job, does it in a Gmail tab and sends back what the page shows.
const AGENT = "http://127.0.0.1:9334";
const RETRY_MS = 3000;
const RESULTS_WAIT_MS = 20000;
const THREAD_WAIT_MS = 12000;
const POLL_MS = 400;
const SETTLE_MS = 1000;
const SEARCH_URL = "https://mail.google.com/mail/u/0/#search/";

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
let running = false;

async function serve() {
  if (running) return;
  running = true;
  try {
    while (true) {
      chrome.runtime.getPlatformInfo(() => {}); // any extension call keeps this worker from being put to sleep
      let job;
      try {
        const response = await fetch(`${AGENT}/job`, { method: "POST" });
        if (response.status === 204) continue; // nothing to do yet (the agent holds each request open a while)
        job = await response.json();
      } catch (error) {
        await sleep(RETRY_MS); // the agent is restarting
        continue;
      }
      let result;
      try {
        result = await run(job.action, job.args || {});
      } catch (error) {
        result = { error: String((error && error.message) || error) };
      }
      await fetch(`${AGENT}/result`, { method: "POST", body: JSON.stringify({ id: job.id, result }) }).catch(() => {});
    }
  } finally {
    running = false;
  }
}

// --- Page functions: these run inside the Gmail tab, so each must stand on its own. ---

// Gmail's message rows (stable class names for years: zA = a row, yW = sender, bog = subject, y2 = preview, xW = time).
function pageRows() {
  return [...document.querySelectorAll("tr.zA")].filter((row) => !row.dataset.artyStale && row.offsetParent).map((row) => {
    const who = row.querySelector(".yW [email]");
    const when = row.querySelector(".xW span");
    return {
      sender: who ? who.getAttribute("name") || who.innerText : row.querySelector(".yW")?.innerText || "",
      email: who ? who.getAttribute("email") : "",
      subject: row.querySelector(".bog")?.innerText || "",
      preview: (row.querySelector(".y2")?.innerText || "").replace(/^\s*[-–]\s*/, ""),
      when: when ? when.getAttribute("title") || when.innerText : "",
      unread: row.classList.contains("zE"),
      thread: row.querySelector("[data-legacy-thread-id]")?.getAttribute("data-legacy-thread-id") || "",
    };
  });
}

function pageMarkStale() {
  document.querySelectorAll("tr.zA").forEach((row) => { row.dataset.artyStale = "1"; });
}

function pageState() {
  const main = document.querySelector("[role=main]")?.innerText || "";
  const fresh = [...document.querySelectorAll("tr.zA")].some((row) => !row.dataset.artyStale && row.offsetParent);
  return { url: location.href, title: document.title, ready: fresh || /No (messages|conversations) (matched|in)/i.test(main) };
}

// An open conversation: the subject (h2.hP) and each shown message (div.adn) with sender (.gD), time (.g3), body (.a3s).
function pageThread() {
  return {
    subject: document.querySelector("h2.hP")?.innerText || "",
    messages: [...document.querySelectorAll("div.adn")].map((message) => {
      const who = message.querySelector(".gD");
      const when = message.querySelector(".g3");
      return {
        sender: who ? who.getAttribute("name") || who.innerText : "",
        email: who ? who.getAttribute("email") || "" : "",
        when: when ? when.getAttribute("title") || when.innerText : "",
        body: message.querySelector(".a3s")?.innerText || "",
      };
    }),
  };
}

// --- Driving the tab ---

async function inPage(tabId, func, args = []) {
  const [frame] = await chrome.scripting.executeScript({ target: { tabId }, func, args });
  return frame ? frame.result : undefined;
}

// The extension's own Gmail tab, remembered for the browser session: wherever Google sends it (the sign-in page,
// Gmail's advert page when signed out), it's reused, and it never takes the focus from what the user is doing.
async function gmailTab() {
  const { tabId } = await chrome.storage.session.get("tabId");
  if (tabId !== undefined) {
    try {
      return await chrome.tabs.get(tabId);
    } catch (error) {
      // closed: open another
    }
  }
  const [open] = await chrome.tabs.query({ url: "https://mail.google.com/*" });
  const tab = open || (await chrome.tabs.create({ url: "https://mail.google.com/mail/u/0/", active: false }));
  await chrome.storage.session.set({ tabId: tab.id });
  return tab;
}

async function waitForLoad(tabId) {
  const deadline = Date.now() + RESULTS_WAIT_MS;
  while (Date.now() < deadline) {
    const tab = await chrome.tabs.get(tabId);
    if (tab.status === "complete") return tab;
    await sleep(POLL_MS);
  }
  return chrome.tabs.get(tabId);
}

const signedOut = (url) => !String(url || "").startsWith("https://mail.google.com/");

// Opens a Gmail search and waits for its rows (or "No messages matched"). Returns {tab, blocked?}.
async function search(query) {
  let tab = await gmailTab();
  tab = await waitForLoad(tab.id);
  if (!signedOut(tab.url)) {
    await inPage(tab.id, pageMarkStale).catch(() => {}); // so the last search's rows aren't taken for this one's
  }
  const url = SEARCH_URL + encodeURIComponent(query).replace(/%20/g, "+");
  // Gmail doesn't redraw a search it's already showing (or one it's showing an email from), so its rows stay
  // marked stale and nothing is found: load that search afresh instead.
  const current = String(tab.url || "");
  const repeat = current === url || current.startsWith(url + "/");
  await chrome.tabs.update(tab.id, { url });
  if (repeat) {
    // Reloading before the address has changed would reload the email that was open, not the search.
    const deadline = Date.now() + RESULTS_WAIT_MS;
    while ((await chrome.tabs.get(tab.id)).url !== url && Date.now() < deadline) await sleep(POLL_MS / 4);
    await chrome.tabs.reload(tab.id);
  }
  const deadline = Date.now() + RESULTS_WAIT_MS;
  while (Date.now() < deadline) {
    await sleep(POLL_MS);
    tab = await chrome.tabs.get(tab.id);
    if (tab.status !== "complete") continue;
    // A fresh window passes through accounts.google.com on its way to the inbox, so only judge once loaded.
    if (signedOut(tab.url)) {
      if (Date.now() > deadline - RESULTS_WAIT_MS / 2) break;
      continue;
    }
    const state = await inPage(tab.id, pageState).catch(() => null);
    if (state && state.ready) {
      await sleep(SETTLE_MS);
      return { tab };
    }
  }
  tab = await chrome.tabs.get(tab.id);
  if (signedOut(tab.url)) {
    return { tab, blocked: "the Gmail window on my desktop isn't signed in to Google" };
  }
  return { tab }; // slow, but Gmail is open: read whatever it shows
}

async function run(action, args) {
  if (action === "ping") return { ok: true };
  if (action === "tabs") {
    // For checking the extension's behaviour: its tabs' addresses with any query left out, so no mail is shown.
    const { tabId } = await chrome.storage.session.get("tabId");
    const tabs = await chrome.tabs.query({});
    return { remembered: tabId ?? null, tabs: tabs.map((t) => ({ id: t.id, active: t.active, url: (t.url || "").split(/[?#]/)[0] })) };
  }
  const max = Math.max(1, Math.min(Number(args.max) || 15, 30));
  if (action === "gmail_list") {
    const { tab, blocked } = await search(String(args.query || "in:inbox is:unread"));
    if (blocked) return { blocked, url: tab.url };
    const emails = (await inPage(tab.id, pageRows)) || [];
    return { title: tab.title, url: tab.url, emails: emails.slice(0, max).map(({ thread, ...email }) => email) };
  }
  if (action === "gmail_read") {
    const query = String(args.query || "in:inbox");
    const { tab, blocked } = await search(query);
    if (blocked) return { blocked, url: tab.url };
    const rows = (await inPage(tab.id, pageRows)) || [];
    const number = Math.max(1, Number(args.number) || 1);
    if (rows.length < number) return { title: tab.title, url: tab.url, subject: "", messages: [], found: rows.length };
    const thread = rows[number - 1].thread;
    if (!thread) return { error: "couldn't find a link to that email" };
    await chrome.tabs.update(tab.id, { url: SEARCH_URL + encodeURIComponent(query).replace(/%20/g, "+") + "/" + thread });
    const deadline = Date.now() + THREAD_WAIT_MS;
    while (Date.now() < deadline) {
      await sleep(POLL_MS);
      const opened = await inPage(tab.id, pageThread).catch(() => null);
      if (opened && opened.subject) {
        await sleep(SETTLE_MS * 1.5); // the message bodies load just after the subject
        const current = await chrome.tabs.get(tab.id);
        return { title: current.title, url: current.url, ...(await inPage(tab.id, pageThread)) };
      }
    }
    return { error: "the email didn't open in time" };
  }
  throw new Error(`unknown action ${action}`);
}

chrome.alarms.create("serve", { periodInMinutes: 0.5 }); // restarts the loop if the worker was stopped
chrome.alarms.onAlarm.addListener(serve);
chrome.runtime.onStartup.addListener(serve);
chrome.runtime.onInstalled.addListener(serve);
serve();
