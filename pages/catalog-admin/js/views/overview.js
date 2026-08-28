/**
 * Overview view: a single-screen control dashboard. Two headline figures
 * (catalog size + today’s draw activity), a completeness gauge, the source
 * distribution and the latest audit entries. Everything is derived from the
 * cached admin/summary payload, so opening this view never issues a second
 * request.
 */

import { h, qs, replaceChildren } from "../core/dom.js";
import { state } from "../core/state.js";
import { ensureSummary, onSummary, refreshSummary } from "../core/summary.js";
import { formatNumber } from "../core/format.js";
import { icon } from "../core/icons.js";
import {
  badge,
  renderBarList,
  renderHealthList,
  renderSkeletonStack,
  setButtonBusy,
} from "../ui/widgets.js";
import { toastError } from "../ui/toast.js";
import { renderAuditItems } from "./audit.js";

/** Percentage of entries that are *not* missing the given attribute. */
function completeness(total, missing) {
  const entries = Number(total) || 0;
  const gap = Number(missing) || 0;
  return ((entries - gap) * 100) / Math.max(1, entries);
}

/** Small uppercase caption with a leading glyph. */
function eyebrow(glyph, text) {
  return h("span", { class: "dash-eyebrow" }, icon(glyph), h("span", { text }));
}

/** One compact label/value pair used in the hero footer. */
function chip(label, value) {
  return h(
    "div",
    { class: "dash-chip" },
    h("span", { class: "dash-chip-label", text: label }),
    h("strong", { text: formatNumber(Number(value) || 0) })
  );
}

/** One label/value row used in the spotlight footer. */
function line(label, value) {
  return h(
    "div",
    { class: "dash-line" },
    h("span", { text: label }),
    h("strong", { text: formatNumber(Number(value) || 0) })
  );
}

/** Conic-gradient progress ring with a solid core sitting on top. */
function ring(percent, caption) {
  const value = Math.max(0, Math.min(100, Number(percent) || 0));
  return h(
    "div",
    {
      class: "dash-ring",
      style: { "--ring": String(Math.round(value * 10) / 10) },
      attrs: { role: "img", "aria-label": caption + " " + Math.round(value) + "%" },
    },
    h(
      "div",
      { class: "dash-ring-core" },
      h("strong", { text: Math.round(value) + "%" }),
      h("small", { text: caption })
    )
  );
}

/** Headline card: catalog size plus the blended completeness gauge. */
function renderHero(catalog) {
  const host = qs("#overviewHero");
  if (!host) {
    return;
  }
  const entries = Number(catalog.entries) || 0;
  const missing =
    (Number(catalog.missing_assets) || 0) + (Number(catalog.missing_descriptions) || 0);
  const blended = ((entries * 2 - missing) * 100) / Math.max(1, entries * 2);
  const sources = Array.isArray(catalog.sources) ? catalog.sources : [];
  const kinds = Array.isArray(catalog.kinds) ? catalog.kinds : [];

  replaceChildren(
    host,
    h(
      "div",
      { class: "dash-card-head" },
      eyebrow("library-big", "素材总量"),
      badge(formatNumber(Number(catalog.manual_entries) || 0) + " 项手动新增", "info", "wand-sparkles")
    ),
    h(
      "div",
      { class: "dash-hero-body" },
      h(
        "div",
        { class: "dash-figure" },
        h("span", { class: "dash-figure-value", text: formatNumber(entries) }),
        h("span", { class: "dash-figure-unit", text: "条图鉴条目" })
      ),
      ring(blended, "完整度")
    ),
    h(
      "div",
      { class: "dash-chip-row" },
      chip("收录作品", sources.length),
      chip("条目分类", kinds.length),
      chip("待补素材", missing)
    )
  );
}

/** Second headline card: how much the bot handed out today. */
function renderSpotlight(groups) {
  const host = qs("#overviewSpotlight");
  if (!host) {
    return;
  }
  replaceChildren(
    host,
    h(
      "div",
      { class: "dash-card-head" },
      eyebrow("dices", "今日抽取"),
      h("span", { class: "dash-live" }, h("span", { class: "dash-live-dot" }), h("span", { text: "实时" }))
    ),
    h(
      "div",
      { class: "dash-figure is-accent" },
      h("span", {
        class: "dash-figure-value",
        text: formatNumber(Number(groups.draws_today) || 0),
      }),
      h("span", { class: "dash-figure-unit", text: "次抽取已计入今日额度" })
    ),
    h(
      "div",
      { class: "dash-stack" },
      line("覆盖群组", groups.count),
      line("记录成员", groups.users),
      line("解锁记录", groups.unlock_records)
    )
  );
}

/** Compact stat strip pinned to the bottom of the completeness panel. */
function renderMiniStats(summary, catalog) {
  const host = qs("#overviewMetrics");
  if (!host) {
    return;
  }
  const terminology = summary.terminology || null;
  const wikiIndex = summary.wiki_index || null;
  const cells = [
    ["名称库词条", terminology ? Number(terminology.entries) || 0 : "未启用"],
    ["百科序号", wikiIndex ? Number(wikiIndex.total) || 0 : "未启用"],
    ["回收站", Number(summary.trash) || 0],
    ["缺少简介", Number(catalog.missing_descriptions) || 0],
  ];
  replaceChildren(
    host,
    ...cells.map(([label, value]) =>
      h(
        "div",
        { class: "dash-mini" },
        h("span", { class: "dash-mini-label", text: label }),
        h("strong", {
          class: "dash-mini-value",
          text: typeof value === "number" ? formatNumber(value) : value,
        })
      )
    )
  );
}

/** Data freshness stamp shown next to the dashboard title. */
function renderStamp(summary) {
  const host = qs("#overviewStamp");
  if (!host) {
    return;
  }
  const today = String(summary.today || "").trim();
  if (!today) {
    replaceChildren(host);
    return;
  }
  replaceChildren(
    host,
    icon("calendar-days", { size: 13 }),
    h("span", { text: "统计日期 " + today })
  );
}

/** Paints every dashboard block from state.summary. */
export function renderOverview() {
  const summary = state.summary;
  if (!summary) {
    return;
  }
  const catalog = summary.catalog || {};
  const groups = summary.groups || {};

  renderStamp(summary);
  renderHero(catalog);
  renderSpotlight(groups);
  renderMiniStats(summary, catalog);

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
    sources.slice(0, 14).map((item) => ({ label: item.name, value: item.count })),
    { empty: "暂无作品分布数据" }
  );

  renderAuditItems(qs("#recentAudit"), summary.recent_audit, {
    compact: true,
    limit: 12,
    empty: { glyph: "file-clock", title: "暂无管理操作" },
  });
}

/** Ensures the summary exists, then renders. */
export async function loadOverview() {
  if (!state.summary) {
    renderSkeletonStack(qs("#overviewHero"), 3);
    renderSkeletonStack(qs("#overviewSpotlight"), 3);
    renderSkeletonStack(qs("#overviewHealth"), 2);
    renderSkeletonStack(qs("#overviewSources"), 5);
    renderSkeletonStack(qs("#recentAudit"), 5);
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
