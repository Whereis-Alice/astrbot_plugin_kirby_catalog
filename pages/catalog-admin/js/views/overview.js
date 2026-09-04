/**
 * Overview view: a single-screen control dashboard.
 *
 * The layout mirrors the rest of the console: a topline with the live stamp,
 * a hero band (catalog size + today's draw activity), a five-card metric strip
 * with inline visualisations, and three panels for source distribution, asset
 * health plus kind split, and the latest audit entries. All derived from cached
 * admin/summary payload, so opening this view never issues a second request.
 */

import { h, qs, replaceChildren, setText } from "../core/dom.js";
import { state } from "../core/state.js";
import { ensureSummary, onSummary, refreshSummary } from "../core/summary.js";
import { formatNumber } from "../core/format.js";
import { icon } from "../core/icons.js";
import { kindLabel } from "../core/labels.js";
import {
  badge,
  renderBarList,
  renderHealthList,
  renderMetrics,
  renderSkeletonStack,
  setButtonBusy,
} from "../ui/widgets.js";
import { toastError } from "../ui/toast.js";
import { renderAuditItems } from "./audit.js";

/** Coerces anything into a finite non-negative-ish number. */
function num(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : 0;
}

/** Percentage of entries that are *not* missing the given attribute. */
function completeness(total, missing) {
  const entries = num(total);
  return ((entries - num(missing)) * 100) / Math.max(1, entries);
}

/** Small uppercase caption with a leading glyph. */
function eyebrow(glyph, text) {
  return h("span", { class: "dash-eyebrow" }, icon(glyph), h("span", { text }));
}

/** One compact label/value pill used in the hero footer. */
function chip(label, value) {
  return h(
    "div",
    { class: "dash-chip" },
    h("span", { class: "dash-chip-label", text: label }),
    h("strong", { text: formatNumber(num(value)) })
  );
}

/** One label/value row used in the spotlight footer. */
function line(label, value, raw) {
  return h(
    "div",
    { class: "dash-line" },
    h("span", { text: label }),
    h("strong", { text: raw ? String(value) : formatNumber(num(value)) })
  );
}

/** One boxed label/value cell used beside the hero gauge. */
function mini(label, value) {
  return h(
    "div",
    { class: "dash-mini" },
    h("span", { class: "dash-mini-label", text: label }),
    h("strong", { class: "dash-mini-value", text: formatNumber(num(value)) })
  );
}

/** Conic-gradient progress ring with a solid core sitting on top. */
function ring(percent, caption) {
  const value = Math.max(0, Math.min(100, num(percent)));
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

/** Data freshness stamp shown in the topline. */
function renderStamp(summary) {
  const today = String(summary.today || "").trim();
  setText(qs("#overviewStamp"), today ? "统计日期 " + today : "同步完成");
}

/** Headline card: catalog size plus the blended completeness gauge. */
function renderHero(catalog, summary) {
  const host = qs("#overviewHero");
  if (!host) {
    return;
  }
  const entries = num(catalog.entries);
  const missing = num(catalog.missing_assets) + num(catalog.missing_descriptions);
  const blended = ((entries * 2 - missing) * 100) / Math.max(1, entries * 2);
  const sources = Array.isArray(catalog.sources) ? catalog.sources : [];
  const kinds = Array.isArray(catalog.kinds) ? catalog.kinds : [];

  replaceChildren(
    host,
    h(
      "div",
      { class: "dash-card-head" },
      eyebrow("library-big", "素材总量"),
      badge(formatNumber(num(catalog.manual_entries)) + " 项手动新增", "info", "wand-sparkles")
    ),
    h(
      "div",
      { class: "dash-hero-grid" },
      h(
        "div",
        { class: "dash-hero-main" },
        h(
          "div",
          { class: "dash-figure" },
          h("span", { class: "dash-figure-value", text: formatNumber(entries) }),
          h("span", { class: "dash-figure-unit", text: "条图鉴条目" })
        ),
        h(
          "div",
          { class: "dash-chip-row" },
          chip("收录作品", sources.length),
          chip("条目分类", kinds.length),
          chip("待补素材", missing)
        )
      ),
      h(
        "div",
        { class: "dash-hero-side" },
        ring(blended, "完整度"),
        h(
          "div",
          { class: "dash-mini-stats" },
          mini("缺少图片", catalog.missing_assets),
          mini("缺少简介", catalog.missing_descriptions),
          mini("回收站", summary.trash)
        )
      )
    )
  );
}

/** Second headline card: how much the bot handed out today. */
function renderSpotlight(groups) {
  const host = qs("#overviewSpotlight");
  if (!host) {
    return;
  }
  const users = num(groups.users);
  const unlocks = num(groups.unlock_records);
  replaceChildren(
    host,
    h(
      "div",
      { class: "dash-card-head" },
      eyebrow("dices", "今日抽取"),
      badge(formatNumber(users) + " 位成员在册", "info", "users-round")
    ),
    h(
      "div",
      { class: "dash-figure is-accent" },
      h("span", { class: "dash-figure-value", text: formatNumber(num(groups.draws_today)) }),
      h("span", { class: "dash-figure-unit", text: "次抽取已计入今日额度" })
    ),
    h(
      "div",
      { class: "dash-stack" },
      line("覆盖群组", groups.count),
      line("解锁记录", unlocks),
      line("人均解锁", (unlocks / Math.max(1, users)).toFixed(1), true)
    )
  );
}

/** Builds the name-library metric card, which may be disabled. */
function terminologyMetric(terminology) {
  if (!terminology) {
    return {
      label: "名称库词条",
      value: "未启用",
      glyph: "languages",
      color: "--purple",
      note: "未启用名称库",
    };
  }
  const entries = num(terminology.entries);
  const filled =
    entries * 3 -
    num(terminology.missing_zh) -
    num(terminology.missing_en) -
    num(terminology.missing_ja);
  return {
    label: "名称库词条",
    value: entries,
    glyph: "languages",
    color: "--purple",
    viz: {
      kind: "rate",
      percent: (filled * 100) / Math.max(1, entries * 3),
      caption: "三语完备",
    },
    note:
      "停用 " +
      formatNumber(Math.max(0, entries - num(terminology.enabled))) +
      " 条 · 冲突 " +
      formatNumber(num(terminology.conflicts)) +
      " 条",
  };
}

/** Builds the wiki-index metric card, which may be disabled. */
function wikiIndexMetric(wikiIndex) {
  if (!wikiIndex) {
    return {
      label: "百科序号",
      value: "未启用",
      glyph: "list-ordered",
      color: "--green",
      note: "未启用百科序号",
    };
  }
  const sites = wikiIndex.sites && typeof wikiIndex.sites === "object" ? wikiIndex.sites : {};
  const siteList = Object.keys(sites).map((key) => sites[key] || {});
  const total = num(wikiIndex.total);
  const enabled = siteList.reduce((sum, site) => sum + num(site.enabled), 0);
  return {
    label: "百科序号",
    value: total,
    glyph: "list-ordered",
    color: "--green",
    viz: {
      kind: "meter",
      rows: [
        { label: "启用率", percent: (enabled * 100) / Math.max(1, total) },
        { label: "自定义覆盖", percent: (num(wikiIndex.overrides) * 100) / Math.max(1, total) },
      ],
    },
    note:
      "覆盖 " +
      formatNumber(siteList.length) +
      " 个百科 · 冲突 " +
      formatNumber(num(wikiIndex.conflicts)) +
      " 条",
  };
}

/** Five-card strip with inline visualisations, all fed by real summary data. */
function renderMetricStrip(summary, catalog, groups) {
  const host = qs("#overviewMetrics");
  if (!host) {
    return;
  }
  const entries = num(catalog.entries);
  const missingAssets = num(catalog.missing_assets);
  const missingDescriptions = num(catalog.missing_descriptions);
  const missing = missingAssets + missingDescriptions;
  const blended = ((entries * 2 - missing) * 100) / Math.max(1, entries * 2);
  const sources = Array.isArray(catalog.sources) ? catalog.sources : [];
  const unlocks = num(groups.unlock_records);
  const draws = num(groups.draws_today);

  renderMetrics(host, [
    {
      label: "图鉴条目",
      value: entries,
      glyph: "library-big",
      color: "--accent",
      viz: { kind: "rate", percent: blended, caption: "资料完整度" },
      note:
        "手动新增 " +
        formatNumber(num(catalog.manual_entries)) +
        " 项 · 收录 " +
        formatNumber(sources.length) +
        " 部作品",
    },
    {
      label: "待补资料",
      value: missing,
      glyph: "image-off",
      color: "--orange",
      viz: {
        kind: "meter",
        rows: [
          { label: "图片补齐", percent: completeness(entries, missingAssets) },
          { label: "简介补齐", percent: completeness(entries, missingDescriptions) },
        ],
      },
      note:
        "缺图 " +
        formatNumber(missingAssets) +
        " 条 · 缺简介 " +
        formatNumber(missingDescriptions) +
        " 条",
    },
    {
      label: "今日抽取",
      value: draws,
      glyph: "dices",
      color: "--cyan",
      viz: {
        kind: "ruler",
        percent: (draws * 100) / Math.max(1, unlocks),
        min: "0",
        max: formatNumber(unlocks),
      },
      note:
        "覆盖 " +
        formatNumber(num(groups.count)) +
        " 个群 · " +
        formatNumber(num(groups.users)) +
        " 位成员",
    },
    terminologyMetric(summary.terminology || null),
    wikiIndexMetric(summary.wiki_index || null),
  ]);
}

/**
 * Completeness rows for the health panel. The catalog rows are always present;
 * the terminology and wiki-index rows only appear when those optional modules
 * are enabled, so the panel never shows a bar backed by missing data.
 */
function healthRows(summary, catalog) {
  const rows = [
    {
      label: "素材图片",
      color: "--cyan",
      missing: num(catalog.missing_assets),
      percent: completeness(catalog.entries, catalog.missing_assets),
    },
    {
      label: "简体中文简介",
      color: "--green",
      missing: num(catalog.missing_descriptions),
      percent: completeness(catalog.entries, catalog.missing_descriptions),
    },
  ];

  const terminology = summary.terminology;
  if (terminology) {
    const total = num(terminology.entries);
    const missing =
      num(terminology.missing_zh) + num(terminology.missing_en) + num(terminology.missing_ja);
    rows.push({
      label: "名称库三语",
      color: "--purple",
      missing,
      percent: ((total * 3 - missing) * 100) / Math.max(1, total * 3),
    });
  }

  const wikiIndex = summary.wiki_index;
  if (wikiIndex) {
    const sites = wikiIndex.sites && typeof wikiIndex.sites === "object" ? wikiIndex.sites : {};
    const total = num(wikiIndex.total);
    const enabled = Object.keys(sites).reduce((sum, key) => sum + num((sites[key] || {}).enabled), 0);
    rows.push({
      label: "百科序号启用",
      color: "--yellow",
      missing: Math.max(0, total - enabled),
      percent: (enabled * 100) / Math.max(1, total),
    });
  }

  return rows;
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
  renderHero(catalog, summary);
  renderSpotlight(groups);
  renderMetricStrip(summary, catalog, groups);

  renderHealthList(qs("#overviewHealth"), healthRows(summary, catalog));

  const sources = Array.isArray(catalog.sources) ? catalog.sources : [];
  renderBarList(
    qs("#overviewSources"),
    sources.slice(0, 14).map((item) => ({ label: item.name, value: item.count })),
    { empty: "暂无作品分布数据" }
  );

  const kinds = Array.isArray(catalog.kinds) ? catalog.kinds : [];
  renderBarList(
    qs("#overviewKinds"),
    kinds.map((item) => ({ label: kindLabel(item.name), value: item.count })),
    { empty: "暂无分类数据" }
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
    renderSkeletonStack(qs("#overviewMetrics"), 5);
    renderSkeletonStack(qs("#overviewHealth"), 4);
    renderSkeletonStack(qs("#overviewSources"), 5);
    renderSkeletonStack(qs("#overviewKinds"), 4);
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
