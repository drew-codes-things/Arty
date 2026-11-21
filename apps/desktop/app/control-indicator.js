'use strict';

const CONTROL_HOTKEY = 'CommandOrControl+Alt+End';
const POLL_SECONDS = 25;
const RETRY_MS = 3000;

async function followControl({ fetchState, postShown, showIndicator, hideIndicator, onEnabled, sleep, keepGoing }) {
  let after = 0;
  let shownSession = 0;
  let enabled = null;
  while (keepGoing()) {
    let state;
    try {
      state = await fetchState(after, POLL_SECONDS);
    } catch {
      if (shownSession) {
        hideIndicator();
        shownSession = 0;
      }
      await sleep(RETRY_MS);
      continue;
    }
    after = state.version;
    if (state.enabled !== enabled) {
      enabled = state.enabled;
      onEnabled(enabled);
    }
    if (state.active && state.session !== shownSession) {
      await showIndicator(state);
      shownSession = state.session;
      await postShown(state.session);
    } else if (!state.active && shownSession) {
      hideIndicator();
      shownSession = 0;
    }
  }
}

module.exports = { CONTROL_HOTKEY, POLL_SECONDS, RETRY_MS, followControl };
