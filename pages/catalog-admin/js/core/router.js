/**
 * Hash router for the seven workspace views.
 *
 * Each view registers a loader that is invoked the first time the view becomes
 * visible; afterwards state.loaded gates re-fetching. Navigation writes real
 * history entries so the browser back button walks the view stack instead of
 * replacing it.
 */

import { qs, qsa, setHidden } from "./dom.js";
import { state } from "./state.js";

export const VIEW_IDS = Object.freeze([
  "overview",
  "catalog",
  "terminology",
  "wiki-index",
  "groups",
  "trash",
  "audit",
]);

const DEFAULT_VIEW = "overview";
const loaders = new Map();

let navRoot = null;
let indicator = null;
let panels = new Map();
let navItems = new Map();
let activeView = null;
let indicatorReady = false;

/** Associates a view id with its lazy loader. */
export function registerView(viewId, loader) {
  loaders.set(viewId, loader);
}

/** Currently visible view id. */
export function currentView() {
  return activeView;
}

function normalizeView(raw) {
  const text = String(raw === null || raw === undefined ? "" : raw)
    .replace(/^#\/?/, "")
    .trim()
    .toLowerCase();
  return VIEW_IDS.includes(text) ? text : "";
}

function readHash() {
  return normalizeView(window.location.hash);
}

function moveIndicator(item) {
  if (!indicator || !item) {
    return;
  }
  const width = item.offsetWidth;
  if (!width) {
    return;
  }
  indicator.style.width = width + "px";
  indicator.style.transform = "translateX(" + item.offsetLeft + "px)";
  if (!indicatorReady) {
    // Only fade the pill in once it has a real position, otherwise it slides
    // in from the left edge on first paint.
    window.requestAnimationFrame(() => indicator.classList.add("is-ready"));
    indicatorReady = true;
  }
}

function syncIndicator() {
  moveIndicator(navItems.get(activeView));
}

async function runLoader(viewId) {
  const loader = loaders.get(viewId);
  if (!loader || state.loaded.has(viewId)) {
    return;
  }
  // Marked loaded only after success so a failed request can be retried by
  // simply navigating back to the view.
  await loader();
  state.loaded.add(viewId);
}

/**
 * Shows a view, optionally pushing a history entry.
 *
 * @param {string} requested
 * @param {{push?: boolean, replace?: boolean, force?: boolean}} [options]
 */
export function switchView(requested, options) {
  const opts = options || {};
  const viewId = normalizeView(requested) || DEFAULT_VIEW;
  const changed = viewId !== activeView;

  if (changed) {
    activeView = viewId;
    for (const [id, panel] of panels) {
      setHidden(panel, id !== viewId);
    }
    for (const [id, item] of navItems) {
      const isActive = id === viewId;
      item.classList.toggle("is-active", isActive);
      item.setAttribute("aria-current", isActive ? "page" : "false");
    }
    const item = navItems.get(viewId);
    moveIndicator(item);
    if (item && typeof item.scrollIntoView === "function") {
      item.scrollIntoView({ block: "nearest", inline: "nearest" });
    }
    window.scrollTo({ top: 0, behavior: "auto" });
  }

  const target = "#" + viewId;
  if (window.location.hash !== target) {
    if (opts.replace) {
      window.history.replaceState(null, "", target);
    } else if (opts.push !== false) {
      window.history.pushState(null, "", target);
    }
  }

  if (opts.force) {
    state.loaded.delete(viewId);
  }
  return runLoader(viewId).catch((error) => {
    console.error("view loader failed", viewId, error);
  });
}

/** Forces the active view to reload on the next visit. */
export function reloadActiveView() {
  return switchView(activeView || DEFAULT_VIEW, { push: false, force: true });
}

/**
 * Wires nav buttons, in-page view links and history events.
 * Returns the view id that should be shown first.
 */
export function initRouter() {
  navRoot = qs("#primaryNav");
  indicator = qs("#navIndicator");

  panels = new Map(
    qsa("[data-view-panel]").map((panel) => [panel.getAttribute("data-view-panel"), panel])
  );
  navItems = new Map(
    qsa(".nav-item[data-view]", navRoot || document).map((item) => [item.dataset.view, item])
  );

  for (const [viewId, item] of navItems) {
    item.addEventListener("click", () => switchView(viewId));
  }

  for (const link of qsa("[data-view-link]")) {
    link.addEventListener("click", () => switchView(link.getAttribute("data-view-link")));
  }

  window.addEventListener("hashchange", () => {
    const viewId = readHash();
    if (!viewId) {
      // Unknown fragment: rewrite it instead of leaving a dead view behind.
      switchView(DEFAULT_VIEW, { replace: true });
      return;
    }
    switchView(viewId, { push: false });
  });

  window.addEventListener("resize", syncIndicator);
  if (typeof ResizeObserver === "function" && navRoot) {
    new ResizeObserver(syncIndicator).observe(navRoot);
  }
  if (document.fonts && document.fonts.ready) {
    document.fonts.ready.then(syncIndicator).catch(() => {});
  }

  const initial = readHash();
  return { view: initial || DEFAULT_VIEW, corrected: !initial };
}
