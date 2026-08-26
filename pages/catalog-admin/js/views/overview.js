/**
 * Overview view: metric strip, completeness bars, source distribution and the
 * most recent dashboard actions. Everything is derived from the cached
 * admin/summary payload, so opening this view never issues a second request.
 */

import { qs } from "../core/dom.js";
import { state } from "../core/state.js";
import { ensureSummary, onSummary, refreshSummary } from "../core/summary.js";
import { formatNumber } from "../core/format.js";
import {
  renderBarList,
  renderHealthList,
  renderMetrics,
  renderSkeletonStack,
  setButtonBusy,
} from "../ui/widgets.js";
import { toastError } from "../ui/toast.js";
import { renderAuditItems } from "./audit.js";

function completeness(total, missing) {
  const entries = Number(total) || 0;
  const gap = Number(missing) || 0;
  return ((entries - gap) * 100) / Math.max(1, entries);
}

/** Paints every overview block from state.summary. */
export function renderOverview() {
  const summary = state.summary;
  if (!summary) {
    return;
  }
  const catalog = summary.catalog || {};
  const groups = summary.groups || {};

  renderMetrics(qs("#overviewMetrics"), [
    {
      label: "图鉴条目",
      value: Number(catalog.entries) || 0,
      note: formatNumber(catalog.manual_entries) + " 项手动新增",
      glyph: "library-big",
      color: "--accent",
    },
    {
      label: "群组",
      value: Number(groups.count) || 0,
      note: formatNumber(groups.users) + " 位成员",
      glyph: "messages-square",
      color: "--cyan",
    },
    {
      label: "解锁记录",
      value: Number(groups.unlock_records) || 0,
      note: "全部群历史记录",
      glyph: "badge-check",
      color: "--green",
    },
    {
      label: "今日抽取",
      value: Number(groups.draws_today) || 0,
      note: "已使用次数",
      glyph: "dices",
      color: "--yellow",
    },
    {
      label: "回收站",
      value: Number(summary.trash) || 0,
      note: "编号仍被保留",
      glyph: "archive",
      color: "--purple",
    },
  ]);

  renderHealthList(qs("#overviewHealth"), [
    {
      label: "素材图片",
      color: "--cyan",
      missing: Number(catalog.missing_assets) || 0,
      percent: completeness(catalog.entries, catalog.missing_assets),
    },
    {
      label: "简体中文简介",
      color: "--green",
      missing: Number(catalog.missing_descriptions) || 0,
      percent: completeness(catalog.entries, catalog.missing_descriptions),
    },
  ]);

  const sources = Array.isArray(catalog.sources) ? catalog.sources : [];
  renderBarList(
    qs("#overviewSources"),
    sources.slice(0, 8).map((item) => ({ label: item.name, value: item.count })),
    { empty: "暂无作品分布数据" }
  );

  renderAuditItems(qs("#recentAudit"), summary.recent_audit, {
    compact: true,
    limit: 8,
    empty: { glyph: "file-clock", title: "暂无管理操作" },
  });
}

/** Ensures the summary exists, then renders. */
export async function loadOverview() {
  if (!state.summary) {
    renderSkeletonStack(qs("#overviewHealth"), 2);
    renderSkeletonStack(qs("#overviewSources"), 4);
    renderSkeletonStack(qs("#recentAudit"), 4);
  }
  await ensureSummary();
  renderOverview();
}

/** Binds the refresh button and subscribes to summary updates. */
export function initOverview() {
  onSummary(renderOverview);

  const button = qs("#refreshSummaryButton");
  if (!button) {
    return;
  }
  button.addEventListener("click", async () => {
    setButtonBusy(button, true, "刷新中");
    try {
      await refreshSummary();
    } catch (error) {
      toastError("刷新失败", error);
    } finally {
      setButtonBusy(button, false);
    }
  });
}
