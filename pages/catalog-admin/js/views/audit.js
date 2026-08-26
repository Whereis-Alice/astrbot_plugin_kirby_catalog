/**
 * Audit trail view.
 *
 * Also exports the shared audit renderer used by the overview card, so both
 * places stay visually identical and DOM-bounded.
 */

import { h, qs, replaceChildren, clear, setHidden } from "../core/dom.js";
import { icon } from "../core/icons.js";
import { apiGet } from "../core/bridge.js";
import { state, nextSequence, isCurrentSequence } from "../core/state.js";
import { actionLabel, actionIcon } from "../core/labels.js";
import { formatDateTime } from "../core/format.js";
import { renderEmptyState, renderSkeletonStack, setButtonBusy } from "../ui/widgets.js";
import { toastError } from "../ui/toast.js";

/** Hard cap so a chatty audit log cannot balloon the DOM. */
const MAX_RENDERED = 200;

function metaRow(record) {
  const parts = [];
  if (record.target) {
    parts.push(h("span", { text: String(record.target) }));
  }
  if (record.username) {
    parts.push(h("span", { text: String(record.username) }));
  }
  parts.push(h("span", { text: formatDateTime(record.timestamp) }));

  const children = [];
  parts.forEach((node, index) => {
    if (index > 0) {
      children.push(h("span", { class: "dot", "aria-hidden": "true" }));
    }
    children.push(node);
  });
  return h("div", { class: "audit-meta" }, children);
}

function auditItem(record) {
  return h(
    "article",
    { class: "audit-item" },
    h("span", { class: "audit-icon" }, icon(actionIcon(record.action))),
    h(
      "div",
      { class: "audit-copy" },
      h("span", { class: "audit-title", text: actionLabel(record.action) }),
      record.summary ? h("span", { class: "audit-summary", text: String(record.summary) }) : null,
      metaRow(record)
    )
  );
}

/**
 * Renders an audit list into a container.
 *
 * @param {Element} container
 * @param {Array<Object>} items
 * @param {{compact?: boolean, limit?: number, empty?: Object}} [options]
 */
export function renderAuditItems(container, items, options) {
  if (!container) {
    return;
  }
  const opts = options || {};
  const records = Array.isArray(items) ? items : [];
  if (!records.length) {
    renderEmptyState(container, opts.empty || { glyph: "file-clock", title: "暂无管理操作" });
    return;
  }
  const bounded = records.slice(0, opts.limit || MAX_RENDERED);
  const list = h(
    "div",
    { class: opts.compact ? "audit-list compact" : "audit-list" },
    bounded.map((record) => auditItem(record))
  );
  replaceChildren(container, list);
}

function renderAuditView() {
  const rows = qs("#auditRows");
  const empty = qs("#auditEmpty");
  const items = state.audit.items;
  if (!items.length) {
    clear(rows);
    setHidden(empty, false);
    renderEmptyState(empty, {
      glyph: "file-clock",
      title: "暂无管理操作",
      message: "在 Dashboard 中修改素材、成员或名称库后会记录在这里。",
      tall: true,
    });
    return;
  }
  setHidden(empty, true);
  clear(empty);
  renderAuditItems(rows, items);
}

/** Fetches the audit trail. Stale responses are discarded. */
export async function loadAudit() {
  const rows = qs("#auditRows");
  const empty = qs("#auditEmpty");
  const token = nextSequence("audit");
  setHidden(empty, true);
  renderSkeletonStack(rows, 8);
  try {
    const payload = await apiGet("admin/audit", { limit: state.audit.limit });
    if (!isCurrentSequence("audit", token)) {
      return;
    }
    state.audit.items = Array.isArray(payload && payload.items) ? payload.items : [];
    renderAuditView();
  } catch (error) {
    if (!isCurrentSequence("audit", token)) {
      return;
    }
    clear(rows);
    setHidden(empty, false);
    renderEmptyState(empty, {
      glyph: "circle-alert",
      title: "无法读取操作记录",
      message: error && error.message ? error.message : "请稍后重试。",
      tall: true,
    });
    toastError("读取操作记录失败", error);
  }
}

/** Binds the refresh button. */
export function initAudit() {
  const button = qs("#refreshAuditButton");
  if (!button) {
    return;
  }
  button.addEventListener("click", async () => {
    setButtonBusy(button, true, "刷新中");
    try {
      await loadAudit();
      state.loaded.add("audit");
    } finally {
      setButtonBusy(button, false);
    }
  });
}
