/**
 * Side drawer controller.
 *
 * Accessibility fixes over the previous build: a closed drawer is marked inert
 * and hidden so its controls leave the tab order, focus is trapped while open
 * and restored to the trigger on close, and aria-hidden is never placed on a
 * container that holds the focused element.
 */

import { qsa } from "../core/dom.js";

const FOCUSABLE = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled]):not([type=hidden])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

let scrim = null;
let openDrawerId = null;
let lastTrigger = null;
const closeHandlers = new Map();

function focusables(drawer) {
  return qsa(FOCUSABLE, drawer).filter((node) => node.offsetParent !== null || node === document.activeElement);
}

function onKeydown(event) {
  if (!openDrawerId) {
    return;
  }
  const drawer = document.getElementById(openDrawerId);
  if (!drawer) {
    return;
  }
  if (event.key === "Escape") {
    event.preventDefault();
    closeDrawer();
    return;
  }
  if (event.key !== "Tab") {
    return;
  }
  const nodes = focusables(drawer);
  if (!nodes.length) {
    return;
  }
  const first = nodes[0];
  const last = nodes[nodes.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

/** Wires the scrim and every [data-drawer-close] button once at boot. */
export function initDrawers() {
  scrim = document.getElementById("drawerScrim");
  if (scrim) {
    scrim.addEventListener("click", () => closeDrawer());
  }
  for (const button of qsa("[data-drawer-close]")) {
    button.addEventListener("click", () => closeDrawer());
  }
  for (const drawer of qsa(".drawer")) {
    drawer.inert = true;
  }
  document.addEventListener("keydown", onKeydown);
}

/** Registers a callback invoked whenever the given drawer closes. */
export function onDrawerClose(drawerId, handler) {
  closeHandlers.set(drawerId, handler);
}

/** True when any drawer is currently open. */
export function isDrawerOpen(drawerId) {
  return drawerId ? openDrawerId === drawerId : Boolean(openDrawerId);
}

/**
 * Opens a drawer, closing whichever one is already open.
 *
 * @param {string} drawerId
 * @param {{focus?: string}} [options] Selector of the control to focus.
 */
export function openDrawer(drawerId, options) {
  const drawer = document.getElementById(drawerId);
  if (!drawer) {
    return;
  }
  if (openDrawerId && openDrawerId !== drawerId) {
    closeDrawer({ silent: false, restoreFocus: false });
  }
  if (!openDrawerId) {
    lastTrigger = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  }

  openDrawerId = drawerId;
  drawer.hidden = false;
  drawer.inert = false;
  // Force a frame so the transition runs from the closed state.
  window.requestAnimationFrame(() => drawer.classList.add("is-open"));
  if (scrim) {
    scrim.hidden = false;
    window.requestAnimationFrame(() => scrim.classList.add("is-open"));
  }
  document.body.style.overflow = "hidden";

  const focusTarget = options && options.focus ? drawer.querySelector(options.focus) : null;
  const fallback = focusables(drawer)[0];
  const node = focusTarget || fallback;
  if (node) {
    window.setTimeout(() => node.focus({ preventScroll: true }), 60);
  }
}

/**
 * Closes the open drawer.
 *
 * @param {{restoreFocus?: boolean}} [options]
 */
export function closeDrawer(options) {
  if (!openDrawerId) {
    return;
  }
  const drawerId = openDrawerId;
  const drawer = document.getElementById(drawerId);
  openDrawerId = null;

  if (drawer) {
    drawer.classList.remove("is-open");
    drawer.inert = true;
    window.setTimeout(() => {
      if (!openDrawerId) {
        drawer.hidden = true;
      }
    }, 260);
  }
  if (scrim) {
    scrim.classList.remove("is-open");
    window.setTimeout(() => {
      if (!openDrawerId) {
        scrim.hidden = true;
      }
    }, 260);
  }
  document.body.style.overflow = "";

  const restore = !options || options.restoreFocus !== false;
  if (restore && lastTrigger && lastTrigger.isConnected) {
    lastTrigger.focus({ preventScroll: true });
  }
  lastTrigger = null;

  const handler = closeHandlers.get(drawerId);
  if (handler) {
    handler();
  }
}
