/**
 * Recycle bin view. Deleted catalog entries keep their numeric id, so
 * restoring one re-attaches every user unlock that referenced it.
 */

import { h, qs, clear, setHidden } from "../core/dom.js";
import { apiPost, apiGet } from "../core/bridge.js";
import { state, nextSequence, isCurrentSequence, invalidateView } from "../core/state.js";
import { formatDateTime, formatNumber } from "../core/format.js";
import { refreshSummary } from "../core/summary.js";
import {
  badge,
  badgeRow,
  primaryCell,
  renderEmptyState,
  renderSkeletonRows,
  rowAction,
} from "../ui/widgets.js";
import { confirmAction } from "../ui/confirm.js";
import { toast, toastError } from "../ui/toast.js";

/** Keeps the table bounded even if the bin grows large. */
const MAX_ROWS = 200;

function trashRow(record) {
  const affected = Number(record.affected_users) || 0;
  const restore = rowAction("archive-restore", "恢复该素材", () => restoreEntry(record));

  return h(
    "tr",
    null,
    h(
      "td",
      null,
      primaryCell(record.name || "未命名", "#" + record.id + (record.filename ? " · " + record.filename : "")),
      badgeRow(
        record.asset_present
          ? badge("图片已保留", "good", "image")
          : badge("图片已丢失", "bad", "image-off")
      )
    ),
    h("td", null, h("span", { text: record.source || "--" })),
    h(
      "td",
      null,
      h("span", { text: formatDateTime(record.deleted_at) }),
      h("span", { class: "cell-muted", text: record.deleted_by ? "由 " + record.deleted_by : "由 dashboard" })
    ),
    h(
      "td",
      null,
      affected
        ? badge(formatNumber(affected) + " 位用户", "warn", "users-round")
        : h("span", { class: "cell-muted", text: "无引用" })
    ),
    h("td", { class: "actions-column" }, restore)
  );
}

function renderTrash() {
  const rows = qs("#trashRows");
  const empty = qs("#trashEmpty");
  const items = state.trash.items;

  const label = qs("#trashCountLabel");
  if (label) {
    label.textContent = formatNumber(items.length) + " 条记录";
  }

  clear(rows);
  if (!items.length) {
    setHidden(empty, false);
    renderEmptyState(empty, {
      glyph: "archive",
      title: "回收站为空",
      message: "删除的素材会保留原编号和用户引用快照。",
      tall: true,
    });
    return;
  }
  setHidden(empty, true);
  clear(empty);
  for (const record of items.slice(0, MAX_ROWS)) {
    rows.appendChild(trashRow(record));
  }
}

async function restoreEntry(record) {
  const accepted = await confirmAction({
    title: "恢复该素材？",
    message:
      "恢复 #" +
      record.id +
      " " +
      (record.name || "未命名") +
      " 后，之前解锁过它的用户会重新看到该条目。",
    acceptLabel: "恢复",
    tone: "warning",
    glyph: "archive-restore",
  });
  if (!accepted) {
    return;
  }
  try {
    const entry = await apiPost("admin/trash/restore", { token: record.token });
    toast("已恢复素材", "#" + entry.id + " " + (entry.name || ""), "success");
  } catch (error) {
    toastError("恢复失败", error);
    return;
  }
  invalidateView("catalog", "audit");
  await Promise.all([
    loadTrash().catch((error) => console.error(error)),
    refreshSummary().catch((error) => console.error(error)),
  ]);
}

/** Fetches the recycle bin. */
export async function loadTrash() {
  const rows = qs("#trashRows");
  const empty = qs("#trashEmpty");
  const token = nextSequence("trash");
  setHidden(empty, true);
  renderSkeletonRows(rows, 5, 5);
  try {
    const payload = await apiGet("admin/trash");
    if (!isCurrentSequence("trash", token)) {
      return;
    }
    state.trash.items = Array.isArray(payload && payload.items) ? payload.items : [];
    renderTrash();
  } catch (error) {
    if (!isCurrentSequence("trash", token)) {
      return;
    }
    clear(rows);
    setHidden(empty, false);
    renderEmptyState(empty, {
      glyph: "circle-alert",
      title: "无法读取回收站",
      message: error && error.message ? error.message : "请稍后重试。",
      tall: true,
    });
    toastError("读取回收站失败", error);
  }
}

/** Nothing to bind statically; kept for symmetry with the other views. */
export function initTrash() {}
