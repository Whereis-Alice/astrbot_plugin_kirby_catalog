/**
 * Shared summary cache.
 *
 * admin/summary feeds five separate consumers (overview metrics, nav badges,
 * catalog filter options, terminology/wiki metric strips and the unlock date
 * default). Keeping one fetch plus a subscriber list here avoids the old
 * behaviour where every view refetched the same payload.
 */

import { apiGet } from "./bridge.js";
import { state, nextSequence, isCurrentSequence } from "./state.js";

const listeners = new Set();

/** Registers a callback fired after every successful summary refresh. */
export function onSummary(handler) {
  listeners.add(handler);
  return () => listeners.delete(handler);
}

function notify(payload) {
  for (const handler of listeners) {
    try {
      handler(payload);
    } catch (error) {
      console.error("summary listener failed", error);
    }
  }
}

/**
 * Fetches admin/summary and fans it out. Stale responses are dropped so a slow
 * refresh cannot overwrite a newer one.
 *
 * @returns {Promise<Object|null>}
 */
export async function refreshSummary() {
  const token = nextSequence("summary");
  const payload = await apiGet("admin/summary");
  if (!isCurrentSequence("summary", token)) {
    return state.summary;
  }
  state.summary = payload || null;
  notify(state.summary);
  return state.summary;
}

/** Returns the cached payload, fetching it once when still empty. */
export async function ensureSummary() {
  if (state.summary) {
    return state.summary;
  }
  return refreshSummary();
}

/** Today in the bot timezone, as reported by the backend. */
export function summaryToday() {
  return (state.summary && state.summary.today) || "";
}
