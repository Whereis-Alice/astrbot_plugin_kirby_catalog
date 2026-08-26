/**
 * Wiki index view: the per-site number -> query target table.
 *
 * The site list and the metric cards are both derived from the payload, so a
 * newly bundled wiki shows up without touching this file.
 */

import { h, qs, clear, setHidden, replaceChildren, setText } from "../core/dom.js";
import { apiGet, apiPost } from "../core/bridge.js";
import { state, nextSequence, isCurrentSequence, invalidateView } from "../core/state.js";
import { debounce, formatNumber, formatDateTime, clampNumber } from "../core/format.js";
import { wikiSiteLabel, wikiSiteIcon, wikiSiteColor } from "../core/labels.js";
import { onSummary, refreshSummary } from "../core/summary.js";
import {
  badge,
  badgeRow,
  formSection,
  metadataList,
  renderEmptyState,
  renderMetrics,
  renderSkeletonRows,
  renderSkeletonStack,
  setButtonBusy,
} from "../ui/widgets.js";
import { renderPagination } from "../ui/pagination.js";
import { openDrawer, closeDrawer, onDrawerClose } from "../ui/drawer.js";
import { confirmAction } from "../ui/confirm.js";
import { toast, toastError } from "../ui/toast.js";

const PAGE_SIZE = 30;

/** Signature of the option set currently rendered into the site select. */
let siteSignature = null;

function filters() {
  return state.wikiIndex.filters;
}

/* ==========================================================================
   Metrics + filter options
   ========================================================================== */

/** One card for the grand total, one per wiki site, one for the overrides. */
function renderWikiIndexMetrics() {
  const container = qs("#wikiIndexMetrics");
  const stats = state.wikiIndex.stats || {};
  const sites = (stats && stats.sites) || {};
  const conflicts = Number(stats.conflicts) || 0;

  const cards = [
    {
      label: "序号总数",
      value: Number(stats.total) || 0,
      note: "三套百科独立编号",
      glyph: "list-ordered",
      color: "--accent",
    },
  ];

  const keys = Object.keys(sites);
  keys.forEach((site, index) => {
    const info = sites[site] || {};
    cards.push({
      label: info.label || wikiSiteLabel(site, state.wikiIndex.sites),
      value: Number(info.total) || 0,
      note: "启用 " + formatNumber(Number(info.enabled) || 0),
      glyph: wikiSiteIcon(site),
      color: wikiSiteColor(site, index),
    });
  });

  cards.push({
    label: "管理员修改",
    value: Number(stats.overrides) || 0,
    note: "编号冲突 " + formatNumber(conflicts),
    glyph: conflicts ? "triangle-alert" : "pencil-line",
    color: conflicts ? "--red" : "--purple",
  });

  renderMetrics(container, cards);
}

/** Rebuilds the site select from payload.sites, keeping the "all" option. */
function syncSiteOptions(sites) {
  const select = qs("#wikiIndexSiteFilter");
  if (!select) {
    return;
  }
  const list = Array.isArray(sites) ? sites : [];
  const signature = list.map((item) => item.value + ":" + item.label).join("|");
  if (signature === siteSignature) {
    return;
  }
  siteSignature = signature;
  while (select.options.length > 1) {
    select.remove(1);
  }
  for (const site of list) {
    select.appendChild(h("option", { value: site.value, text: site.label || site.value }));
  }
  const wanted = filters().site || "";
  select.value = wanted;
  if (wanted && select.value !== wanted) {
    select.appendChild(h("option", { value: wanted, text: wanted }));
    select.value = wanted;
  }
}

/* ==========================================================================
   Table
   ========================================================================== */

function identityCell(row) {
  return h(
    "div",
    { class: "wiki-index-identity" },
    h("strong", { text: row.label_zh || row.label_en || row.key }),
    h("span", {
      text: [row.site_label || wikiSiteLabel(row.site, state.wikiIndex.sites), row.label_en, row.label_ja]
        .filter(Boolean)
        .join(" · "),
    })
  );
}

function statusCell(row) {
  return badgeRow(
    row.enabled ? badge("可查询", "good", "circle-check") : badge("已停用", "bad", "ban"),
    row.conflict ? badge("编号冲突", "bad", "triangle-alert") : null,
    row.context ? badge(row.context, "info", "layers") : null
  );
}

function originCell(row) {
  if (!row.has_override) {
    return badgeRow(badge("内置", "good", "book-open"));
  }
  const stamp = row.updated_at ? formatDateTime(row.updated_at) : "";
  return badgeRow(
    badge("已修改", "warn", "pencil-line"),
    stamp ? badge(stamp, "info", "clock") : null
  );
}

function wikiIndexRow(row) {
  const node = h(
    "tr",
    {
      class: "is-clickable",
      role: "button",
      tabindex: "0",
      "aria-label": "编辑 " + (row.site_label || row.site) + " 第 " + row.number + " 条",
    },
    h("td", null, h("span", { class: "wiki-index-number", text: "#" + row.number })),
    h("td", null, identityCell(row)),
    h("td", null, h("div", { class: "wiki-index-target", text: row.target || "--" })),
    h("td", null, statusCell(row)),
    h("td", null, originCell(row))
  );

  const open = () => openWikiIndexDrawer(row.site, row.key);
  node.addEventListener("click", open);
  node.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      open();
    }
  });
  return node;
}

function renderWikiIndex() {
  const tbody = qs("#wikiIndexRows");
  const empty = qs("#wikiIndexEmpty");
  const items = state.wikiIndex.items;

  setText(qs("#wikiIndexTotalLabel"), formatNumber(state.wikiIndex.total) + " 条");

  clear(tbody);
  if (!items.length) {
    setHidden(empty, false);
    renderEmptyState(empty, {
      glyph: "search-x",
      title: "没有匹配的百科序号",
      message: "调整百科、状态或搜索条件",
      tall: true,
    });
  } else {
    setHidden(empty, true);
    clear(empty);
    for (const row of items) {
      tbody.appendChild(wikiIndexRow(row));
    }
  }

  renderPagination(
    qs("#wikiIndexPagination"),
    { page: state.wikiIndex.page, pages: state.wikiIndex.pages, total: state.wikiIndex.total },
    (page) => {
      state.wikiIndex.page = page;
      loadWikiIndex();
    },
    { unit: "条序号" }
  );
}

/** Fetches one page of wiki index rows. */
export async function loadWikiIndex() {
  const tbody = qs("#wikiIndexRows");
  const empty = qs("#wikiIndexEmpty");
  const active = filters();
  const token = nextSequence("wikiIndex");

  setHidden(empty, true);
  renderSkeletonRows(tbody, 6, 5);

  let payload = null;
  try {
    payload = await apiGet("admin/wiki-index", {
      site: active.site,
      query: active.query,
      status: active.status,
      sort: active.sort,
      page: state.wikiIndex.page,
      page_size: PAGE_SIZE,
    });
  } catch (error) {
    if (!isCurrentSequence("wikiIndex", token)) {
      return;
    }
    clear(tbody);
    setHidden(empty, false);
    renderEmptyState(empty, {
      glyph: "circle-alert",
      title: "无法读取百科序号",
      message: error && error.message ? error.message : "请稍后重试。",
      tall: true,
    });
    toastError("读取百科序号失败", error);
    throw error;
  }
  if (!isCurrentSequence("wikiIndex", token)) {
    return;
  }

  const items = Array.isArray(payload && payload.items) ? payload.items : [];
  const total = Number(payload && payload.total) || 0;
  const pages = Math.max(1, Number(payload && payload.pages) || 1);

  if (total > 0 && !items.length && state.wikiIndex.page > pages) {
    state.wikiIndex.page = pages;
    return loadWikiIndex();
  }

  state.wikiIndex.items = items;
  state.wikiIndex.total = total;
  state.wikiIndex.pages = pages;
  state.wikiIndex.page = Math.min(pages, Math.max(1, Number(payload && payload.page) || 1));
  state.wikiIndex.sites = Array.isArray(payload && payload.sites) ? payload.sites : [];
  state.wikiIndex.stats = (payload && payload.stats) || { total: 0, overrides: 0, conflicts: 0, sites: {} };
  syncSiteOptions(state.wikiIndex.sites);
  renderWikiIndexMetrics();
  renderWikiIndex();
}

function reloadFromFirstPage() {
  state.wikiIndex.page = 1;
  loadWikiIndex().catch((error) => console.error(error));
}

/* ==========================================================================
   Drawer
   ========================================================================== */

/** metadataList() with an extra class so the drawer spacing rules apply. */
function labelledList(rows, className) {
  const node = metadataList(rows);
  if (node) {
    node.classList.add(className);
  }
  return node;
}

function renderWikiIndexDrawer(row) {
  const body = qs("#wikiIndexDrawerBody");
  if (!body) {
    return;
  }
  const siteLabel = row.site_label || wikiSiteLabel(row.site, state.wikiIndex.sites);
  const titleNode = qs("#wikiIndexDrawerTitle");
  if (titleNode) {
    setText(titleNode, siteLabel + " #" + row.number);
  }

  const numberInput = h("input", {
    type: "number",
    class: "input",
    id: "wikiIndexNumberInput",
    min: "1",
    max: "999999",
    value: String(Number(row.number) || 1),
  });
  const keyInput = h("input", {
    type: "text",
    class: "input",
    id: "wikiIndexKeyInput",
    readOnly: true,
    value: row.key || "",
  });
  const targetInput = h("textarea", {
    class: "textarea",
    id: "wikiIndexTargetInput",
    rows: "4",
    maxlength: "500",
    value: row.target || "",
  });
  const enabledInput = h("input", {
    type: "checkbox",
    id: "wikiIndexEnabledInput",
    checked: row.enabled !== false,
  });

  const stamp = row.updated_at
    ? "最后修改：" + formatDateTime(row.updated_at) + " · " + (row.updated_by || "dashboard")
    : "";

  replaceChildren(
    body,
    h(
      "div",
      { class: "wiki-index-summary" },
      h("h3", { text: row.label_zh || row.target || row.key }),
      h("p", { text: row.has_override ? "管理员覆盖版本" : "插件内置版本" }),
      badgeRow(
        badge(siteLabel, "info", wikiSiteIcon(row.site)),
        row.enabled !== false ? badge("可查询", "good", "circle-check") : badge("已停用", "bad", "ban"),
        row.conflict ? badge("编号冲突", "bad", "triangle-alert") : null
      ),
      labelledList(
        [
          { label: "简体中文", value: row.label_zh },
          { label: "English", value: row.label_en },
          { label: "日本語", value: row.label_ja },
          { label: "条目范围", value: row.context },
        ],
        "wiki-index-language-list"
      )
    ),
    formSection(
      "查询设置",
      "仅影响当前百科",
      h(
        "div",
        { class: "form-grid two-columns" },
        h("label", { class: "field" }, h("span", { text: "序号" }), numberInput),
        h("label", { class: "field" }, h("span", { text: "固定条目键" }), keyInput)
      ),
      h(
        "label",
        { class: "field" },
        h("span", { text: "查询目标" }),
        targetInput,
        h("span", { class: "field-hint", text: "用户输入该序号时实际查询的百科条目名。" })
      ),
      h("label", { class: "check-field" }, enabledInput, h("span", { text: "允许通过该序号查询" }))
    ),
    formSection(
      "内置值对照",
      "恢复时使用",
      labelledList(
        [
          { label: "内置序号", value: row.default_number },
          { label: "内置查询目标", value: row.default_target },
          { label: "内置状态", value: row.default_enabled ? "启用" : "停用" },
        ],
        "wiki-index-defaults"
      ),
      stamp ? h("span", { class: "field-hint", text: stamp }) : null
    )
  );

  const restoreButton = qs("#restoreWikiIndexButton");
  if (restoreButton) {
    restoreButton.hidden = !row.has_override;
  }
}

/** Loads one row and shows the drawer. */
export async function openWikiIndexDrawer(site, key) {
  const body = qs("#wikiIndexDrawerBody");
  renderSkeletonStack(body, 7);
  openDrawer("wikiIndexDrawer");
  try {
    const row = await apiGet("admin/wiki-index-entry", { site: site, key: key });
    state.activeWikiRow = row;
    renderWikiIndexDrawer(row);
  } catch (error) {
    toastError("读取百科序号失败", error);
    closeDrawer();
  }
}

/* ==========================================================================
   Writes
   ========================================================================== */

async function saveWikiIndex() {
  const row = state.activeWikiRow;
  if (!row) {
    return;
  }
  const number = clampNumber((qs("#wikiIndexNumberInput") || {}).value, 1, 999999, Number(row.number) || 1);
  const target = ((qs("#wikiIndexTargetInput") || {}).value || "").trim();
  if (!target) {
    toast("请填写查询目标", "查询目标不能为空。", "warning");
    const input = qs("#wikiIndexTargetInput");
    if (input) {
      input.focus();
    }
    return;
  }

  const button = qs("#saveWikiIndexButton");
  setButtonBusy(button, true, "保存中");
  let updated = null;
  try {
    updated = await apiPost("admin/wiki-index/save", {
      site: row.site,
      key: row.key,
      number: number,
      target: target,
      enabled: Boolean((qs("#wikiIndexEnabledInput") || {}).checked),
    });
  } catch (error) {
    toastError("保存百科序号失败", error);
    return;
  } finally {
    setButtonBusy(button, false);
  }

  toast("已保存百科序号", (updated.site_label || updated.site) + " #" + updated.number, "success");
  state.activeWikiRow = updated;
  renderWikiIndexDrawer(updated);
  invalidateView("audit");
  await Promise.all([
    loadWikiIndex().catch((error) => console.error(error)),
    refreshSummary().catch((error) => console.error(error)),
  ]);
}

async function restoreWikiIndex() {
  const row = state.activeWikiRow;
  if (!row || !row.has_override) {
    return;
  }
  const accepted = await confirmAction({
    title: "恢复内置序号",
    message:
      "将丢弃对 " +
      (row.site_label || row.site) +
      " " +
      (row.label_zh || row.key) +
      " 的修改，改用内置序号 #" +
      row.default_number +
      "。",
    acceptLabel: "恢复",
    tone: "warning",
    glyph: "rotate-ccw",
  });
  if (!accepted) {
    return;
  }

  const button = qs("#restoreWikiIndexButton");
  setButtonBusy(button, true, "恢复中");
  let restored = null;
  try {
    restored = await apiPost("admin/wiki-index/restore", { site: row.site, key: row.key });
  } catch (error) {
    toastError("恢复失败", error);
    return;
  } finally {
    setButtonBusy(button, false);
  }

  toast("已恢复内置序号", (restored.site_label || restored.site) + " #" + restored.number, "success");
  state.activeWikiRow = restored;
  renderWikiIndexDrawer(restored);
  invalidateView("audit");
  await Promise.all([
    loadWikiIndex().catch((error) => console.error(error)),
    refreshSummary().catch((error) => console.error(error)),
  ]);
}

/* ==========================================================================
   Wiring
   ========================================================================== */

/** Binds the wiki index toolbar and drawer footer exactly once. */
export function initWikiIndex() {
  onSummary(() => {
    const summary = state.summary || {};
    if (summary.wiki_index) {
      state.wikiIndex.stats = summary.wiki_index;
      renderWikiIndexMetrics();
    }
  });

  const search = qs("#wikiIndexSearch");
  if (search) {
    const run = debounce(() => reloadFromFirstPage(), 280);
    search.addEventListener("input", () => {
      filters().query = search.value.trim();
      run();
    });
  }

  const selectBindings = [
    ["#wikiIndexSiteFilter", "site"],
    ["#wikiIndexStatusFilter", "status"],
    ["#wikiIndexSort", "sort"],
  ];
  for (const binding of selectBindings) {
    const select = qs(binding[0]);
    if (!select) {
      continue;
    }
    select.value = filters()[binding[1]];
    select.addEventListener("change", () => {
      filters()[binding[1]] = select.value;
      reloadFromFirstPage();
    });
  }

  const saveButton = qs("#saveWikiIndexButton");
  if (saveButton) {
    saveButton.addEventListener("click", () => saveWikiIndex());
  }
  const restoreButton = qs("#restoreWikiIndexButton");
  if (restoreButton) {
    restoreButton.addEventListener("click", () => restoreWikiIndex());
  }
  onDrawerClose("wikiIndexDrawer", () => {
    state.activeWikiRow = null;
  });
}
