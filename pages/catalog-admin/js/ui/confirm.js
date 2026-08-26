/**
 * Promise-based confirmation dialog.
 *
 * A single <dialog> is reused. Calling it while another confirmation is still
 * open used to throw InvalidStateError, so any open dialog is closed (and its
 * promise resolved as cancelled) before the new one is shown.
 */

import { swapIcon } from "../core/icons.js";
import { setText } from "../core/dom.js";

let dialog = null;
let iconHost = null;
let titleNode = null;
let messageNode = null;
let acceptButton = null;
let cancelButton = null;
let pendingResolve = null;

function settle(result) {
  const resolve = pendingResolve;
  pendingResolve = null;
  if (resolve) {
    resolve(result);
  }
}

/** Wires the dialog once at boot. */
export function initConfirm() {
  dialog = document.getElementById("confirmDialog");
  if (!dialog) {
    return;
  }
  iconHost = document.getElementById("confirmIcon");
  titleNode = document.getElementById("confirmTitle");
  messageNode = document.getElementById("confirmMessage");
  acceptButton = document.getElementById("confirmAcceptButton");
  cancelButton = document.getElementById("confirmCancelButton");

  acceptButton.addEventListener("click", () => {
    dialog.close("accept");
  });
  cancelButton.addEventListener("click", () => {
    dialog.close("cancel");
  });
  dialog.addEventListener("close", () => {
    settle(dialog.returnValue === "accept");
  });
  dialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    dialog.close("cancel");
  });
}

/**
 * @param {{title: string, message?: string, acceptLabel?: string,
 *          cancelLabel?: string, tone?: "danger"|"warning", glyph?: string}} options
 * @returns {Promise<boolean>}
 */
export function confirmAction(options) {
  if (!dialog) {
    initConfirm();
  }
  if (!dialog) {
    return Promise.resolve(false);
  }

  // Resolve and tear down any dialog still on screen before reopening.
  if (dialog.open) {
    dialog.close("cancel");
  }

  const opts = options || {};
  setText(titleNode, opts.title || "确认操作");
  setText(messageNode, opts.message || "此操作不可撤销。");
  setText(acceptButton, opts.acceptLabel || "确认");
  setText(cancelButton, opts.cancelLabel || "取消");

  const warning = opts.tone === "warning";
  iconHost.classList.toggle("is-warning", warning);
  swapIcon(iconHost, opts.glyph || "triangle-alert");
  acceptButton.className = "button " + (warning ? "warning" : "danger");

  return new Promise((resolve) => {
    pendingResolve = resolve;
    dialog.returnValue = "";
    dialog.showModal();
    cancelButton.focus();
  });
}
