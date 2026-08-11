const byId = (id) => document.getElementById(id);
const all = (selector, root = document) => Array.from(root.querySelectorAll(selector));

const state = {
  bridge: null,
  context: null,
  summary: null,
  view: "overview",
  loaded: new Set(),
  theme: "auto",
  hostDark: false,
  entries: { items: [], page: 1, pages: 1, total: 0, page_size: 30 },
  entryFilters: {
    query: "",
    source: "",
    kind: "",
    status: "all",
    sort: "id_asc",
  },
  activeEntry: null,
  entryDescriptionDirty: false,
  terminology: { items: [], page: 1, pages: 1, total: 0, page_size: 30, categories: [] },
  terminologyFilters: {
    query: "",
    category: "",
    origin: "",
    status: "all",
    sort: "category",
  },
  activeTerminology: null,
  groups: { items: [], page: 1, pages: 1, total: 0, page_size: 30 },
  groupQuery: "",
  selectedGroup: "",
  users: { items: [], page: 1, pages: 1, total: 0, page_size: 30 },
  userQuery: "",
  activeUser: null,
  userCurrentSelection: "",
  unlockSelection: "",
  trash: [],
  audit: [],
  addUpload: null,
  requestSequence: { entries: 0, groups: 0, users: 0, terminology: 0, current: 0, unlock: 0 },
};

const numberFormat = new Intl.NumberFormat("zh-CN");
const percentFormat = new Intl.NumberFormat("zh-CN", {
  minimumFractionDigits: 0,
  maximumFractionDigits: 2,
});

const actionLabels = {
  "entry.update": "更新素材资料",
  "entry.add": "新增素材",
  "entry.image.replace": "替换素材图片",
  "entry.delete": "删除素材",
  "entry.restore": "恢复素材",
  "group.user.update": "更新成员数据",
  "group.user.delete": "删除成员数据",
  "group.unlock.add": "增加解锁",
  "group.unlock.remove": "移除解锁",
  "group.draws.reset": "重置群抽取次数",
  "terminology.update": "更新名称库术语",
  "terminology.restore": "恢复名称库内置版本",
  "terminology.import": "导入名称库",
};

const kindLabels = {
  base: "基础角色",
  ability: "能力形态",
  evolution: "进化形态",
  special_form: "特殊形态",
  transformed: "变身形态",
  variant: "角色变体",
  phase: "阶段形态",
  manual: "手动新增",
  legacy: "历史条目",
};

const terminologyCategoryLabels = {
  character: "角色",
  form: "形态",
  ability: "能力",
  work: "作品",
  location: "地点",
  mechanic: "机制",
  mode: "模式",
  title: "称号",
  special: "专有名词",
};

const terminologyOriginLabels = {
  bundled: "内置",
  override: "已覆盖",
  custom: "自定义",
};

const terminologyStatusLabels = {
  official: "官方译名",
  official_reused: "沿用官译",
  project: "项目自译",
  transliterated: "音译",
  unchanged: "原文保留",
  unknown: "未标注",
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function safeUrl(value) {
  const text = String(value || "").trim();
  return /^https?:\/\//i.test(text) ? text : "";
}

function icon(name) {
  return `<i data-lucide="${escapeHtml(name)}"></i>`;
}

function refreshIcons() {
  window.lucide?.createIcons();
}

function formatNumber(value) {
  return numberFormat.format(Number(value || 0));
}

function formatPercent(value) {
  return `${percentFormat.format(Number(value || 0))}%`;
}

function formatDateTime(value) {
  const text = String(value || "").trim();
  if (!text) return "--";
  return text.replace("T", " ").replace(/\+08:00$/, "");
}

function debounce(callback, delay = 280) {
  let timer = 0;
  return (...args) => {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => callback(...args), delay);
  };
}

async function waitForBridge(timeout = 6000) {
  const started = Date.now();
  while (!window.AstrBotPluginPage) {
    if (Date.now() - started > timeout) {
      throw new Error("AstrBot Page Bridge 未加载，请从 AstrBot Dashboard 打开本页面");
    }
    await new Promise((resolve) => window.setTimeout(resolve, 30));
  }
  return window.AstrBotPluginPage;
}

function unwrapResponse(value) {
  if (value && typeof value === "object") {
    if (value.status === "error" || value.ok === false) {
      throw new Error(value.message || value.error || "操作失败");
    }
    if (value.status === "success" && "data" in value) {
      return value.data;
    }
  }
  return value;
}

async function apiGet(endpoint, params = {}) {
  return unwrapResponse(await state.bridge.apiGet(endpoint, params));
}

async function apiPost(endpoint, body = {}) {
  return unwrapResponse(await state.bridge.apiPost(endpoint, body));
}

async function apiUpload(endpoint, file) {
  return unwrapResponse(await state.bridge.upload(endpoint, file));
}

function detectHostDark(context) {
  const values = [
    context?.theme,
    context?.colorScheme,
    context?.appearance,
    context?.themeMode,
    context?.darkMode,
  ];
  const serialized = values
    .map((value) => (typeof value === "object" ? JSON.stringify(value) : String(value ?? "")))
    .join(" ")
    .toLowerCase();
  if (serialized.includes("dark") || serialized.includes("true")) return true;
  if (serialized.includes("light") || serialized.includes("false")) return false;
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches || false;
}

function applyTheme(theme = state.theme) {
  state.theme = ["auto", "kirby", "dark"].includes(theme) ? theme : "auto";
  document.documentElement.dataset.theme = state.theme;
  const effective = state.theme === "auto" ? (state.hostDark ? "dark" : "light") : state.theme;
  document.documentElement.dataset.effectiveTheme = effective;
  all("[data-theme-value]").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.themeValue === state.theme);
    button.setAttribute("aria-pressed", String(button.dataset.themeValue === state.theme));
  });
}

function applyContext(context) {
  state.context = context || state.context;
  state.hostDark = detectHostDark(state.context);
  if (state.theme === "auto") applyTheme("auto");
}

function toast(title, message = "", type = "success") {
  const region = byId("toastRegion");
  const element = document.createElement("div");
  element.className = `toast is-${type}`;
  element.innerHTML = `
    ${icon(type === "error" ? "circle-alert" : type === "success" ? "circle-check" : "info")}
    <div><strong>${escapeHtml(title)}</strong>${message ? `<p>${escapeHtml(message)}</p>` : ""}</div>
    <button type="button" aria-label="关闭通知">${icon("x")}</button>
  `;
  element.querySelector("button").addEventListener("click", () => element.remove());
  region.append(element);
  refreshIcons();
  window.setTimeout(() => element.remove(), type === "error" ? 7000 : 4200);
}

function setButtonBusy(button, busy, busyText = "处理中") {
  if (!button) return;
  if (busy) {
    button.dataset.originalHtml = button.innerHTML;
    button.disabled = true;
    button.innerHTML = `${icon("loader-circle")}<span>${escapeHtml(busyText)}</span>`;
    button.querySelector("svg")?.classList.add("spin");
  } else {
    button.disabled = false;
    if (button.dataset.originalHtml) button.innerHTML = button.dataset.originalHtml;
    delete button.dataset.originalHtml;
  }
  refreshIcons();
}

let confirmResolver = null;

function confirmAction({ title, message, accept = "确认", tone = "danger" }) {
  const dialog = byId("confirmDialog");
  byId("confirmTitle").textContent = title;
  byId("confirmMessage").textContent = message;
  const acceptButton = byId("confirmAcceptButton");
  acceptButton.textContent = accept;
  acceptButton.className = tone === "warning" ? "button warning" : "button danger";
  byId("confirmIcon").classList.toggle("is-warning", tone === "warning");
  if (confirmResolver) confirmResolver(false);
  dialog.showModal();
  return new Promise((resolve) => {
    confirmResolver = resolve;
  });
}

function settleConfirm(result) {
  if (!confirmResolver) return;
  const resolve = confirmResolver;
  confirmResolver = null;
  byId("confirmDialog").close();
  resolve(result);
}

function actionIcon(action) {
  if (action.includes("delete")) return "trash-2";
  if (action.includes("restore")) return "archive-restore";
  if (action.includes("image")) return "image";
  if (action.includes("unlock")) return "badge-check";
  if (action.includes("group")) return "users-round";
  if (action.includes("add")) return "plus";
  return "pencil";
}

function renderAuditItems(items, compact = false) {
  if (!items?.length) return "";
  return items
    .map(
      (item) => `
        <article class="audit-item">
          <span class="audit-icon">${icon(actionIcon(String(item.action || "")))}</span>
          <div class="audit-copy">
            <div class="audit-title">
              <strong>${escapeHtml(actionLabels[item.action] || item.action || "管理操作")}</strong>
              <span>${escapeHtml(item.target || "")}</span>
            </div>
            <p>${escapeHtml(item.summary || "")}</p>
          </div>
          <div class="audit-meta">
            <span>${escapeHtml(item.username || "dashboard")}</span>
            <time>${escapeHtml(formatDateTime(item.timestamp))}</time>
          </div>
        </article>
      `,
    )
    .join("");
}

function renderOverview() {
  const summary = state.summary;
  if (!summary) return;
  byId("todayLabel").textContent = summary.today || "--";
  const metrics = [
    {
      label: "图鉴条目",
      value: summary.catalog.entries,
      note: `${formatNumber(summary.catalog.manual_entries)} 项手动新增`,
      icon: "library-big",
      color: "var(--accent)",
    },
    {
      label: "群组",
      value: summary.groups.count,
      note: `${formatNumber(summary.groups.users)} 位成员`,
      icon: "messages-square",
      color: "var(--cyan)",
    },
    {
      label: "解锁记录",
      value: summary.groups.unlock_records,
      note: "全部群历史记录",
      icon: "badge-check",
      color: "var(--green)",
    },
    {
      label: "今日抽取",
      value: summary.groups.draws_today,
      note: "已使用次数",
      icon: "dices",
      color: "var(--yellow)",
    },
    {
      label: "回收站",
      value: summary.trash,
      note: "编号仍被保留",
      icon: "archive",
      color: "var(--purple)",
    },
  ];
  byId("overviewMetrics").innerHTML = metrics
    .map(
      (item) => `
        <article class="metric" style="--metric-color:${item.color}">
          <span class="metric-label">${escapeHtml(item.label)}${icon(item.icon)}</span>
          <strong class="metric-value">${formatNumber(item.value)}</strong>
          <span class="metric-note">${escapeHtml(item.note)}</span>
        </article>
      `,
    )
    .join("");

  const total = Math.max(1, Number(summary.catalog.entries || 0));
  const assetComplete = Math.max(0, total - Number(summary.catalog.missing_assets || 0));
  const descriptionComplete = Math.max(
    0,
    total - Number(summary.catalog.missing_descriptions || 0),
  );
  const healthRows = [
    {
      label: "素材图片",
      complete: assetComplete,
      missing: summary.catalog.missing_assets,
      color: "var(--cyan)",
    },
    {
      label: "简体中文简介",
      complete: descriptionComplete,
      missing: summary.catalog.missing_descriptions,
      color: "var(--green)",
    },
  ];
  byId("healthContent").innerHTML = healthRows
    .map((row) => {
      const percent = (row.complete * 100) / total;
      return `
        <div class="health-row">
          <div class="health-meta">
            <span>${escapeHtml(row.label)}</span>
            <strong>${formatPercent(percent)} · 缺 ${formatNumber(row.missing)}</strong>
          </div>
          <div class="progress-track"><div class="progress-fill" style="width:${percent}%;--fill:${row.color}"></div></div>
        </div>
      `;
    })
    .join("");

  const sources = (summary.catalog.sources || []).slice(0, 8);
  const maximum = Math.max(1, ...sources.map((item) => Number(item.count || 0)));
  const colors = ["var(--accent)", "var(--cyan)", "var(--yellow)", "var(--green)", "var(--purple)"];
  byId("sourceDistribution").innerHTML = sources.length
    ? sources
        .map(
          (item, index) => `
            <div class="bar-row">
              <div class="bar-meta"><span title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</span><strong>${formatNumber(item.count)}</strong></div>
              <div class="bar-track"><div class="bar-fill" style="width:${(Number(item.count || 0) * 100) / maximum}%;--bar-color:${colors[index % colors.length]}"></div></div>
            </div>
          `,
        )
        .join("")
    : '<div class="empty-state"><strong>暂无作品分布数据</strong></div>';

  byId("recentAudit").innerHTML = renderAuditItems(summary.recent_audit || [], true) ||
    '<div class="empty-state"><strong>暂无管理操作</strong></div>';
  renderTerminologyMetrics(summary.terminology);
  populateEntryFilters();
  refreshIcons();
}

function populateEntryFilters() {
  if (!state.summary) return;
  const sourceSelect = byId("entrySourceFilter");
  const kindSelect = byId("entryKindFilter");
  const selectedSource = sourceSelect.value;
  const selectedKind = kindSelect.value;
  sourceSelect.innerHTML = '<option value="">全部作品</option>' +
    (state.summary.catalog.sources || [])
      .map((item) => `<option value="${escapeHtml(item.name)}">${escapeHtml(item.name)} (${formatNumber(item.count)})</option>`)
      .join("");
  kindSelect.innerHTML = '<option value="">全部类型</option>' +
    (state.summary.catalog.kinds || [])
      .map((item) => `<option value="${escapeHtml(item.name)}">${escapeHtml(kindLabels[item.name] || item.name)} (${formatNumber(item.count)})</option>`)
      .join("");
  sourceSelect.value = selectedSource;
  kindSelect.value = selectedKind;
}

function renderTableSkeleton(target, columns, rows = 8) {
  target.innerHTML = Array.from({ length: rows }, () => `
    <tr class="skeleton-row">
      ${Array.from({ length: columns }, () => '<td><div class="skeleton skeleton-line"></div><div class="skeleton skeleton-line"></div></td>').join("")}
    </tr>
  `).join("");
}

function entryThumbnail(entry, size = "normal") {
  return `
    <span class="thumb-frame ${size === "small" ? "small-thumb" : ""}">
      ${entry.thumbnail ? `<img src="${escapeHtml(entry.thumbnail)}" alt="${escapeHtml(entry.name)}" />` : icon("image-off")}
    </span>
  `;
}

function entryStatusBadges(entry) {
  const badges = [];
  badges.push(entry.has_asset ? '<span class="badge good">图片正常</span>' : '<span class="badge bad">缺少图片</span>');
  if (entry.description_missing) {
    badges.push('<span class="badge warn">缺少简介</span>');
  } else if (entry.description_origin === "override") {
    badges.push('<span class="badge info">简介已修改</span>');
  } else {
    badges.push('<span class="badge good">简介正常</span>');
  }
  return badges.join("");
}

function renderEntries() {
  const body = byId("entryRows");
  const empty = byId("entryEmpty");
  if (!state.entries.items.length) {
    body.innerHTML = "";
    empty.hidden = false;
  } else {
    empty.hidden = true;
    body.innerHTML = state.entries.items
      .map(
        (entry) => `
          <tr class="is-clickable" data-entry-id="${entry.id}" tabindex="0">
            <td>${entryThumbnail(entry)}</td>
            <td>
              <div class="primary-cell">
                <strong>${escapeHtml(entry.name)}</strong>
                <span><span class="catalog-id">#${entry.id}</span>${entry.name_en ? ` · ${escapeHtml(entry.name_en)}` : ""}</span>
              </div>
            </td>
            <td>
              <div class="secondary-cell">
                <strong title="${escapeHtml(entry.source || "未标注")}">${escapeHtml(entry.source || "未标注")}</strong>
                <span>${entry.debut_year ? `${escapeHtml(entry.debut_year)} 年` : escapeHtml(entry.filename)}</span>
              </div>
            </td>
            <td><span class="badge">${escapeHtml(kindLabels[entry.catalog_kind] || entry.catalog_kind || "历史条目")}</span></td>
            <td><div class="badge-row">${entryStatusBadges(entry)}</div></td>
            <td><button class="icon-button small row-action" type="button" title="编辑素材" aria-label="编辑素材" data-open-entry="${entry.id}">${icon("pencil")}</button></td>
          </tr>
        `,
      )
      .join("");
  }
  renderPagination(byId("entryPagination"), state.entries, "entries");
  refreshIcons();
}

function terminologyCategoryLabel(value) {
  return terminologyCategoryLabels[String(value || "")] || String(value || "专有名词");
}

function terminologyOriginLabel(value) {
  return terminologyOriginLabels[String(value || "")] || String(value || "未知");
}

function terminologyListText(values) {
  return (Array.isArray(values) ? values : []).join("\n");
}

function parseTerminologyList(value) {
  return String(value || "")
    .split(/[\n|]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function renderTerminologyMetrics(stats = state.summary?.terminology) {
  const target = byId("terminologyMetrics");
  if (!target || !stats) return;
  const metrics = [
    { label: "词条总数", value: stats.entries, note: `启用 ${formatNumber(stats.enabled)}`, color: "var(--cyan)", icon: "languages" },
    { label: "已覆盖", value: stats.overrides, note: `自定义 ${formatNumber(stats.custom)}`, color: "var(--accent)", icon: "pencil-line" },
    { label: "缺少中文", value: stats.missing_zh, note: "优先补全显示名称", color: "var(--orange)", icon: "languages" },
    { label: "缺少英文", value: stats.missing_en, note: "影响双语输出", color: "var(--yellow)", icon: "text" },
    { label: "缺少日文", value: stats.missing_ja, note: "影响原文检索", color: "var(--green)", icon: "text-cursor-input" },
    { label: "匹配冲突", value: stats.conflicts, note: "优先级决定胜出项", color: "var(--red)", icon: "triangle-alert" },
  ];
  target.innerHTML = metrics
    .map(
      (item) => `
        <article class="metric" style="--metric-color:${item.color}">
          <div class="metric-label">${icon(item.icon)}<span>${escapeHtml(item.label)}</span></div>
          <strong class="metric-value">${formatNumber(item.value)}</strong>
          <span class="metric-note">${escapeHtml(item.note)}</span>
        </article>
      `,
    )
    .join("");
  refreshIcons();
}

function renderTerminologyFilters(categories = state.terminology.categories) {
  const select = byId("terminologyCategoryFilter");
  if (!select) return;
  const selected = state.terminologyFilters.category;
  select.innerHTML = `<option value="">全部分类</option>${(categories || [])
    .map(
      (category) => `<option value="${escapeHtml(category)}">${escapeHtml(terminologyCategoryLabel(category))}</option>`,
    )
    .join("")}`;
  select.value = selected;
}

function renderTerminology() {
  const body = byId("terminologyRows");
  const empty = byId("terminologyEmpty");
  if (!state.terminology.items.length) {
    body.innerHTML = "";
    empty.hidden = false;
  } else {
    empty.hidden = true;
    body.innerHTML = state.terminology.items
      .map(
        (entry) => `
          <tr class="is-clickable" data-term-id="${escapeHtml(entry.term_id)}" tabindex="0">
            <td>
              <div class="primary-cell">
                <strong>${escapeHtml(entry.canonical_label)}</strong>
                <span>${escapeHtml(entry.term_id)}</span>
              </div>
            </td>
            <td>
              <div class="secondary-cell terminology-languages">
                <strong>${escapeHtml(entry.zh_cn || "未收录中文")}</strong>
                <span>${escapeHtml(entry.en || "未收录英文")} · ${escapeHtml(entry.ja || "未收录日文")}</span>
              </div>
            </td>
            <td><span class="badge info">${escapeHtml(terminologyCategoryLabel(entry.category))}</span></td>
            <td>
              <div class="badge-row">
                <span class="badge ${entry.enabled ? "good" : "bad"}">${entry.enabled ? "启用" : "停用"}</span>
                <span class="badge">优先 ${escapeHtml(entry.priority)}</span>
                ${entry.conflict ? '<span class="badge warn">冲突</span>' : ""}
              </div>
            </td>
            <td><span class="badge ${entry.origin === "bundled" ? "" : "info"}">${escapeHtml(terminologyOriginLabel(entry.origin))}</span></td>
            <td><button class="icon-button small row-action" type="button" title="编辑术语" aria-label="编辑术语" data-open-term="${escapeHtml(entry.term_id)}">${icon("pencil")}</button></td>
          </tr>
        `,
      )
      .join("");
  }
  renderPagination(byId("terminologyPagination"), state.terminology, "terminology");
  refreshIcons();
}

async function loadTerminology(page = state.terminology.page || 1) {
  const sequence = ++state.requestSequence.terminology;
  renderTableSkeleton(byId("terminologyRows"), 6, 8);
  byId("terminologyEmpty").hidden = true;
  try {
    const data = await apiGet("admin/terminology", {
      ...state.terminologyFilters,
      page,
      page_size: 30,
    });
    if (sequence !== state.requestSequence.terminology) return;
    state.terminology = data;
    renderTerminologyFilters(data.categories || []);
    state.loaded.add("terminology");
    renderTerminology();
  } catch (error) {
    if (sequence !== state.requestSequence.terminology) return;
    byId("terminologyRows").innerHTML = "";
    byId("terminologyEmpty").hidden = false;
    toast("名称库加载失败", error.message, "error");
  }
}

async function openTerminology(termId) {
  const drawer = byId("terminologyDrawer");
  byId("terminologyDrawerBody").innerHTML = '<div class="drawer-loading">正在读取名称库条目</div>';
  byId("terminologyDrawerFooter").hidden = true;
  openDrawer(drawer);
  try {
    state.activeTerminology = await apiGet("admin/terminology/entry", { term_id: termId });
    renderTerminologyDrawer();
  } catch (error) {
    byId("terminologyDrawerBody").innerHTML = `<div class="empty-state"><strong>读取失败</strong><span>${escapeHtml(error.message)}</span></div>`;
    toast("名称库条目加载失败", error.message, "error");
  }
}

function openNewTerminology() {
  state.activeTerminology = {
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
  renderTerminologyDrawer();
  openDrawer(byId("terminologyDrawer"));
}

function renderTerminologyDrawer() {
  const entry = state.activeTerminology;
  if (!entry) return;
  const isNew = !entry.term_id;
  byId("terminologyDrawerTitle").textContent = isNew ? "新增名称库条目" : entry.canonical_label || entry.term_id;
  byId("terminologyDrawerBody").innerHTML = `
    <section class="form-section" style="padding-top:0">
      <div class="form-section-title"><h3>基本信息</h3><span>${escapeHtml(isNew ? "保存后自动生成自定义 ID" : `${terminologyOriginLabel(entry.origin)} · ${entry.term_id}`)}</span></div>
      <div class="form-grid two-columns">
        <label class="field"><span>术语 ID</span><input id="termIdInput" maxlength="180" value="${escapeHtml(entry.term_id)}" ${isNew ? "" : "readonly"} placeholder="例如 character:driblee" /></label>
        <label class="field"><span>分类</span><input id="termCategoryInput" maxlength="80" value="${escapeHtml(entry.category)}" placeholder="character / work / ability" /></label>
      </div>
    </section>
    <section class="form-section">
      <div class="form-section-title"><h3>规范名称</h3><span>输出格式为 中文（English）</span></div>
      <div class="form-grid">
        <label class="field"><span>简体中文</span><input id="termZhInput" maxlength="240" value="${escapeHtml(entry.zh_cn)}" /></label>
        <label class="field"><span>English</span><input id="termEnInput" maxlength="240" value="${escapeHtml(entry.en)}" /></label>
        <label class="field"><span>日本語</span><input id="termJaInput" maxlength="240" value="${escapeHtml(entry.ja)}" /></label>
      </div>
    </section>
    <section class="form-section">
      <div class="form-section-title"><h3>别名与来源</h3><span>每行一个，也支持 | 分隔</span></div>
      <div class="form-grid two-columns">
        <label class="field"><span>中文别名</span><textarea id="termAliasesZhInput" rows="4">${escapeHtml(terminologyListText(entry.aliases_zh))}</textarea></label>
        <label class="field"><span>英文别名</span><textarea id="termAliasesEnInput" rows="4">${escapeHtml(terminologyListText(entry.aliases_en))}</textarea></label>
        <label class="field"><span>日文别名</span><textarea id="termAliasesJaInput" rows="4">${escapeHtml(terminologyListText(entry.aliases_ja))}</textarea></label>
        <label class="field"><span>资料来源</span><textarea id="termSourcesInput" rows="4">${escapeHtml(terminologyListText(entry.sources))}</textarea></label>
      </div>
    </section>
    <section class="form-section">
      <div class="form-section-title"><h3>匹配策略</h3><span>数值越高越优先</span></div>
      <div class="form-grid two-columns">
        <label class="field"><span>中文名称状态</span><select id="termStatusInput">
          ${["official", "official_reused", "project", "transliterated", "unchanged", "unknown"].map((status) => `<option value="${status}" ${entry.zh_status === status ? "selected" : ""}>${terminologyStatusLabels[status]}</option>`).join("")}
        </select></label>
        <label class="field"><span>优先级</span><input id="termPriorityInput" type="number" min="-1000" max="1000" value="${escapeHtml(entry.priority)}" /></label>
      </div>
      <label class="check-field"><input id="termEnabledInput" type="checkbox" ${entry.enabled ? "checked" : ""} /><span>启用该条目匹配</span></label>
      <label class="check-field"><input id="termMatchCaseInput" type="checkbox" ${entry.match_case ? "checked" : ""} /><span>英文/日文匹配区分大小写</span></label>
    </section>
    <section class="form-section">
      <div class="form-section-title"><h3>维护备注</h3><span>不会发送给用户</span></div>
      <label class="field"><textarea id="termNotesInput" maxlength="5000" rows="5">${escapeHtml(entry.notes)}</textarea></label>
    </section>
    ${entry.conflicts?.length ? `<section class="form-section"><div class="form-section-title"><h3>匹配冲突</h3><span class="badge warn">${entry.conflicts.length} 项</span></div><div class="conflict-list">${entry.conflicts.map((conflict) => `<div class="conflict-item"><strong>${escapeHtml(conflict.alias)}</strong><span>${escapeHtml(conflict.entries.map((item) => item.label).join(" · "))}</span></div>`).join("")}</div></section>` : ""}
  `;
  byId("terminologyDrawerFooter").hidden = false;
  byId("restoreTerminologyButton").hidden = !entry.has_override;
  byId("restoreTerminologyButton").innerHTML = entry.origin === "custom"
    ? `${icon("trash-2")}删除自定义`
    : `${icon("rotate-ccw")}恢复内置`;
  refreshIcons();
}

async function saveTerminology() {
  if (!state.activeTerminology) return;
  const button = byId("saveTerminologyButton");
  setButtonBusy(button, true, "保存中");
  try {
    state.activeTerminology = await apiPost("admin/terminology/save", {
      term_id: byId("termIdInput").value.trim(),
      category: byId("termCategoryInput").value.trim(),
      zh_cn: byId("termZhInput").value.trim(),
      en: byId("termEnInput").value.trim(),
      ja: byId("termJaInput").value.trim(),
      aliases_zh: parseTerminologyList(byId("termAliasesZhInput").value),
      aliases_en: parseTerminologyList(byId("termAliasesEnInput").value),
      aliases_ja: parseTerminologyList(byId("termAliasesJaInput").value),
      sources: parseTerminologyList(byId("termSourcesInput").value),
      zh_status: byId("termStatusInput").value,
      priority: Number(byId("termPriorityInput").value || 100),
      enabled: byId("termEnabledInput").checked,
      match_case: byId("termMatchCaseInput").checked,
      notes: byId("termNotesInput").value,
    });
    renderTerminologyDrawer();
    await Promise.all([loadTerminology(1), refreshSummary()]);
    toast("名称库条目已保存", state.activeTerminology.canonical_label);
  } catch (error) {
    toast("名称库保存失败", error.message, "error");
  } finally {
    setButtonBusy(button, false);
  }
}

async function restoreTerminology() {
  const entry = state.activeTerminology;
  if (!entry?.term_id || !entry.has_override) return;
  const accepted = await confirmAction({
    title: entry.origin === "custom" ? "删除自定义名称" : "恢复内置名称",
    message: entry.origin === "custom"
      ? `删除自定义术语 ${entry.term_id}，该操作不会影响内置名称库。`
      : `删除 ${entry.term_id} 的管理员覆盖，恢复插件内置名称库版本。`,
    accept: entry.origin === "custom" ? "删除自定义" : "恢复内置",
    tone: "warning",
  });
  if (!accepted) return;
  try {
    const result = await apiPost("admin/terminology/restore", { term_id: entry.term_id });
    if (result.deleted) {
      closeDrawers();
      state.activeTerminology = null;
      await Promise.all([loadTerminology(state.terminology.page), refreshSummary()]);
      toast("自定义术语已删除", entry.term_id);
      return;
    }
    state.activeTerminology = result;
    renderTerminologyDrawer();
    await Promise.all([loadTerminology(state.terminology.page), refreshSummary()]);
    toast("已恢复内置名称", state.activeTerminology.canonical_label);
  } catch (error) {
    toast("恢复名称失败", error.message, "error");
  }
}

function downloadTerminologyExport(payload) {
  const binary = atob(payload.content_base64 || "");
  const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0));
  const blob = new Blob([bytes], { type: payload.mime_type || "application/octet-stream" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = payload.filename || "kirby_terminology.json";
  anchor.click();
  URL.revokeObjectURL(url);
}

async function exportTerminology(format) {
  try {
    const payload = await apiGet("admin/terminology/export", { format, scope: "merged" });
    downloadTerminologyExport(payload);
    toast("名称库已导出", payload.filename);
  } catch (error) {
    toast("名称库导出失败", error.message, "error");
  }
}

async function importTerminology(file) {
  if (!file) return;
  const button = byId("terminologyImportButton");
  setButtonBusy(button, true, "导入中");
  try {
    const result = await apiUpload("admin/terminology/import", file);
    state.loaded.delete("terminology");
    await Promise.all([loadTerminology(1), refreshSummary()]);
    toast("名称库导入完成", `写入 ${formatNumber(result.imported)} 条覆盖记录`);
  } catch (error) {
    toast("名称库导入失败", error.message, "error");
  } finally {
    setButtonBusy(button, false);
    byId("terminologyImportInput").value = "";
  }
}

function renderPagination(container, data, scope) {
  const page = Number(data.page || 1);
  const pages = Math.max(1, Number(data.pages || 1));
  const total = Number(data.total || 0);
  container.innerHTML = `
    <span class="pagination-meta">共 ${formatNumber(total)} 条</span>
    <div class="pagination-actions">
      <button class="icon-button small" type="button" title="上一页" aria-label="上一页" data-page-scope="${scope}" data-page="${page - 1}" ${page <= 1 ? "disabled" : ""}>${icon("chevron-left")}</button>
      <span class="page-number">${page} / ${pages}</span>
      <button class="icon-button small" type="button" title="下一页" aria-label="下一页" data-page-scope="${scope}" data-page="${page + 1}" ${page >= pages ? "disabled" : ""}>${icon("chevron-right")}</button>
    </div>
  `;
  refreshIcons();
}

async function loadEntries(page = state.entries.page || 1) {
  const sequence = ++state.requestSequence.entries;
  renderTableSkeleton(byId("entryRows"), 6);
  byId("entryEmpty").hidden = true;
  try {
    const data = await apiGet("admin/entries", {
      ...state.entryFilters,
      page,
      page_size: 30,
    });
    if (sequence !== state.requestSequence.entries) return;
    state.entries = data;
    state.loaded.add("catalog");
    renderEntries();
  } catch (error) {
    if (sequence !== state.requestSequence.entries) return;
    byId("entryRows").innerHTML = "";
    byId("entryEmpty").hidden = false;
    toast("素材库加载失败", error.message, "error");
  }
}

function openDrawer(drawer) {
  closeDrawers();
  const scrim = byId("drawerScrim");
  scrim.hidden = false;
  requestAnimationFrame(() => scrim.classList.add("is-visible"));
  drawer.classList.add("is-open");
  drawer.setAttribute("aria-hidden", "false");
  document.body.style.overflow = "hidden";
}

function closeDrawers() {
  all(".drawer.is-open").forEach((drawer) => {
    drawer.classList.remove("is-open");
    drawer.setAttribute("aria-hidden", "true");
  });
  const scrim = byId("drawerScrim");
  scrim.classList.remove("is-visible");
  window.setTimeout(() => {
    if (!document.querySelector(".drawer.is-open")) scrim.hidden = true;
  }, 190);
  document.body.style.overflow = "";
  hideComboResults();
}

async function openEntry(entryId) {
  const drawer = byId("entryDrawer");
  byId("entryDrawerBody").innerHTML = '<div class="drawer-loading">正在读取素材资料</div>';
  byId("entryDrawerFooter").hidden = true;
  openDrawer(drawer);
  try {
    state.activeEntry = await apiGet(`admin/entries/${entryId}`);
    state.entryDescriptionDirty = false;
    renderEntryDrawer();
  } catch (error) {
    byId("entryDrawerBody").innerHTML = `<div class="empty-state"><strong>读取失败</strong><span>${escapeHtml(error.message)}</span></div>`;
    toast("素材详情加载失败", error.message, "error");
  }
}

function renderEntryDrawer() {
  const entry = state.activeEntry;
  if (!entry) return;
  const sourceUrl = safeUrl(entry.source_url);
  const metadata = {
    "文件名": entry.filename,
    "固定键": entry.entry_key,
    "英文页名": entry.page_title || entry.name_en,
    "变体键": entry.variant_key,
    "图鉴类型": kindLabels[entry.catalog_kind] || entry.catalog_kind,
    ...entry.metadata,
  };
  byId("entryDrawerTitle").textContent = `#${entry.id} ${entry.name}`;
  byId("entryDrawerBody").innerHTML = `
    <section class="entry-visual">
      <div class="entry-image-large">
        ${entry.thumbnail ? `<img src="${escapeHtml(entry.thumbnail)}" alt="${escapeHtml(entry.name)}" />` : icon("image-off")}
      </div>
      <div class="entry-identity">
        <span class="badge info">图鉴编号 #${entry.id}</span>
        <h3>${escapeHtml(entry.name)}</h3>
        <p>${escapeHtml(entry.name_en || "未收录英文名称")}</p>
        <input id="replaceEntryImageInput" type="file" accept="image/png,image/jpeg,image/gif,image/bmp,image/webp" hidden />
        <button id="replaceEntryImageButton" class="button secondary" type="button">${icon("image-up")}替换图片</button>
      </div>
    </section>

    <section class="form-section">
      <div class="form-section-title"><h3>基础资料</h3><span>名称或作品变化会同步重命名素材文件</span></div>
      <div class="form-grid">
        <label class="field"><span>盟友名称</span><input id="entryNameInput" maxlength="160" value="${escapeHtml(entry.name)}" /></label>
        <label class="field"><span>首次登场作品</span><input id="entrySourceInput" maxlength="240" value="${escapeHtml(entry.source || "")}" /></label>
      </div>
    </section>

    <section class="form-section">
      <div class="form-section-title">
        <h3>简体中文简介</h3>
        <span class="badge ${entry.description_origin === "override" ? "info" : entry.description_missing ? "warn" : "good"}">${entry.description_origin === "override" ? "管理员版本" : entry.description_missing ? "尚未填写" : "内置资料"}</span>
      </div>
      <label class="field">
        <textarea id="entryDescriptionInput" maxlength="30000" rows="12">${escapeHtml(entry.description || "")}</textarea>
        <span class="field-hint">最多 30,000 字；抽取消息中的显示长度仍由插件配置控制。</span>
      </label>
      ${entry.description_origin === "override" ? `<button id="restoreEntryDescriptionButton" class="text-button" type="button">${icon("rotate-ccw")}恢复内置简介</button>` : ""}
    </section>

    <section class="form-section">
      <div class="form-section-title"><h3>条目标识</h3>${sourceUrl ? `<a class="text-button" href="${escapeHtml(sourceUrl)}" target="_blank" rel="noreferrer">来源页面${icon("external-link")}</a>` : ""}</div>
      <dl class="metadata-list">
        ${Object.entries(metadata)
          .filter(([, value]) => value !== undefined && value !== null && value !== "")
          .map(([key, value]) => `<div class="metadata-row"><dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value)}</dd></div>`)
          .join("")}
      </dl>
    </section>
  `;
  byId("entryDrawerFooter").hidden = false;
  byId("entryDescriptionInput").addEventListener("input", () => {
    state.entryDescriptionDirty = true;
  });
  byId("replaceEntryImageButton").addEventListener("click", () => byId("replaceEntryImageInput").click());
  byId("replaceEntryImageInput").addEventListener("change", replaceEntryImage);
  byId("restoreEntryDescriptionButton")?.addEventListener("click", restoreEntryDescription);
  refreshIcons();
}

async function saveEntry() {
  if (!state.activeEntry) return;
  const button = byId("saveEntryButton");
  setButtonBusy(button, true, "保存中");
  try {
    const payload = {
      id: state.activeEntry.id,
      name: byId("entryNameInput").value,
      source: byId("entrySourceInput").value,
      description_action: state.entryDescriptionDirty ? "set" : "keep",
      description: byId("entryDescriptionInput").value,
    };
    state.activeEntry = await apiPost("admin/entries/save", payload);
    state.entryDescriptionDirty = false;
    renderEntryDrawer();
    await Promise.all([loadEntries(state.entries.page), refreshSummary()]);
    toast("素材资料已保存", `#${state.activeEntry.id} ${state.activeEntry.name}`);
  } catch (error) {
    toast("保存失败", error.message, "error");
  } finally {
    setButtonBusy(button, false);
  }
}

async function restoreEntryDescription() {
  const entry = state.activeEntry;
  if (!entry) return;
  const accepted = await confirmAction({
    title: "恢复内置简介",
    message: `将删除 #${entry.id} ${entry.name} 的管理员简介，恢复为插件内置版本。`,
    accept: "恢复",
    tone: "warning",
  });
  if (!accepted) return;
  try {
    state.activeEntry = await apiPost("admin/entries/save", {
      id: entry.id,
      name: byId("entryNameInput").value,
      source: byId("entrySourceInput").value,
      description_action: "restore",
    });
    state.entryDescriptionDirty = false;
    renderEntryDrawer();
    await Promise.all([loadEntries(state.entries.page), refreshSummary()]);
    toast("已恢复内置简介", `#${entry.id} ${entry.name}`);
  } catch (error) {
    toast("恢复失败", error.message, "error");
  }
}

async function replaceEntryImage(event) {
  const file = event.target.files?.[0];
  const entry = state.activeEntry;
  if (!file || !entry) return;
  const button = byId("replaceEntryImageButton");
  setButtonBusy(button, true, "上传中");
  try {
    state.activeEntry = await apiUpload(`admin/entries/${entry.id}/image`, file);
    renderEntryDrawer();
    await loadEntries(state.entries.page);
    toast("素材图片已替换", `#${entry.id} ${entry.name}`);
  } catch (error) {
    toast("图片替换失败", error.message, "error");
  } finally {
    setButtonBusy(button, false);
    event.target.value = "";
  }
}

async function deleteActiveEntry() {
  const entry = state.activeEntry;
  if (!entry) return;
  const accepted = await confirmAction({
    title: "移入回收站",
    message: `#${entry.id} ${entry.name} 将从当前图鉴移除，相关用户引用会保存到回收站快照。`,
    accept: "移入回收站",
  });
  if (!accepted) return;
  const button = byId("deleteEntryButton");
  setButtonBusy(button, true, "处理中");
  try {
    await apiPost("admin/entries/delete", { id: entry.id });
    closeDrawers();
    state.activeEntry = null;
    await Promise.all([loadEntries(Math.min(state.entries.page, state.entries.pages)), refreshSummary()]);
    state.loaded.delete("trash");
    toast("素材已移入回收站", `#${entry.id} ${entry.name}`);
  } catch (error) {
    toast("删除失败", error.message, "error");
  } finally {
    setButtonBusy(button, false);
  }
}

function openAddDialog() {
  state.addUpload = null;
  byId("addEntryForm").reset();
  byId("addImagePreview").hidden = true;
  byId("addUploadPlaceholder").hidden = false;
  byId("addEntryDialog").showModal();
}

async function stageAddImage(file) {
  if (!file) return;
  const zone = byId("addUploadZone");
  zone.setAttribute("aria-busy", "true");
  try {
    state.addUpload = await apiUpload("admin/uploads/image", file);
    byId("addImagePreview").src = state.addUpload.preview;
    byId("addImagePreview").hidden = false;
    byId("addUploadPlaceholder").hidden = true;
    toast("图片已就绪", `${formatNumber(state.addUpload.bytes)} bytes`);
  } catch (error) {
    state.addUpload = null;
    toast("图片上传失败", error.message, "error");
  } finally {
    zone.removeAttribute("aria-busy");
  }
}

async function submitAddEntry(event) {
  event.preventDefault();
  if (!state.addUpload?.token) {
    toast("请先选择素材图片", "新增图鉴条目必须包含图片", "error");
    return;
  }
  const button = byId("confirmAddEntryButton");
  setButtonBusy(button, true, "添加中");
  try {
    const entry = await apiPost("admin/entries/add", {
      upload_token: state.addUpload.token,
      name: byId("addName").value,
      source: byId("addSource").value,
      description: byId("addDescription").value,
    });
    byId("addEntryDialog").close();
    state.addUpload = null;
    await Promise.all([refreshSummary(), loadEntries(1)]);
    await switchView("catalog");
    toast("素材已添加", `#${entry.id} ${entry.name}`);
    await openEntry(entry.id);
  } catch (error) {
    toast("新增失败", error.message, "error");
  } finally {
    setButtonBusy(button, false);
  }
}

function renderGroups() {
  const list = byId("groupList");
  byId("groupTotalLabel").textContent = `${formatNumber(state.groups.total)} 个群组`;
  list.innerHTML = state.groups.items.length
    ? state.groups.items
        .map(
          (group) => `
            <button class="group-item ${group.group_id === state.selectedGroup ? "is-active" : ""}" type="button" data-group-id="${escapeHtml(group.group_id)}">
              <span class="group-avatar">${icon("messages-square")}</span>
              <span class="group-main"><strong>${escapeHtml(group.group_id)}</strong><span>${formatNumber(group.users)} 人 · ${formatPercent(group.completion)}</span></span>
              <span class="group-stat">${formatNumber(group.draws_today)} 抽</span>
            </button>
          `,
        )
        .join("")
    : '<div class="empty-state"><strong>没有群组数据</strong></div>';
  renderPagination(byId("groupPagination"), state.groups, "groups");
  refreshIcons();
}

async function loadGroups(page = state.groups.page || 1) {
  const sequence = ++state.requestSequence.groups;
  byId("groupList").innerHTML = Array.from({ length: 6 }, () => '<div class="skeleton" style="height:62px"></div>').join("");
  try {
    const data = await apiGet("admin/groups", {
      query: state.groupQuery,
      page,
      page_size: 30,
    });
    if (sequence !== state.requestSequence.groups) return;
    state.groups = data;
    state.loaded.add("groups");
    renderGroups();
  } catch (error) {
    if (sequence !== state.requestSequence.groups) return;
    byId("groupList").innerHTML = '<div class="empty-state"><strong>群组加载失败</strong></div>';
    toast("群组加载失败", error.message, "error");
  }
}

function renderUsers() {
  const body = byId("userRows");
  const empty = byId("userEmpty");
  const group = state.users.group;
  byId("selectedGroupId").textContent = state.selectedGroup;
  byId("selectedGroupSummary").textContent = group
    ? `${formatNumber(group.users)} 位成员 · ${formatNumber(group.unlock_records)} 条解锁 · 今日 ${formatNumber(group.draws_today)} 抽`
    : "--";
  if (!state.users.items.length) {
    body.innerHTML = "";
    empty.hidden = false;
  } else {
    empty.hidden = true;
    body.innerHTML = state.users.items
      .map(
        (user) => `
          <tr class="is-clickable" data-user-id="${escapeHtml(user.user_id)}" tabindex="0">
            <td><div class="primary-cell"><strong>${escapeHtml(user.nickname)}</strong><span>${escapeHtml(user.user_id)}</span></div></td>
            <td>
              <div class="mini-progress">
                <span>${formatNumber(user.unlocked)} / ${formatNumber(user.total)} · ${formatPercent(user.completion)}</span>
                <div class="progress-track"><div class="progress-fill" style="width:${Math.min(100, Number(user.completion || 0))}%;--fill:var(--green)"></div></div>
              </div>
            </td>
            <td><div class="secondary-cell"><strong>${escapeHtml(user.current?.name || "未设置")}</strong><span>${escapeHtml(user.current_date || "--")}</span></div></td>
            <td><div class="count-pair"><span class="badge info">已用 ${formatNumber(user.draw_count)}</span><span class="badge warn">额外 ${formatNumber(user.draw_bonus)}</span></div></td>
            <td><button class="icon-button small row-action" type="button" title="编辑成员" aria-label="编辑成员" data-open-user="${escapeHtml(user.user_id)}">${icon("pencil")}</button></td>
          </tr>
        `,
      )
      .join("");
  }
  renderPagination(byId("userPagination"), state.users, "users");
  refreshIcons();
}

async function selectGroup(groupId) {
  state.selectedGroup = String(groupId || "");
  state.users.page = 1;
  state.userQuery = "";
  byId("userSearch").value = "";
  byId("groupSelectionEmpty").hidden = true;
  byId("groupSelectionContent").hidden = false;
  renderGroups();
  await loadUsers(1);
}

async function loadUsers(page = state.users.page || 1) {
  if (!state.selectedGroup) return;
  const sequence = ++state.requestSequence.users;
  renderTableSkeleton(byId("userRows"), 5, 7);
  byId("userEmpty").hidden = true;
  try {
    const data = await apiGet("admin/groups/users", {
      group_id: state.selectedGroup,
      query: state.userQuery,
      page,
      page_size: 30,
    });
    if (sequence !== state.requestSequence.users) return;
    state.users = data;
    renderUsers();
  } catch (error) {
    if (sequence !== state.requestSequence.users) return;
    byId("userRows").innerHTML = "";
    byId("userEmpty").hidden = false;
    toast("成员数据加载失败", error.message, "error");
  }
}

async function openUser(userId) {
  const drawer = byId("userDrawer");
  byId("userDrawerBody").innerHTML = '<div class="drawer-loading">正在读取成员图鉴</div>';
  byId("userDrawerFooter").hidden = true;
  openDrawer(drawer);
  try {
    state.activeUser = await apiGet("admin/groups/user", {
      group_id: state.selectedGroup,
      user_id: userId,
    });
    state.userCurrentSelection = state.activeUser.current?.id ? String(state.activeUser.current.id) : "";
    state.unlockSelection = "";
    renderUserDrawer();
  } catch (error) {
    byId("userDrawerBody").innerHTML = `<div class="empty-state"><strong>读取失败</strong><span>${escapeHtml(error.message)}</span></div>`;
    toast("成员详情加载失败", error.message, "error");
  }
}

function renderUserDrawer() {
  const user = state.activeUser;
  if (!user) return;
  const current = user.current;
  byId("userDrawerTitle").textContent = `${user.nickname} · ${user.user_id}`;
  byId("userDrawerBody").innerHTML = `
    <section class="form-section" style="padding-top:0">
      <div class="form-section-title"><h3>成员资料</h3><span>${formatNumber(user.unlocked)} / ${formatNumber(user.total)} · ${formatPercent(user.completion)}</span></div>
      <div class="form-grid two-columns">
        <label class="field"><span>群昵称</span><input id="userNicknameInput" maxlength="160" value="${escapeHtml(user.nickname)}" /></label>
        <label class="field"><span>连续未解锁新条目</span><input id="userNoNewInput" type="number" min="0" step="1" value="${Number(user.no_new_count || 0)}" /></label>
        <label class="field"><span>今日已用次数</span><input id="userDrawCountInput" type="number" min="0" step="1" value="${Number(user.draw_count || 0)}" /></label>
        <label class="field"><span>今日额外次数</span><input id="userDrawBonusInput" type="number" min="0" step="1" value="${Number(user.draw_bonus || 0)}" /></label>
      </div>
    </section>

    <section class="form-section">
      <div class="form-section-title"><h3>当前盟友</h3><span>${escapeHtml(user.current_date || "未设置日期")}</span></div>
      <div class="current-ally-block">
        ${current ? entryThumbnail(current) : '<span class="thumb-frame">' + icon("circle-slash") + "</span>"}
        <div class="primary-cell"><strong>${escapeHtml(current?.name || "未设置当前盟友")}</strong><span>${current ? `#${current.id} · ${escapeHtml(current.source || "未标注")}` : ""}</span></div>
      </div>
      <div class="form-grid two-columns" style="margin-top:12px">
        <div class="field combo-wrap">
          <span>搜索并选择盟友</span>
          <div class="combo-input-row">
            <input id="currentEntrySearch" autocomplete="off" value="${current ? `#${current.id} ${escapeHtml(current.name)}` : ""}" placeholder="输入编号或名称" />
            <button id="clearCurrentEntryButton" class="icon-button" type="button" title="清空当前盟友" aria-label="清空当前盟友">${icon("x")}</button>
          </div>
          <div id="currentEntryResults" class="combo-results" hidden></div>
        </div>
        <label class="field"><span>当前盟友日期</span><input id="userCurrentDateInput" type="date" value="${escapeHtml(user.current_date || "")}" /></label>
      </div>
    </section>

    <section class="form-section">
      <div class="form-section-title"><h3>已解锁图鉴</h3><span class="unlock-count">${formatNumber(user.unlock_records)} 条历史记录</span></div>
      <div class="form-grid two-columns">
        <div class="field combo-wrap">
          <span>增加解锁</span>
          <div class="combo-input-row"><input id="unlockEntrySearch" autocomplete="off" placeholder="输入编号或名称" /><button id="addUnlockButton" class="icon-button" type="button" title="增加解锁" aria-label="增加解锁" disabled>${icon("plus")}</button></div>
          <div id="unlockEntryResults" class="combo-results" hidden></div>
        </div>
        <label class="field"><span>解锁日期</span><input id="unlockDateInput" type="date" value="${escapeHtml(state.summary?.today || "")}" /></label>
      </div>
      <div class="unlock-list">
        ${(user.unlocks || []).length
          ? user.unlocks
              .map(
                (entry) => `
                  <div class="unlock-item ${entry.missing ? "missing" : ""}">
                    ${entryThumbnail(entry)}
                    <div class="primary-cell"><strong>${escapeHtml(entry.name)}</strong><span>${entry.id ? `#${entry.id} · ` : ""}${escapeHtml(entry.unlock_date || "--")}</span></div>
                    ${entry.id ? `<button class="icon-button small" type="button" title="移除解锁" aria-label="移除解锁" data-remove-unlock="${entry.id}">${icon("minus")}</button>` : '<span class="badge bad">素材缺失</span>'}
                  </div>
                `,
              )
              .join("")
          : '<div class="empty-state"><strong>尚未解锁图鉴</strong></div>'}
      </div>
    </section>
  `;
  byId("userDrawerFooter").hidden = false;
  const currentSearch = debounce(() => searchEntryCombo("current"), 260);
  const unlockSearch = debounce(() => searchEntryCombo("unlock"), 260);
  byId("currentEntrySearch").addEventListener("input", () => {
    state.userCurrentSelection = "";
    currentSearch();
  });
  byId("unlockEntrySearch").addEventListener("input", () => {
    state.unlockSelection = "";
    byId("addUnlockButton").disabled = true;
    unlockSearch();
  });
  byId("clearCurrentEntryButton").addEventListener("click", () => {
    state.userCurrentSelection = "";
    byId("currentEntrySearch").value = "";
    hideComboResults();
  });
  byId("addUnlockButton").addEventListener("click", addUserUnlock);
  all("[data-remove-unlock]", byId("userDrawerBody")).forEach((button) => {
    button.addEventListener("click", () => removeUserUnlock(button.dataset.removeUnlock));
  });
  refreshIcons();
}

async function searchEntryCombo(target) {
  const input = byId(target === "current" ? "currentEntrySearch" : "unlockEntrySearch");
  const results = byId(target === "current" ? "currentEntryResults" : "unlockEntryResults");
  if (!input || !results) return;
  const query = input.value.trim();
  if (!query) {
    results.hidden = true;
    return;
  }
  const sequence = ++state.requestSequence[target];
  results.hidden = false;
  results.innerHTML = '<div class="empty-state" style="min-height:80px"><span>搜索中</span></div>';
  try {
    const data = await apiGet("admin/entries", {
      query,
      page: 1,
      page_size: 20,
      status: "all",
      sort: "id_asc",
    });
    if (sequence !== state.requestSequence[target]) return;
    results.innerHTML = data.items?.length
      ? data.items
          .map(
            (entry) => `
              <button class="combo-option" type="button" data-combo-target="${target}" data-combo-id="${entry.id}" data-combo-label="#${entry.id} ${escapeHtml(entry.name)}">
                ${entryThumbnail(entry)}
                <span class="primary-cell"><strong>${escapeHtml(entry.name)}</strong><span>#${entry.id} · ${escapeHtml(entry.source || "未标注")}</span></span>
              </button>
            `,
          )
          .join("")
      : '<div class="empty-state" style="min-height:90px"><strong>没有匹配条目</strong></div>';
    all("[data-combo-target]", results).forEach((button) => {
      button.addEventListener("click", () => selectComboEntry(button));
    });
    refreshIcons();
  } catch (error) {
    results.innerHTML = `<div class="empty-state" style="min-height:90px"><strong>搜索失败</strong><span>${escapeHtml(error.message)}</span></div>`;
  }
}

function selectComboEntry(button) {
  const target = button.dataset.comboTarget;
  const id = button.dataset.comboId;
  const label = button.dataset.comboLabel;
  if (target === "current") {
    state.userCurrentSelection = id;
    byId("currentEntrySearch").value = label;
    byId("currentEntryResults").hidden = true;
  } else {
    state.unlockSelection = id;
    byId("unlockEntrySearch").value = label;
    byId("unlockEntryResults").hidden = true;
    byId("addUnlockButton").disabled = false;
  }
}

function hideComboResults() {
  all(".combo-results").forEach((element) => {
    element.hidden = true;
  });
}

async function saveUser() {
  const user = state.activeUser;
  if (!user) return;
  const button = byId("saveUserButton");
  setButtonBusy(button, true, "保存中");
  try {
    state.activeUser = await apiPost("admin/groups/user/save", {
      group_id: user.group_id,
      user_id: user.user_id,
      nickname: byId("userNicknameInput").value,
      no_new_count: Number(byId("userNoNewInput").value || 0),
      current_id: state.userCurrentSelection,
      current_date: byId("userCurrentDateInput").value,
      draw_count: Number(byId("userDrawCountInput").value || 0),
      draw_bonus: Number(byId("userDrawBonusInput").value || 0),
    });
    state.userCurrentSelection = state.activeUser.current?.id ? String(state.activeUser.current.id) : "";
    renderUserDrawer();
    await Promise.all([loadUsers(state.users.page), loadGroups(state.groups.page), refreshSummary()]);
    toast("成员数据已保存", `${user.nickname} · ${user.user_id}`);
  } catch (error) {
    toast("保存失败", error.message, "error");
  } finally {
    setButtonBusy(button, false);
  }
}

async function addUserUnlock() {
  const user = state.activeUser;
  if (!user || !state.unlockSelection) return;
  const button = byId("addUnlockButton");
  setButtonBusy(button, true, "");
  try {
    state.activeUser = await apiPost("admin/groups/user/unlock", {
      group_id: user.group_id,
      user_id: user.user_id,
      entry_id: state.unlockSelection,
      action: "add",
      unlock_date: byId("unlockDateInput").value,
    });
    state.unlockSelection = "";
    renderUserDrawer();
    await Promise.all([loadUsers(state.users.page), loadGroups(state.groups.page), refreshSummary()]);
    toast("解锁记录已增加", user.nickname);
  } catch (error) {
    toast("增加解锁失败", error.message, "error");
  } finally {
    setButtonBusy(button, false);
  }
}

async function removeUserUnlock(entryId) {
  const user = state.activeUser;
  if (!user) return;
  const accepted = await confirmAction({
    title: "移除解锁记录",
    message: `将从 ${user.nickname} 的个人图鉴中移除 #${entryId}。`,
    accept: "移除",
    tone: "warning",
  });
  if (!accepted) return;
  try {
    state.activeUser = await apiPost("admin/groups/user/unlock", {
      group_id: user.group_id,
      user_id: user.user_id,
      entry_id: entryId,
      action: "remove",
    });
    renderUserDrawer();
    await Promise.all([loadUsers(state.users.page), loadGroups(state.groups.page), refreshSummary()]);
    toast("解锁记录已移除", user.nickname);
  } catch (error) {
    toast("移除失败", error.message, "error");
  }
}

async function deleteActiveUser() {
  const user = state.activeUser;
  if (!user) return;
  const accepted = await confirmAction({
    title: "删除成员图鉴数据",
    message: `${user.nickname}（${user.user_id}）的当前盟友、全部解锁记录和抽取计数都会被删除。`,
    accept: "删除成员数据",
  });
  if (!accepted) return;
  try {
    await apiPost("admin/groups/user/delete", {
      group_id: user.group_id,
      user_id: user.user_id,
    });
    closeDrawers();
    state.activeUser = null;
    await Promise.all([loadUsers(1), loadGroups(state.groups.page), refreshSummary()]);
    toast("成员数据已删除", `${user.nickname} · ${user.user_id}`);
  } catch (error) {
    toast("删除失败", error.message, "error");
  }
}

async function resetSelectedGroupDraws() {
  if (!state.selectedGroup) return;
  const accepted = await confirmAction({
    title: "重置今日群抽取次数",
    message: `群 ${state.selectedGroup} 今天的已用次数和额外机会都会清零，历史日期与图鉴记录不受影响。`,
    accept: "重置今日次数",
    tone: "warning",
  });
  if (!accepted) return;
  const button = byId("resetGroupDrawsButton");
  setButtonBusy(button, true, "重置中");
  try {
    const result = await apiPost("admin/groups/reset-draws", {
      group_id: state.selectedGroup,
    });
    await Promise.all([loadUsers(state.users.page), loadGroups(state.groups.page), refreshSummary()]);
    toast("今日次数已重置", `影响 ${formatNumber(result.users)} 位成员`);
  } catch (error) {
    toast("重置失败", error.message, "error");
  } finally {
    setButtonBusy(button, false);
  }
}

function renderTrash() {
  const body = byId("trashRows");
  byId("trashCountLabel").textContent = `${formatNumber(state.trash.length)} 条记录`;
  byId("trashEmpty").hidden = Boolean(state.trash.length);
  body.innerHTML = state.trash
    .map(
      (item) => `
        <tr>
          <td><div class="primary-cell"><strong>${escapeHtml(item.name)}</strong><span>#${item.id} · ${escapeHtml(item.filename)}</span></div></td>
          <td><div class="secondary-cell"><strong>${escapeHtml(item.source || "未标注")}</strong><span>${item.asset_present ? "含归档图片" : "无本地图片"}</span></div></td>
          <td>${escapeHtml(formatDateTime(item.deleted_at))}<span class="cell-muted">${escapeHtml(item.deleted_by || "dashboard")}</span></td>
          <td><span class="badge warn">${formatNumber(item.affected_users)} 人</span></td>
          <td><button class="icon-button small row-action" type="button" title="恢复素材" aria-label="恢复素材" data-restore-token="${escapeHtml(item.token)}">${icon("archive-restore")}</button></td>
        </tr>
      `,
    )
    .join("");
  refreshIcons();
}

async function loadTrash() {
  renderTableSkeleton(byId("trashRows"), 5, 6);
  byId("trashEmpty").hidden = true;
  try {
    const data = await apiGet("admin/trash");
    state.trash = data.items || [];
    state.loaded.add("trash");
    renderTrash();
  } catch (error) {
    byId("trashRows").innerHTML = "";
    byId("trashEmpty").hidden = false;
    toast("回收站加载失败", error.message, "error");
  }
}

async function restoreTrashEntry(token) {
  const item = state.trash.find((row) => row.token === token);
  if (!item) return;
  const accepted = await confirmAction({
    title: "恢复图鉴条目",
    message: `恢复 #${item.id} ${item.name}、归档图片、简介覆盖和仍可安全合并的用户引用。`,
    accept: "恢复条目",
    tone: "warning",
  });
  if (!accepted) return;
  try {
    const entry = await apiPost("admin/trash/restore", { token });
    await Promise.all([loadTrash(), refreshSummary()]);
    state.loaded.delete("catalog");
    state.loaded.delete("groups");
    toast("图鉴条目已恢复", `#${entry.id} ${entry.name}`);
  } catch (error) {
    toast("恢复失败", error.message, "error");
  }
}

function renderAudit() {
  byId("auditRows").innerHTML = renderAuditItems(state.audit);
  byId("auditEmpty").hidden = Boolean(state.audit.length);
  refreshIcons();
}

async function loadAudit() {
  byId("auditRows").innerHTML = Array.from({ length: 8 }, () => '<div class="skeleton" style="height:68px;margin-bottom:8px"></div>').join("");
  byId("auditEmpty").hidden = true;
  try {
    const data = await apiGet("admin/audit", { limit: 300 });
    state.audit = data.items || [];
    state.loaded.add("audit");
    renderAudit();
  } catch (error) {
    byId("auditRows").innerHTML = "";
    byId("auditEmpty").hidden = false;
    toast("操作记录加载失败", error.message, "error");
  }
}

async function refreshSummary() {
  state.summary = await apiGet("admin/summary");
  renderOverview();
  return state.summary;
}

async function switchView(view, updateHash = true) {
  if (!["overview", "catalog", "terminology", "groups", "trash", "audit"].includes(view)) view = "overview";
  state.view = view;
  all("[data-view-panel]").forEach((panel) => {
    const active = panel.dataset.viewPanel === view;
    panel.hidden = !active;
    panel.classList.toggle("is-active", active);
  });
  all("[data-view]").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.view === view);
    button.setAttribute("aria-current", button.dataset.view === view ? "page" : "false");
  });
  if (updateHash && window.location.hash !== `#${view}`) {
    history.replaceState(null, "", `#${view}`);
  }
  if (view === "catalog" && !state.loaded.has("catalog")) await loadEntries(1);
  if (view === "terminology" && !state.loaded.has("terminology")) await loadTerminology(1);
  if (view === "groups" && !state.loaded.has("groups")) await loadGroups(1);
  if (view === "trash" && !state.loaded.has("trash")) await loadTrash();
  if (view === "audit" && !state.loaded.has("audit")) await loadAudit();
  refreshIcons();
}

function handleEntryRowActivation(event) {
  const button = event.target.closest("[data-open-entry]");
  const row = event.target.closest("[data-entry-id]");
  const id = button?.dataset.openEntry || row?.dataset.entryId;
  if (id) openEntry(id);
}

function bindEvents() {
  all("[data-view]").forEach((button) => {
    button.addEventListener("click", () => switchView(button.dataset.view));
  });
  all("[data-view-link]").forEach((button) => {
    button.addEventListener("click", () => switchView(button.dataset.viewLink));
  });
  all("[data-theme-value]").forEach((button) => {
    button.addEventListener("click", async () => {
      const theme = button.dataset.themeValue;
      applyTheme(theme);
      try {
        await apiPost("admin/preferences", { theme });
      } catch (error) {
        toast("主题偏好保存失败", error.message, "error");
      }
    });
  });

  byId("quickAddButton").addEventListener("click", openAddDialog);
  byId("addEntryButton").addEventListener("click", openAddDialog);
  byId("entryRows").addEventListener("click", handleEntryRowActivation);
  byId("entryRows").addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") handleEntryRowActivation(event);
  });
  byId("terminologyRows").addEventListener("click", (event) => {
    const button = event.target.closest("[data-open-term]");
    const row = event.target.closest("[data-term-id]");
    const termId = button?.dataset.openTerm || row?.dataset.termId;
    if (termId) openTerminology(termId);
  });
  byId("terminologyRows").addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    const row = event.target.closest("[data-term-id]");
    if (row) openTerminology(row.dataset.termId);
  });
  const terminologySearch = debounce(() => {
    state.terminologyFilters.query = byId("terminologySearch").value;
    loadTerminology(1);
  });
  byId("terminologySearch").addEventListener("input", terminologySearch);
  for (const [id, key] of [
    ["terminologyCategoryFilter", "category"],
    ["terminologyOriginFilter", "origin"],
    ["terminologyStatusFilter", "status"],
    ["terminologySort", "sort"],
  ]) {
    byId(id).addEventListener("change", (event) => {
      state.terminologyFilters[key] = event.target.value;
      loadTerminology(1);
    });
  }
  byId("terminologyAddButton").addEventListener("click", openNewTerminology);
  byId("saveTerminologyButton").addEventListener("click", saveTerminology);
  byId("restoreTerminologyButton").addEventListener("click", restoreTerminology);
  byId("terminologyExportJsonButton").addEventListener("click", () => exportTerminology("json"));
  byId("terminologyExportCsvButton").addEventListener("click", () => exportTerminology("csv"));
  byId("terminologyImportButton").addEventListener("click", () => byId("terminologyImportInput").click());
  byId("terminologyImportInput").addEventListener("change", (event) => importTerminology(event.target.files?.[0]));
  byId("saveEntryButton").addEventListener("click", saveEntry);
  byId("deleteEntryButton").addEventListener("click", deleteActiveEntry);

  const entrySearch = debounce(() => {
    state.entryFilters.query = byId("entrySearch").value;
    loadEntries(1);
  });
  byId("entrySearch").addEventListener("input", entrySearch);
  for (const [id, key] of [
    ["entrySourceFilter", "source"],
    ["entryKindFilter", "kind"],
    ["entryStatusFilter", "status"],
    ["entrySort", "sort"],
  ]) {
    byId(id).addEventListener("change", (event) => {
      state.entryFilters[key] = event.target.value;
      loadEntries(1);
    });
  }

  const groupSearch = debounce(() => {
    state.groupQuery = byId("groupSearch").value;
    loadGroups(1);
  });
  byId("groupSearch").addEventListener("input", groupSearch);
  byId("groupList").addEventListener("click", (event) => {
    const button = event.target.closest("[data-group-id]");
    if (button) selectGroup(button.dataset.groupId);
  });
  const userSearch = debounce(() => {
    state.userQuery = byId("userSearch").value;
    loadUsers(1);
  });
  byId("userSearch").addEventListener("input", userSearch);
  byId("userRows").addEventListener("click", (event) => {
    const button = event.target.closest("[data-open-user]");
    const row = event.target.closest("[data-user-id]");
    const id = button?.dataset.openUser || row?.dataset.userId;
    if (id) openUser(id);
  });
  byId("userRows").addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    const row = event.target.closest("[data-user-id]");
    if (row) openUser(row.dataset.userId);
  });
  byId("saveUserButton").addEventListener("click", saveUser);
  byId("deleteUserButton").addEventListener("click", deleteActiveUser);
  byId("resetGroupDrawsButton").addEventListener("click", resetSelectedGroupDraws);

  byId("trashRows").addEventListener("click", (event) => {
    const button = event.target.closest("[data-restore-token]");
    if (button) restoreTrashEntry(button.dataset.restoreToken);
  });
  byId("refreshAuditButton").addEventListener("click", loadAudit);

  document.addEventListener("click", (event) => {
    const pageButton = event.target.closest("[data-page-scope]");
    if (pageButton && !pageButton.disabled) {
      const page = Number(pageButton.dataset.page || 1);
      const scope = pageButton.dataset.pageScope;
      if (scope === "entries") loadEntries(page);
      if (scope === "groups") loadGroups(page);
      if (scope === "users") loadUsers(page);
      if (scope === "terminology") loadTerminology(page);
    }
    if (!event.target.closest(".combo-wrap")) hideComboResults();
  });

  all("[data-close-drawer]").forEach((button) => button.addEventListener("click", closeDrawers));
  byId("drawerScrim").addEventListener("click", closeDrawers);
  all("[data-close-dialog]").forEach((button) => {
    button.addEventListener("click", () => byId(button.dataset.closeDialog).close());
  });
  byId("addEntryForm").addEventListener("submit", submitAddEntry);
  byId("addUploadZone").addEventListener("click", () => byId("addImageInput").click());
  byId("addUploadZone").addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") byId("addImageInput").click();
  });
  byId("addImageInput").addEventListener("change", (event) => stageAddImage(event.target.files?.[0]));
  for (const eventName of ["dragenter", "dragover"]) {
    byId("addUploadZone").addEventListener(eventName, (event) => {
      event.preventDefault();
      byId("addUploadZone").classList.add("is-dragging");
    });
  }
  for (const eventName of ["dragleave", "drop"]) {
    byId("addUploadZone").addEventListener(eventName, (event) => {
      event.preventDefault();
      byId("addUploadZone").classList.remove("is-dragging");
    });
  }
  byId("addUploadZone").addEventListener("drop", (event) => stageAddImage(event.dataTransfer?.files?.[0]));

  byId("confirmCancelButton").addEventListener("click", () => settleConfirm(false));
  byId("confirmAcceptButton").addEventListener("click", () => settleConfirm(true));
  byId("confirmDialog").addEventListener("cancel", (event) => {
    event.preventDefault();
    settleConfirm(false);
  });

  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && document.querySelector(".drawer.is-open")) closeDrawers();
  });
  window.addEventListener("hashchange", () => switchView(window.location.hash.slice(1), false));
  window.matchMedia?.("(prefers-color-scheme: dark)").addEventListener?.("change", () => {
    if (state.theme === "auto") {
      state.hostDark = detectHostDark(state.context);
      applyTheme("auto");
    }
  });
}

async function bootstrap() {
  bindEvents();
  refreshIcons();
  try {
    state.bridge = await waitForBridge();
    const context = await state.bridge.ready();
    applyContext(context);
    const onContextChange = state.bridge.onContextChange || state.bridge.onContext;
    onContextChange?.call(state.bridge, applyContext);
    await refreshSummary();
    applyTheme(state.summary.preferences?.theme || "auto");
    await switchView(window.location.hash.slice(1) || "overview", false);
    byId("app").setAttribute("aria-busy", "false");
    byId("initialLoader").classList.add("is-hidden");
  } catch (error) {
    byId("initialLoader").innerHTML = `
      <span class="loader-mark">${icon("circle-alert")}</span>
      <strong>管理台加载失败</strong>
      <span>${escapeHtml(error.message)}</span>
    `;
    refreshIcons();
  }
}

bootstrap();
