/**
 * Terminology view: the bundled + override name library.
 *
 * Mirrors catalog.js: filters live in state, the category select is only
 * rebuilt when the payload changes, and every write revalidates the page so a
 * deletion cannot leave an empty table behind.
 */

import { h, qs, clear, setHidden, replaceChildren, setText } from "../core/dom.js";
import { apiGet, apiPost, apiUpload, apiDownload } from "../core/bridge.js";
import { state, nextSequence, isCurrentSequence, invalidateView } from "../core/state.js";
import { debounce, formatNumber, splitLines } from "../core/format.js";
import {
  terminologyCategoryLabel,
  terminologyOriginLabel,
  terminologyStatusLabel,
} from "../core/labels.js";
import { onSummary, refreshSummary } from "../core/summary.js";
import {
  badge,
  badgeRow,
  formSection,
  primaryCell,
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
const MAX_NOTES_CHARS = 5000;

const ZH_STATUS_OPTIONS = [
  "official",
  "official_reused",
  "project",
  "transliterated",
  "unchanged",
  "unknown",
];

/** Signature of the option set currently rendered into the category select. */
let categorySignature = null;

function filters() {
  return state.terminology.filters;
}

/* ==========================================================================
   Metrics + filter options
   ========================================================================== */

/** Paints the six-card metric strip from summary.terminology (may be null). */
function renderTerminologyMetrics() {
  const container = qs("#terminologyMetrics");
  const summary = state.summary || {};
  const stats = summary.terminology || null;
  if (!stats) {
    renderMetrics(container, [
      { label: "名称库", value: "未启用", note: "插件未加载名称库", glyph: "languages", color: "--ink-muted" },
    ]);
    return;
  }
  renderMetrics(container, [
    {
      label: "词条总数",
      value: Number(stats.entries) || 0,
      note: "启用 " + formatNumber(Number(stats.enabled) || 0),
      glyph: "languages",
      color: "--cyan",
    },
    {
      label: "已覆盖",
      value: Number(stats.overrides) || 0,
      note: "自定义 " + formatNumber(Number(stats.custom) || 0),
      glyph: "pencil-line",
      color: "--accent",
    },
    {
      label: "缺少中文",
      value: Number(stats.missing_zh) || 0,
      note: "优先补全显示名称",
      glyph: "languages",
      color: "--orange",
    },
    {
      label: "缺少英文",
      value: Number(stats.missing_en) || 0,
      note: "影响双语输出",
      glyph: "type",
      color: "--yellow",
    },
    {
      label: "缺少日文",
      value: Number(stats.missing_ja) || 0,
      note: "影响原文检索",
      glyph: "text-cursor-input",
      color: "--green",
    },
    {
      label: "匹配冲突",
      value: Number(stats.conflicts) || 0,
      note: "优先级决定胜出项",
      glyph: "triangle-alert",
      color: "--red",
    },
  ]);
}

/** Rebuilds the category select, keeping the leading "all" option. */
function syncCategoryOptions(categories) {
  const select = qs("#terminologyCategoryFilter");
  if (!select) {
    return;
  }
  const list = Array.isArray(categories) ? categories : [];
  const signature = list.join("|");
  if (signature === categorySignature) {
    return;
  }
  categorySignature = signature;
  while (select.options.length > 1) {
    select.remove(1);
  }
  for (const category of list) {
    select.appendChild(
      h("option", { value: category, text: terminologyCategoryLabel(category) })
    );
  }
  const wanted = filters().category || "";
  select.value = wanted;
  if (wanted && select.value !== wanted) {
    select.appendChild(h("option", { value: wanted, text: wanted }));
    select.value = wanted;
  }
}

/* ==========================================================================
   Table
   ========================================================================== */

/** ZH / EN / JA chips, greying out the languages that are still missing. */
function languageCell(term) {
  const rows = [
    { tag: "ZH", value: term.zh_cn },
    { tag: "EN", value: term.en },
    { tag: "JA", value: term.ja },
  ];
  return h(
    "div",
    { class: "terminology-languages" },
    rows.map((row) =>
      h(
        "span",
        { class: row.value ? null : "is-missing" },
        h("strong", { text: row.tag }),
        h("span", { text: row.value || "缺失" })
      )
    )
  );
}

function matchCell(term) {
  return badgeRow(
    badge("优先级 " + formatNumber(Number(term.priority) || 0), "info", "trending-up"),
    term.match_case ? badge("区分大小写", "warn", "type") : badge("忽略大小写", "good", "equal-not"),
    term.enabled ? badge("已启用", "good", "circle-check") : badge("已停用", "bad", "ban")
  );
}

function originCell(term) {
  const origin = String(term.origin || "bundled");
  const tone = origin === "custom" ? "info" : origin === "override" ? "warn" : "good";
  const glyph = origin === "custom" ? "sparkle" : origin === "override" ? "pencil-line" : "book-open";
  return badgeRow(
    badge(terminologyOriginLabel(origin), tone, glyph),
    term.conflict ? badge("有冲突", "bad", "triangle-alert") : null
  );
}

function terminologyRow(term) {
  const row = h(
    "tr",
    {
      class: "is-clickable",
      role: "button",
      tabindex: "0",
      "aria-label": "编辑术语 " + (term.canonical_label || term.term_id),
    },
    h("td", null, primaryCell(term.canonical_label || term.term_id, term.term_id)),
    h("td", null, languageCell(term)),
    h("td", null, h("span", { text: terminologyCategoryLabel(term.category) })),
    h("td", null, matchCell(term)),
    h("td", null, originCell(term))
  );

  const open = () => openTerminologyDrawer(term.term_id);
  row.addEventListener("click", open);
  row.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      open();
    }
  });
  return row;
}

function renderTerminology() {
  const tbody = qs("#terminologyRows");
  const empty = qs("#terminologyEmpty");
  const items = state.terminology.items;

  clear(tbody);
  if (!items.length) {
    setHidden(empty, false);
    renderEmptyState(empty, {
      glyph: "search-x",
      title: "没有匹配的术语",
      message: "调整分类、来源或搜索条件",
      tall: true,
    });
  } else {
    setHidden(empty, true);
    clear(empty);
    for (const term of items) {
      tbody.appendChild(terminologyRow(term));
    }
  }

  renderPagination(
    qs("#terminologyPagination"),
    {
      page: state.terminology.page,
      pages: state.terminology.pages,
      total: state.terminology.total,
    },
    (page) => {
      state.terminology.page = page;
      loadTerminology();
    },
    { unit: "条术语" }
  );
}

/** Fetches one page of terminology rows. */
export async function loadTerminology() {
  const tbody = qs("#terminologyRows");
  const empty = qs("#terminologyEmpty");
  const active = filters();
  const token = nextSequence("terminology");

  renderTerminologyMetrics();
  setHidden(empty, true);
  renderSkeletonRows(tbody, 6, 5);

  let payload = null;
  try {
    payload = await apiGet("admin/terminology", {
      query: active.query,
      category: active.category,
      origin: active.origin,
      status: active.status,
      sort: active.sort,
      page: state.terminology.page,
      page_size: PAGE_SIZE,
    });
  } catch (error) {
    if (!isCurrentSequence("terminology", token)) {
      return;
    }
    clear(tbody);
    setHidden(empty, false);
    renderEmptyState(empty, {
      glyph: "circle-alert",
      title: "无法读取名称库",
      message: error && error.message ? error.message : "请稍后重试。",
      tall: true,
    });
    toastError("读取名称库失败", error);
    throw error;
  }
  if (!isCurrentSequence("terminology", token)) {
    return;
  }

  const items = Array.isArray(payload && payload.items) ? payload.items : [];
  const total = Number(payload && payload.total) || 0;
  const pages = Math.max(1, Number(payload && payload.pages) || 1);

  if (total > 0 && !items.length && state.terminology.page > pages) {
    state.terminology.page = pages;
    return loadTerminology();
  }

  state.terminology.items = items;
  state.terminology.total = total;
  state.terminology.pages = pages;
  state.terminology.page = Math.min(pages, Math.max(1, Number(payload && payload.page) || 1));
  state.terminology.categories = Array.isArray(payload && payload.categories)
    ? payload.categories
    : [];
  state.terminology.revision = (payload && payload.revision) || null;
  syncCategoryOptions(state.terminology.categories);
  renderTerminology();
}

function reloadFromFirstPage() {
  state.terminology.page = 1;
  loadTerminology().catch((error) => console.error(error));
}

/* ==========================================================================
   Drawer
   ========================================================================== */

function textField(label, id, options) {
  const opts = options || {};
  const input = h("input", {
    type: opts.type || "text",
    class: "input",
    id: id,
    autocomplete: "off",
    maxlength: opts.maxlength ? String(opts.maxlength) : null,
    min: opts.min !== undefined ? String(opts.min) : null,
    max: opts.max !== undefined ? String(opts.max) : null,
    placeholder: opts.placeholder || null,
    readOnly: Boolean(opts.readOnly),
    value: opts.value === undefined || opts.value === null ? "" : String(opts.value),
  });
  return h(
    "label",
    { class: "field" },
    h("span", { text: label }),
    input,
    opts.hint ? h("span", { class: "field-hint", text: opts.hint }) : null
  );
}

function areaField(label, id, value, rows, maxlength) {
  return h(
    "label",
    { class: "field" },
    h("span", { text: label }),
    h("textarea", {
      class: "textarea",
      id: id,
      rows: String(rows || 4),
      maxlength: maxlength ? String(maxlength) : null,
      value: value === undefined || value === null ? "" : String(value),
    })
  );
}

function checkField(label, id, checked) {
  return h(
    "label",
    { class: "check-field" },
    h("input", { type: "checkbox", id: id, checked: Boolean(checked) }),
    h("span", { text: label })
  );
}

/** Joins a payload alias tuple back into one-per-line textarea content. */
function linesOf(value) {
  if (Array.isArray(value)) {
    return value.join("\n");
  }
  return value === undefined || value === null ? "" : String(value);
}

function conflictSection(term) {
  const conflicts = Array.isArray(term.conflicts) ? term.conflicts.slice(0, 40) : [];
  if (!conflicts.length) {
    return null;
  }
  return formSection(
    "匹配冲突",
    "同一别名被多条术语占用，优先级更高的一方胜出",
    h(
      "div",
      { class: "conflict-list" },
      conflicts.map((conflict) => {
        const entries = Array.isArray(conflict.entries) ? conflict.entries : [];
        const labels = entries
          .map((item) => (item && item.label) || "")
          .filter(Boolean)
          .slice(0, 6)
          .join(" · ");
        return h(
          "div",
          { class: "conflict-item" },
          h("strong", { text: conflict.alias || "未知别名" }),
          h("span", { text: labels || "另有条目使用该别名" })
        );
      })
    )
  );
}

/** Paints the terminology drawer for an existing or brand-new term. */
function renderTerminologyDrawer(term) {
  const body = qs("#terminologyDrawerBody");
  if (!body) {
    return;
  }
  const isNew = Boolean(state.terminologyDraftIsNew);
  const titleNode = qs("#terminologyDrawerTitle");
  if (titleNode) {
    setText(titleNode, isNew ? "新增术语" : term.canonical_label || term.term_id);
  }

  const statusSelect = h(
    "select",
    { class: "input", id: "termStatusInput" },
    ZH_STATUS_OPTIONS.map((value) =>
      h("option", { value: value, text: terminologyStatusLabel(value) })
    )
  );
  statusSelect.value = term.zh_status || "project";

  replaceChildren(
    body,
    formSection(
      "基本信息",
      isNew ? "留空会自动生成 custom: 前缀的 ID" : "术语 ID 不可修改",
      h(
        "div",
        { class: "form-grid two-columns" },
        textField("术语 ID", "termIdInput", {
          maxlength: 180,
          placeholder: "例如 character:driblee",
          readOnly: !isNew,
          value: term.term_id,
        }),
        textField("分类", "termCategoryInput", {
          maxlength: 80,
          placeholder: "character / work / ability",
          value: term.category,
        })
      ),
      isNew ? null : originCell(term)
    ),
    formSection(
      "规范名称",
      "输出格式为 中文（English）",
      h(
        "div",
        { class: "form-grid two-columns" },
        textField("简体中文", "termZhInput", { maxlength: 240, value: term.zh_cn }),
        textField("English", "termEnInput", { maxlength: 240, value: term.en }),
        textField("日本語", "termJaInput", { maxlength: 240, value: term.ja })
      ),
      h("span", { class: "field-hint", text: "三种语言至少填写一项。" })
    ),
    formSection(
      "别名与来源",
      "每行一个，也支持 | 分隔",
      h(
        "div",
        { class: "form-grid two-columns" },
        areaField("中文别名", "termAliasesZhInput", linesOf(term.aliases_zh), 4),
        areaField("英文别名", "termAliasesEnInput", linesOf(term.aliases_en), 4),
        areaField("日文别名", "termAliasesJaInput", linesOf(term.aliases_ja), 4),
        areaField("资料来源", "termSourcesInput", linesOf(term.sources), 4)
      )
    ),
    formSection(
      "匹配策略",
      "数值越高越优先",
      h(
        "div",
        { class: "form-grid two-columns" },
        h("label", { class: "field" }, h("span", { text: "中文名称状态" }), statusSelect),
        textField("优先级", "termPriorityInput", {
          type: "number",
          min: -1000,
          max: 1000,
          value: Number(term.priority) === 0 ? 0 : Number(term.priority) || 100,
        })
      ),
      h(
        "div",
        { class: "form-grid two-columns" },
        checkField("启用该条目匹配", "termEnabledInput", term.enabled !== false),
        checkField("英文/日文匹配区分大小写", "termMatchCaseInput", Boolean(term.match_case))
      )
    ),
    formSection(
      "维护备注",
      "不会发送给用户",
      areaField("备注", "termNotesInput", term.notes, 5, MAX_NOTES_CHARS)
    ),
    conflictSection(term)
  );

  const restoreButton = qs("#restoreTerminologyButton");
  if (restoreButton) {
    restoreButton.hidden = isNew || !term.has_override;
    const label = restoreButton.querySelector("span");
    setText(label, term.origin === "custom" ? "删除自定义" : "恢复内置");
  }
}

/** Loads a term detail payload and shows the drawer. */
export async function openTerminologyDrawer(termId) {
  state.terminologyDraftIsNew = false;
  const body = qs("#terminologyDrawerBody");
  renderSkeletonStack(body, 8);
  openDrawer("terminologyDrawer");
  try {
    const term = await apiGet("admin/terminology-entry", { term_id: termId });
    state.activeTerm = term;
    renderTerminologyDrawer(term);
  } catch (error) {
    toastError("读取术语失败", error);
    closeDrawer();
  }
}

/** Opens the drawer with a blank custom term. */
export function openTerminologyDraft() {
  const draft = {
    term_id: "",
    category: "special",
    zh_cn: "",
    en: "",
    ja: "",
    aliases_zh: [],
    aliases_en: [],
    aliases_ja: [],
    zh_status: "project",
    sources: [],
    notes: "",
    priority: 100,
    enabled: true,
    match_case: false,
    origin: "custom",
    has_override: false,
    conflicts: [],
  };
  state.activeTerm = draft;
  state.terminologyDraftIsNew = true;
  renderTerminologyDrawer(draft);
  openDrawer("terminologyDrawer");
  window.setTimeout(() => {
    const first = qs("#termZhInput");
    if (first) {
      first.focus();
    }
  }, 60);
}

/* ==========================================================================
   Writes
   ========================================================================== */

function collectTerminologyDraft() {
  const zh = ((qs("#termZhInput") || {}).value || "").trim();
  const en = ((qs("#termEnInput") || {}).value || "").trim();
  const ja = ((qs("#termJaInput") || {}).value || "").trim();
  if (!zh && !en && !ja) {
    return null;
  }
  return {
    term_id: ((qs("#termIdInput") || {}).value || "").trim(),
    category: ((qs("#termCategoryInput") || {}).value || "").trim() || "special",
    zh_cn: zh,
    en: en,
    ja: ja,
    aliases_zh: splitLines((qs("#termAliasesZhInput") || {}).value),
    aliases_en: splitLines((qs("#termAliasesEnInput") || {}).value),
    aliases_ja: splitLines((qs("#termAliasesJaInput") || {}).value),
    sources: splitLines((qs("#termSourcesInput") || {}).value),
    zh_status: ((qs("#termStatusInput") || {}).value || "project"),
    priority: Number((qs("#termPriorityInput") || {}).value) || 0,
    enabled: Boolean((qs("#termEnabledInput") || {}).checked),
    match_case: Boolean((qs("#termMatchCaseInput") || {}).checked),
    notes: (qs("#termNotesInput") || {}).value || "",
  };
}

async function saveTerminology() {
  if (!state.activeTerm) {
    return;
  }
  const payload = collectTerminologyDraft();
  if (!payload) {
    toast("请填写名称", "中文、英文和日文名称不能全部为空。", "warning");
    const first = qs("#termZhInput");
    if (first) {
      first.focus();
    }
    return;
  }

  const button = qs("#saveTerminologyButton");
  setButtonBusy(button, true, "保存中");
  let updated = null;
  try {
    updated = await apiPost("admin/terminology/save", payload);
  } catch (error) {
    toastError("保存术语失败", error);
    return;
  } finally {
    setButtonBusy(button, false);
  }

  toast("已保存术语", updated.canonical_label || updated.term_id, "success");
  state.activeTerm = updated;
  state.terminologyDraftIsNew = false;
  renderTerminologyDrawer(updated);
  invalidateView("audit");
  await Promise.all([
    loadTerminology().catch((error) => console.error(error)),
    refreshSummary().catch((error) => console.error(error)),
  ]);
}

async function restoreTerminology() {
  const term = state.activeTerm;
  if (!term || !term.term_id || !term.has_override) {
    return;
  }
  const isCustom = term.origin === "custom";
  const accepted = await confirmAction({
    title: isCustom ? "删除自定义术语" : "恢复内置版本",
    message: isCustom
      ? "自定义术语 " + (term.canonical_label || term.term_id) + " 将被永久删除。"
      : "将丢弃对 " + (term.canonical_label || term.term_id) + " 的所有修改，改用插件内置版本。",
    acceptLabel: isCustom ? "删除" : "恢复",
    tone: isCustom ? "danger" : "warning",
    glyph: isCustom ? "trash-2" : "rotate-ccw",
  });
  if (!accepted) {
    return;
  }

  const button = qs("#restoreTerminologyButton");
  setButtonBusy(button, true, "处理中");
  let result = null;
  try {
    result = await apiPost("admin/terminology/restore", { term_id: term.term_id });
  } catch (error) {
    toastError(isCustom ? "删除失败" : "恢复失败", error);
    return;
  } finally {
    setButtonBusy(button, false);
  }

  if (result && result.deleted) {
    toast("已删除自定义术语", term.term_id, "success");
    state.activeTerm = null;
    closeDrawer();
  } else {
    toast("已恢复内置版本", result.canonical_label || term.term_id, "success");
    state.activeTerm = result;
    renderTerminologyDrawer(result);
  }
  invalidateView("audit");
  await Promise.all([
    loadTerminology().catch((error) => console.error(error)),
    refreshSummary().catch((error) => console.error(error)),
  ]);
}

/**
 * Exports the merged library. The payload is raw bytes, so it goes through the
 * host bridge download() helper instead of the JSON api helpers.
 */
async function exportTerminology(format, button) {
  setButtonBusy(button, true, "导出中");
  try {
    await apiDownload(
      "admin/terminology/download",
      { format: format, scope: "merged" },
      "kirby-terminology." + format
    );
    toast("已开始下载", format === "csv" ? "CSV 文件" : "JSON 文件", "success");
  } catch (error) {
    toastError("导出失败", error);
  } finally {
    setButtonBusy(button, false);
  }
}

async function importTerminology(file) {
  const button = qs("#terminologyImportButton");
  setButtonBusy(button, true, "导入中");
  let result = null;
  try {
    result = await apiUpload("admin/terminology/import", file);
  } catch (error) {
    toastError("导入失败", error);
    return;
  } finally {
    setButtonBusy(button, false);
  }

  const imported = Number(result && result.imported) || 0;
  const overrides = Number(result && result.overrides) || 0;
  toast(
    "名称库已导入",
    "写入 " + formatNumber(imported) + " 条，覆盖层共 " + formatNumber(overrides) + " 条",
    "success"
  );
  invalidateView("audit");
  categorySignature = null;
  state.terminology.page = 1;
  await Promise.all([
    loadTerminology().catch((error) => console.error(error)),
    refreshSummary().catch((error) => console.error(error)),
  ]);
}

/* ==========================================================================
   Wiring
   ========================================================================== */

/** Binds the terminology toolbar and drawer footer exactly once. */
export function initTerminology() {
  onSummary(() => renderTerminologyMetrics());

  const search = qs("#terminologySearch");
  if (search) {
    const run = debounce(() => reloadFromFirstPage(), 280);
    search.addEventListener("input", () => {
      filters().query = search.value.trim();
      run();
    });
  }

  const selectBindings = [
    ["#terminologyCategoryFilter", "category"],
    ["#terminologyOriginFilter", "origin"],
    ["#terminologyStatusFilter", "status"],
    ["#terminologySort", "sort"],
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

  const addButton = qs("#terminologyAddButton");
  if (addButton) {
    addButton.addEventListener("click", () => openTerminologyDraft());
  }
  const saveButton = qs("#saveTerminologyButton");
  if (saveButton) {
    saveButton.addEventListener("click", () => saveTerminology());
  }
  const restoreButton = qs("#restoreTerminologyButton");
  if (restoreButton) {
    restoreButton.addEventListener("click", () => restoreTerminology());
  }
  onDrawerClose("terminologyDrawer", () => {
    state.activeTerm = null;
    state.terminologyDraftIsNew = false;
  });

  const jsonButton = qs("#terminologyExportJsonButton");
  if (jsonButton) {
    jsonButton.addEventListener("click", () => exportTerminology("json", jsonButton));
  }
  const csvButton = qs("#terminologyExportCsvButton");
  if (csvButton) {
    csvButton.addEventListener("click", () => exportTerminology("csv", csvButton));
  }

  const importInput = qs("#terminologyImportInput");
  const importButton = qs("#terminologyImportButton");
  if (importButton && importInput) {
    importButton.addEventListener("click", () => importInput.click());
    importInput.addEventListener("change", () => {
      const file = importInput.files && importInput.files[0];
      importInput.value = "";
      if (file) {
        importTerminology(file);
      }
    });
  }
}
