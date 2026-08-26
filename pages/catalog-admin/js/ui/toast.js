/** Transient notifications. */

import { h, clear } from "../core/dom.js";
import { icon } from "../core/icons.js";

const ICONS = Object.freeze({
  error: "circle-alert",
  warning: "triangle-alert",
  success: "circle-check",
  info: "info",
});

let region = null;

/** Caches the toast container. */
export function initToasts() {
  region = document.getElementById("toastRegion");
}

/**
 * Shows a toast.
 *
 * @param {string} title
 * @param {string} [message]
 * @param {"info"|"success"|"warning"|"error"} [type="info"]
 */
export function toast(title, message, type) {
  if (!region) {
    initToasts();
  }
  if (!region) {
    return;
  }
  const kind = ICONS[type] ? type : "info";
  const duration = kind === "error" ? 7000 : 4200;

  const node = h(
    "div",
    { class: "toast is-" + kind },
    h("span", { class: "toast-icon" }, icon(ICONS[kind])),
    h(
      "div",
      { class: "toast-copy" },
      h("strong", { text: title || "" }),
      message ? h("span", { text: message }) : null
    ),
    h(
      "button",
      { type: "button", class: "toast-close", "aria-label": "关闭提示" },
      icon("x")
    ),
    h("span", { class: "toast-timer", style: { "animation-duration": duration + "ms" } })
  );

  let timer = 0;
  const dismiss = () => {
    window.clearTimeout(timer);
    if (!node.isConnected) {
      return;
    }
    node.classList.add("is-leaving");
    window.setTimeout(() => node.remove(), 220);
  };

  node.querySelector(".toast-close").addEventListener("click", dismiss);
  region.appendChild(node);

  // Keep the region bounded; stacked toasts are noise after four.
  while (region.children.length > 4) {
    region.firstElementChild.remove();
  }

  timer = window.setTimeout(dismiss, duration);
  return dismiss;
}

/** Convenience wrapper for caught errors. */
export function toastError(title, error) {
  const message = error && error.message ? error.message : String(error || "未知错误");
  toast(title, message, "error");
}

/** Removes every visible toast. */
export function clearToasts() {
  if (region) {
    clear(region);
  }
}
