/**
 * Catalog view: paginated entry table, the entry drawer and the add-entry
 * modal.
 *
 * Filter values are read from state.entries.filters rather than the DOM, the
 * select options are only rebuilt when the summary actually changes, and every
 * write path re-validates the current page so deleting the last row of the last
 * page cannot leave an empty table behind.
 */

import { h, qs, clear, setHidden, replaceChildren } from "../core/dom.js";
import { apiGet, apiPost, apiUpload } from "../core/bridge.js";
import { state, nextSequence, isCurrentSequence, invalidateView } from "../core/state.js";
import { debounce, formatNumber, stringifyValue } from "../core/format.js";
import { kindLabel } from "../core/labels.js";
import { onSummary, refreshSummary } from "../core/summary.js";
import { icon } from "../core/icons.js";
import {
  badge,
  badgeRow,
  externalLink,
  formSection,
  metadataList,
  primaryCell,
  renderEmptyState,
  renderSkeletonRows,
  renderSkeletonStack,
  secondaryCell,
  setButtonBusy,
  thumbFrame,
} from "../ui/widgets.js";
import { renderPagination } from "../ui/pagination.js";
import { openDrawer, closeDrawer, onDrawerClose } from "../ui/drawer.js";
import { confirmAction } from "../ui/confirm.js";
import { toast, toastError } from "../ui/toast.js";
import { bindUploadZone, pickImageFile, validateImageFile } from "../ui/upload.js";

const PAGE_SIZE = 30;
const MAX_DESCRIPTION_CHARS = 30000;

/** Signatures of the option sets currently rendered into the two selects. */
let sourceSignature = null;
let kindSignature = null;

/** True once the operator pressed "restore bundled description" in the drawer. */
let descriptionRestoreRequested = false;

function filters() {
  return state.entries.filters;
}

/* ==========================================================================
   Filter options
   ========================================================================== */

/**
 * Rebuilds a select while keeping its first ("all") option and the current
 * value, even when that value is no longer part of the payload.
 */
function syncOptions(select, options, current) {
  if (!select) {
    return;
  }
  while (select.options.length > 1) {
    select.remove(1);
  }
  for (const option of options) {
    select.appendChild(h("option", { value: option.value, text: option.label }));
  }
  const wanted = current || "";
  select.value = wanted;
  if (wanted && select.value !== wanted) {
    select.appendChild(h("option", { value: wanted, text: wanted }));
    select.value = wanted;
  }
}

/** Mirrors summary.catalog.sources / kinds into the toolbar selects. */
function syncFilterOptions(summary) {
  const catalog = (summary && summary.catalog) || {};
  const sources = Array.isArray(catalog.sources) ? catalog.sources : [];
  const kinds = Array.isArray(catalog.kinds) ? catalog.kinds : [];

  const nextSourceSignature = sources.map((item) => item.name + ":" + item.count).join("|");
  if (nextSourceSignature !== sourceSignature) {
    sourceSignature = nextSourceSignature;
    syncOptions(
      qs("#entrySourceFilter"),
      sources.map((item) => ({
        value: item.name,
        label: (item.name || "未标注") + " (" + formatNumber(item.count) + ")",
      })),
      filters().source
    );
  }

  const nextKindSignature = kinds.map((item) => item.name + ":" + item.count).join("|");
  if (nextKindSignature !== kindSignature) {
    kindSignature = nextKindSignature;
    syncOptions(
      qs("#entryKindFilter"),
      kinds.map((item) => ({
        value: item.name,
        label: kindLabel(item.name) + " (" + formatNumber(item.count) + ")",
      })),
      filters().kind
    );
  }
}

/* ==========================================================================
   Table
   ========================================================================== */

function statusBadges(entry) {
  const badges = [];
  if (!entry.has_asset) {
    badges.push(badge("缺少图片", "bad", "image-off"));
  }
  if (entry.description_missing) {
    badges.push(badge("缺少简介", "warn", "align-left"));
  } else if (entry.description_origin === "override") {
    badges.push(badge("已修改简介", "info", "pencil-line"));
  }
  if (entry.catalog_kind === "manual") {
    badges.push(badge("手动新增", "info", "plus"));
  }
  if (!badges.length) {
    badges.push(badge("资料完整", "good", "circle-check"));
  }
  return badgeRow.apply(null, badges);
}

function entryRow(entry) {
  const row = h(
    "tr",
    {
      class: "is-clickable",
      role: "button",
      tabindex: "0",
      "aria-label": "编辑 #" + entry.id + " " + (entry.name || "未命名"),
    },
    h("td", { class: "media-column" }, thumbFrame(entry)),
    h(
      "td",
      null,
      h("span", { class: "catalog-id", text: "#" + entry.id }),
      primaryCell(entry.name || "未命名", entry.name_en || entry.page_title || "")
    ),
    h(
      "td",
      null,
      secondaryCell(
        entry.display_work || entry.source || "--",
        entry.debut_year ? String(entry.debut_year) + " 年" : ""
      )
    ),
    h(
      "td",
      null,
      entry.description_excerpt
        ? h("p", { class: "cell-excerpt", text: entry.description_excerpt })
        : h("span", { class: "cell-muted", text: "尚无简介" })
    ),
    h(
      "td",
      null,
      h("span", { text: kindLabel(entry.catalog_kind) }),
      entry.variant_key ? h("span", { class: "cell-muted", text: entry.variant_key }) : null
    ),
    h("td", null, statusBadges(entry))
  );

  const open = () => openEntryDrawer(entry.id);
  row.addEventListener("click", open);
  row.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      open();
    }
  });
  return row;
}

function renderEntries() {
  const tbody = qs("#entryRows");
  const empty = qs("#entryEmpty");
  const items = state.entries.items;

  clear(tbody);
  if (!items.length) {
    setHidden(empty, false);
    renderEmptyState(empty, {
      glyph: "search-x",
      title: "没有匹配的素材",
      message: "调整搜索或筛选条件",
      tall: true,
    });
  } else {
    setHidden(empty, true);
    clear(empty);
    for (const entry of items) {
      tbody.appendChild(entryRow(entry));
    }
  }

  renderPagination(
    qs("#entryPagination"),
    { page: state.entries.page, pages: state.entries.pages, total: state.entries.total },
    (page) => {
      state.entries.page = page;
      loadCatalog();
    },
    { unit: "个素材" }
  );
}

/** Fetches one page of catalog entries. */
export async function loadCatalog() {
  const tbody = qs("#entryRows");
  const empty = qs("#entryEmpty");
  const active = filters();
  const token = nextSequence("entries");

  setHidden(empty, true);
  renderSkeletonRows(tbody, 6, 6);

  let payload = null;
  try {
    payload = await apiGet("admin/entries", {
      query: active.query,
      source: active.source,
      kind: active.kind,
      status: active.status,
      sort: active.sort,
      page: state.entries.page,
      page_size: PAGE_SIZE,
    });
  } catch (error) {
    if (!isCurrentSequence("entries", token)) {
      return;
    }
    clear(tbody);
    setHidden(empty, false);
    renderEmptyState(empty, {
      glyph: "circle-alert",
      title: "无法读取素材库",
      message: error && error.message ? error.message : "请稍后重试。",
      tall: true,
    });
    toastError("读取素材库失败", error);
    throw error;
  }
  if (!isCurrentSequence("entries", token)) {
    return;
  }

  const items = Array.isArray(payload && payload.items) ? payload.items : [];
  const total = Number(payload && payload.total) || 0;
  const pages = Math.max(1, Number(payload && payload.pages) || 1);

  // The page we asked for no longer exists (last row of the last page was
  // deleted). Clamp against the freshly returned page count and retry once.
  if (total > 0 && !items.length && state.entries.page > pages) {
    state.entries.page = pages;
    return loadCatalog();
  }

  state.entries.items = items;
  state.entries.total = total;
  state.entries.pages = pages;
  state.entries.page = Math.min(pages, Math.max(1, Number(payload && payload.page) || 1));
  renderEntries();
}

/** Resets to page 1 and refetches; used by every filter control. */
function reloadFromFirstPage() {
  state.entries.page = 1;
  loadCatalog().catch((error) => console.error(error));
}

/* ==========================================================================
   Entry drawer
   ========================================================================== */

/** Snapshot of the editable drawer fields, so a re-render can restore them. */
function captureEntryDraft() {
  const nameInput = qs("#entryNameInput");
  if (!nameInput) {
    return null;
  }
  return {
    name: nameInput.value,
    source: (qs("#entrySourceInput") || {}).value || "",
    description: (qs("#entryDescriptionInput") || {}).value || "",
  };
}

function descriptionBadge(entry) {
  if (entry.description_origin === "override") {
    return badge("管理员版本", "info", "pencil-line");
  }
  if (entry.description_missing) {
    return badge("尚未填写", "warn", "circle-alert");
  }
  return badge("内置资料", "good", "book-open");
}

function entryHero(entry) {
  const source = typeof entry.thumbnail === "string" && entry.thumbnail.startsWith("data:image/")
    ? entry.thumbnail
    : "";
  const visual = h(
    "div",
    { class: "entry-image-large" },
    source ? h("img", { src: source, alt: entry.name || "" }) : icon("image-off")
  );

  const replaceButton = h(
    "button",
    { type: "button", class: "button ghost", id: "replaceEntryImageButton" },
    icon("image-up"),
    h("span", { text: "替换图片" })
  );
  replaceButton.addEventListener("click", () => replaceEntryImage());

  const aliases = Array.isArray(entry.aliases) ? entry.aliases.slice(0, 24) : [];

  return h(
    "div",
    { class: "entry-visual" },
    visual,
    h(
      "div",
      { class: "entry-identity" },
      h("h3", { text: entry.name || "未命名" }),
      h("span", {
        class: "entry-subtitle",
        text: "#" + entry.id + (entry.source ? " · " + entry.source : ""),
      }),
      h("div", { class: "entry-actions" }, replaceButton, externalLink(entry.source_url)),
      aliases.length
        ? h(
            "div",
            { class: "entry-aliases" },
            aliases.map((alias) => h("span", { class: "alias-chip", text: stringifyValue(alias) }))
          )
        : null
    )
  );
}

function entryMetadata(entry) {
  const metadata = (entry && entry.metadata) || {};
  const rows = [
    { label: "文件名", value: entry.filename },
    { label: "固定键", value: entry.entry_key },
    { label: "英文页名", value: entry.page_title || entry.name_en },
    { label: "变体键", value: entry.variant_key },
    { label: "图鉴类型", value: kindLabel(entry.catalog_kind) },
  ];
  for (const key of Object.keys(metadata)) {
    rows.push({ label: key, value: stringifyValue(metadata[key]) });
  }
  return metadataList(rows);
}

/**
 * Paints the entry drawer.
 *
 * @param {Object} entry Entry detail payload.
 * @param {{name: string, source: string, description: string}|null} [draft]
 *        Unsaved field values to restore (used after an image replacement).
 */
function renderEntryDrawer(entry, draft) {
  const body = qs("#entryDrawerBody");
  if (!body) {
    return;
  }

  const titleNode = qs("#entryDrawerTitle");
  if (titleNode) {
    titleNode.textContent = "#" + entry.id + " " + (entry.name || "未命名");
  }

  const nameInput = h("input", {
    type: "text",
    class: "input",
    id: "entryNameInput",
    maxlength: "160",
    required: true,
    autocomplete: "off",
    value: draft ? draft.name : entry.name || "",
  });
  const sourceInput = h("input", {
    type: "text",
    class: "input",
    id: "entrySourceInput",
    maxlength: "240",
    autocomplete: "off",
    value: draft ? draft.source : entry.source || "",
  });
  const descriptionInput = h("textarea", {
    class: "textarea",
    id: "entryDescriptionInput",
    rows: "12",
    maxlength: String(MAX_DESCRIPTION_CHARS),
    value: draft ? draft.description : entry.description || "",
  });

  let restoreButton = null;
  if (entry.description_origin === "override") {
    restoreButton = h(
      "button",
      { type: "button", class: "button ghost", id: "restoreEntryDescriptionButton" },
      icon("rotate-ccw"),
      h("span", { text: "恢复内置简介" })
    );
    restoreButton.addEventListener("click", () => {
      descriptionRestoreRequested = true;
      descriptionInput.value = "";
      descriptionInput.disabled = true;
      restoreButton.disabled = true;
      toast("已标记恢复", "保存后会重新使用内置简介。", "info");
    });
  }

  replaceChildren(
    body,
    entryHero(entry),
    entryMetadata(entry),
    formSection(
      "基础资料",
      "名称会直接出现在抽取消息里",
      h(
        "div",
        { class: "form-grid two-columns" },
        h("label", { class: "field" }, h("span", { text: "盟友名称" }), nameInput),
        h("label", { class: "field" }, h("span", { text: "首次登场作品" }), sourceInput)
      )
    ),
    formSection(
      "角色简介",
      "最多 30,000 字",
      badgeRow(descriptionBadge(entry)),
      h("label", { class: "field" }, descriptionInput),
      h("span", {
        class: "field-hint",
        text: "最多 30,000 字；抽取消息中的显示长度仍由插件配置控制。",
      }),
      restoreButton
    )
  );
}

/** Loads an entry detail payload and shows the drawer. */
export async function openEntryDrawer(entryId) {
  descriptionRestoreRequested = false;
  const body = qs("#entryDrawerBody");
  renderSkeletonStack(body, 7);
  openDrawer("entryDrawer");
  try {
    const entry = await apiGet("admin/entries/" + encodeURIComponent(entryId));
    state.activeEntry = entry;
    renderEntryDrawer(entry);
  } catch (error) {
    toastError("读取素材详情失败", error);
    closeDrawer();
  }
}

/** Replaces the asset image without discarding unsaved text edits. */
async function replaceEntryImage() {
  const entry = state.activeEntry;
  if (!entry) {
    return;
  }
  const file = await pickImageFile();
  if (!file) {
    return;
  }
  const check = validateImageFile(file);
  if (!check.ok) {
    toast("图片不可用", check.message, "warning");
    return;
  }

  // Snapshot first: the drawer is fully re-rendered from the response.
  const draft = captureEntryDraft();
  const button = qs("#replaceEntryImageButton");
  setButtonBusy(button, true, "上传中");
  try {
    const updated = await apiUpload("admin/entries/" + encodeURIComponent(entry.id) + "/image", file);
    state.activeEntry = updated;
    renderEntryDrawer(updated, draft);
    toast("已替换素材图片", "#" + updated.id + " " + (updated.name || ""), "success");
  } catch (error) {
    setButtonBusy(button, false);
    toastError("替换图片失败", error);
    return;
  }
  invalidateView("audit");
  await Promise.all([
    loadCatalog().catch((error) => console.error(error)),
    refreshSummary().catch((error) => console.error(error)),
  ]);
}

async function saveEntry() {
  const entry = state.activeEntry;
  if (!entry) {
    return;
  }
  const nameInput = qs("#entryNameInput");
  const name = (nameInput && nameInput.value.trim()) || "";
  if (!name) {
    toast("请填写盟友名称", "名称不能为空。", "warning");
    if (nameInput) {
      nameInput.focus();
    }
    return;
  }
  const source = ((qs("#entrySourceInput") || {}).value || "").trim();
  const description = (qs("#entryDescriptionInput") || {}).value || "";

  let action = "keep";
  if (descriptionRestoreRequested) {
    action = "restore";
  } else if (description !== (entry.description || "")) {
    action = "set";
  }

  const button = qs("#saveEntryButton");
  setButtonBusy(button, true, "保存中");
  let updated = null;
  try {
    updated = await apiPost("admin/entries/save", {
      id: entry.id,
      name,
      source,
      description_action: action,
      description,
    });
  } catch (error) {
    toastError("保存失败", error);
    return;
  } finally {
    setButtonBusy(button, false);
  }

  // Reported only after the write itself succeeded: the follow-up refreshes
  // below must never turn a successful save into a failure toast.
  toast("已保存素材资料", "#" + updated.id + " " + (updated.name || ""), "success");
  state.activeEntry = updated;
  descriptionRestoreRequested = false;
  renderEntryDrawer(updated);
  invalidateView("audit");
  await Promise.all([
    loadCatalog().catch((error) => console.error(error)),
    refreshSummary().catch((error) => console.error(error)),
  ]);
}

async function deleteEntry() {
  const entry = state.activeEntry;
  if (!entry) {
    return;
  }
  const accepted = await confirmAction({
    title: "移入回收站？",
    message:
      "#" +
      entry.id +
      " " +
      (entry.name || "未命名") +
      " 会从抽取池移除，编号与用户解锁记录仍会保留，可随时从回收站恢复。",
    acceptLabel: "移入回收站",
    glyph: "trash-2",
  });
  if (!accepted) {
    return;
  }

  const button = qs("#deleteEntryButton");
  setButtonBusy(button, true, "处理中");
  let result = null;
  try {
    result = await apiPost("admin/entries/delete", { id: entry.id });
  } catch (error) {
    toastError("删除失败", error);
    return;
  } finally {
    setButtonBusy(button, false);
  }

  const affected = Number(result && result.affected_users) || 0;
  toast(
    "已移入回收站",
    affected ? "影响 " + formatNumber(affected) + " 位用户的解锁记录" : "没有用户解锁过该素材",
    "success"
  );
  state.activeEntry = null;
  closeDrawer();
  invalidateView("trash", "audit");
  await Promise.all([
    loadCatalog().catch((error) => console.error(error)),
    refreshSummary().catch((error) => console.error(error)),
  ]);
}

/* ==========================================================================
   Add-entry modal
   ========================================================================== */

function resetAddPreview() {
  const preview = qs("#addImagePreview");
  const placeholder = qs("#addUploadPlaceholder");
  if (preview) {
    preview.removeAttribute("src");
    preview.hidden = true;
  }
  setHidden(placeholder, false);
}

/** Opens the modal with a clean form. Exported for the topbar quick action. */
export function openAddEntryDialog() {
  const dialog = qs("#addEntryDialog");
  if (!dialog) {
    return;
  }
  state.pendingUpload = null;
  for (const id of ["#addName", "#addSource", "#addDescription"]) {
    const node = qs(id);
    if (node) {
      node.value = "";
    }
  }
  resetAddPreview();
  if (!dialog.open) {
    dialog.showModal();
  }
  window.setTimeout(() => {
    const first = qs("#addName");
    if (first) {
      first.focus();
    }
  }, 60);
}

function closeAddEntryDialog() {
  const dialog = qs("#addEntryDialog");
  if (dialog && dialog.open) {
    dialog.close();
  }
  state.pendingUpload = null;
}

/** Stages the picked file so add_entry only has to reference the token. */
async function stageUpload(file) {
  const zone = qs("#addUploadZone");
  if (zone) {
    zone.disabled = true;
  }
  try {
    const result = await apiUpload("admin/uploads/image", file);
    state.pendingUpload = result;
    const preview = qs("#addImagePreview");
    if (preview && result && typeof result.preview === "string") {
      preview.src = result.preview;
      preview.hidden = false;
      setHidden(qs("#addUploadPlaceholder"), true);
    }
    const size = (result && result.size) || {};
    toast(
      "图片已就绪",
      (size.width || 0) + " × " + (size.height || 0) + " 像素",
      "success"
    );
  } catch (error) {
    state.pendingUpload = null;
    resetAddPreview();
    toastError("图片上传失败", error);
  } finally {
    if (zone) {
      zone.disabled = false;
    }
  }
}

async function confirmAddEntry() {
  const nameInput = qs("#addName");
  const name = (nameInput && nameInput.value.trim()) || "";
  if (!name) {
    toast("请填写盟友名称", "名称不能为空。", "warning");
    if (nameInput) {
      nameInput.focus();
    }
    return;
  }
  const upload = state.pendingUpload;
  if (!upload || !upload.token) {
    toast("请先选择素材图片", "新增素材必须附带一张立绘。", "warning");
    return;
  }

  const button = qs("#confirmAddEntryButton");
  setButtonBusy(button, true, "添加中");
  let entry = null;
  try {
    entry = await apiPost("admin/entries/add", {
      name,
      source: ((qs("#addSource") || {}).value || "").trim(),
      upload_token: upload.token,
      description: (qs("#addDescription") || {}).value || "",
    });
  } catch (error) {
    toastError("新增失败", error);
    return;
  } finally {
    setButtonBusy(button, false);
  }

  toast("已加入图鉴", "#" + entry.id + " " + (entry.name || ""), "success");
  closeAddEntryDialog();
  invalidateView("audit");
  state.entries.page = 1;
  await Promise.all([
    loadCatalog().catch((error) => console.error(error)),
    refreshSummary().catch((error) => console.error(error)),
  ]);
  openEntryDrawer(entry.id);
}

/* ==========================================================================
   Wiring
   ========================================================================== */

/** Binds the toolbar, drawer footer and add-entry modal exactly once. */
export function initCatalog() {
  onSummary(syncFilterOptions);

  const search = qs("#entrySearch");
  if (search) {
    const run = debounce(() => reloadFromFirstPage(), 280);
    search.addEventListener("input", () => {
      filters().query = search.value.trim();
      run();
    });
  }

  const selectBindings = [
    ["#entrySourceFilter", "source"],
    ["#entryKindFilter", "kind"],
    ["#entryStatusFilter", "status"],
    ["#entrySort", "sort"],
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

  const saveButton = qs("#saveEntryButton");
  if (saveButton) {
    saveButton.addEventListener("click", () => saveEntry());
  }
  const deleteButton = qs("#deleteEntryButton");
  if (deleteButton) {
    deleteButton.addEventListener("click", () => deleteEntry());
  }
  onDrawerClose("entryDrawer", () => {
    state.activeEntry = null;
    descriptionRestoreRequested = false;
  });

  const addButton = qs("#addEntryButton");
  if (addButton) {
    addButton.addEventListener("click", () => openAddEntryDialog());
  }
  const closeButton = qs("#closeAddEntryButton");
  if (closeButton) {
    closeButton.addEventListener("click", () => closeAddEntryDialog());
  }
  const confirmButton = qs("#confirmAddEntryButton");
  if (confirmButton) {
    confirmButton.addEventListener("click", () => confirmAddEntry());
  }
  const form = qs("#addEntryForm");
  if (form) {
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      confirmAddEntry();
    });
  }
  const dialog = qs("#addEntryDialog");
  if (dialog) {
    dialog.addEventListener("close", () => {
      state.pendingUpload = null;
    });
  }

  const zone = qs("#addUploadZone");
  const input = qs("#addImageInput");
  if (zone && input) {
    bindUploadZone({
      zone,
      input,
      onFile: (file) => stageUpload(file),
      onReject: (message) => toast("图片不可用", message, "warning"),
    });
  }
}
