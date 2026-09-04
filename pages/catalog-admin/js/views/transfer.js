/**
 * Backup & migration view.
 *
 * Every import here is deliberately two-phase. An upload only ever lands in a
 * staging directory where it gets parsed and diffed against the live data, and
 * nothing is written until the operator confirms that diff. That is the only
 * way a 96 MB asset archive can be accepted without risking a half-applied
 * import when the parse fails halfway through.
 *
 * Exports go through apiDownload() rather than apiGet() because the backend
 * streams them in 256 KB chunks; buffering a 4 MB catalog dump into a JSON
 * envelope would double the memory cost for no benefit.
 */

import { h, qs, replaceChildren, setHidden, setText } from "../core/dom.js";
import { apiDownload, apiGet, apiPost, apiUpload } from "../core/bridge.js";
import { state, nextSequence, isCurrentSequence, invalidateView } from "../core/state.js";
import { clampNumber, formatBytes, formatNumber } from "../core/format.js";
import { icon } from "../core/icons.js";
import { refreshSummary } from "../core/summary.js";
import {
  badge,
  renderEmptyState,
  renderMetrics,
  renderSkeletonStack,
  setButtonBusy,
} from "../ui/widgets.js";
import { confirmAction } from "../ui/confirm.js";
import { toast, toastError } from "../ui/toast.js";

const FORMAT_LABELS = { json: "JSON", csv: "CSV", zip: "ZIP" };
const COUNT_UNITS = { bundle: " 个文件", assets: " 张图片" };
const SUMMARY_FIELDS = [
  ["added", "新增", "good"],
  ["updated", "更新", "info"],
  ["unchanged", "不变", ""],
  ["removed", "移除", "bad"],
  ["skipped", "跳过", "warn"],
];
const STATE_TONES = {
  added: "good",
  updated: "info",
  unchanged: "",
  removed: "bad",
  skipped: "warn",
};
const STATE_LABELS = {
  added: "新增",
  updated: "更新",
  unchanged: "不变",
  removed: "移除",
  skipped: "跳过",
};
const TEXT_DATASETS = ["catalog", "terminology", "wiki-index", "groups", "audit"];
const MAX_SAMPLES = 8;

/** Non-empty while an upload is in flight, so the queue can show a placeholder. */
let uploadingLabel = "";

function store() {
  return state.transfer;
}

function datasetByName(name) {
  return store().datasets.find((item) => item.name === name) || null;
}

function positive(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 0;
}

function scopeFor(dataset) {
  const scopes = dataset.scopes || [];
  const chosen = store().scopes[dataset.name];
  return scopes.indexOf(chosen) >= 0 ? chosen : scopes[0] || "merged";
}

function volumeFor(dataset) {
  const total = Math.max(1, positive(dataset.volumes));
  const chosen = Math.max(1, positive(store().volumes[dataset.name]) || 1);
  return Math.min(total, chosen);
}

function modeFor(record) {
  const modes = record.modes || [];
  const chosen = store().modes[record.token];
  return modes.indexOf(chosen) >= 0 ? chosen : modes[0] || "merge";
}

function modeLabelFor(record, mode) {
  const index = (record.modes || []).indexOf(mode);
  return (record.mode_labels || [])[index] || "合并导入";
}

function summaryFor(record, mode) {
  const summaries = record.summaries || {};
  return summaries[mode] || record.summary || {};
}

function countOf(summary, key) {
  return Math.max(0, positive(summary && summary[key]));
}

/** Staged uploads expire server-side, so the card shows a live countdown. */
function formatExpiry(seconds) {
  const total = Math.floor(positive(seconds));
  if (!total) return "已过期";
  if (total < 90) return total + " 秒后过期";
  const minutes = Math.round(total / 60);
  if (minutes < 60) return minutes + " 分钟后过期";
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? hours + " 小时 " + rest + " 分后过期" : hours + " 小时后过期";
}

function exportFilename(dataset, format, volume) {
  const parts = ["kirby", dataset.name];
  if (volume) parts.push("part" + volume);
  return parts.join("-") + "." + format;
}

function segment(label, options, active, onPick) {
  return h(
    "div",
    { class: "transfer-segment", attrs: { role: "group", "aria-label": label } },
    options.map((option) => {
      const on = option.value === active;
      return h(
        "button",
        {
          type: "button",
          class: on ? "transfer-chip is-active" : "transfer-chip",
          attrs: { "aria-pressed": on ? "true" : "false" },
          onclick: () => {
            if (!on) onPick(option.value);
          },
        },
        h("span", { text: option.label })
      );
    })
  );
}

function renderTransferMetrics() {
  const box = store();
  const limits = box.limits || {};
  const assets = box.assets || {};
  const pending = box.pending || [];

  let textCount = 0;
  let textBytes = 0;
  let largest = 0;
  TEXT_DATASETS.forEach((name) => {
    const dataset = datasetByName(name);
    if (!dataset) return;
    textCount += positive(dataset.count);
    textBytes += positive(dataset.bytes);
    largest = Math.max(largest, positive(dataset.bytes));
  });

  const textLimit = positive(limits.text_bytes) || 1;
  const assetBytes = positive(assets.bytes);
  const assetVolumes = Math.max(1, positive(assets.volumes) || 1);
  const volumeLimit =
    positive(limits.volume_bytes) || positive(assets.volume_bytes) || 1;
  const bundle = datasetByName("bundle");

  renderMetrics(qs("#transferMetrics"), [
    {
      label: "可导出条目",
      value: textCount,
      note: "图鉴、名称库、百科序号、群组与操作记录",
      glyph: "library-big",
      color: "--accent",
      /* 这里原本挂了一个三行 meter 拆分三个文本数据集的体积占比，但下方
         的数据列表已经逐行给出条目数和体积，重复信息把整排指标卡顶到
         187px 高，1366x768 上就有一条数据行被挤到折叠线以下。 */
    },
    {
      label: "文本备份体积",
      value: formatBytes(textBytes),
      note: "最大的单个文件 " + formatBytes(largest),
      glyph: "file-json",
      color: "--cyan",
      viz: {
        kind: "ruler",
        percent: (Math.min(largest, textLimit) / textLimit) * 100,
        min: "0",
        max: formatBytes(textLimit) + " 上限",
      },
    },
    {
      label: "素材图片",
      value: positive(assets.count),
      note: formatBytes(assetBytes) + " · 分 " + assetVolumes + " 卷下载",
      glyph: "image",
      color: "--purple",
      viz: {
        kind: "rate",
        /* 总体积除以分卷数才是有意义的填充率：直接拿总体积比单卷上限
           在素材超过一卷时恒为 100%，圆环就没有信息量了。 */
        percent: clampNumber(
          (assetBytes / assetVolumes / volumeLimit) * 100,
          0,
          100,
          0,
        ),
        title: "平均每卷 " + formatBytes(Math.round(assetBytes / assetVolumes)),
        caption:
          assetVolumes > 1
            ? "上限 " + formatBytes(volumeLimit) + " · 逐卷导入"
            : "一个压缩包就能装下",
      },
    },
    {
      label: "待确认导入",
      value: pending.length,
      note: pending.length
        ? "预检已完成，确认后才写入"
        : "配置整包含 " + formatNumber(positive(bundle && bundle.count)) + " 个文件",
      glyph: pending.length ? "scan-search" : "shield-check",
      color: pending.length ? "--yellow" : "--green",
    },
  ]);
}

/**
 * One row per dataset instead of one card.
 *
 * A 1366x900 laptop leaves roughly 340px of scroll height in this panel, and
 * seven cards never fit: the narrowest card that still held two export buttons
 * plus an import button needed 273px of width, which forced the action row to
 * wrap and pushed every card past 190px tall. A single control strip keeps all
 * seven datasets reachable without scrolling, so the hint is truncated rather
 * than wrapped and the full sentence moves into the row tooltip.
 */
function datasetRow(dataset) {
  const name = dataset.name;
  const ready = dataset.ready !== false;
  const formats = dataset.formats || [];
  const scopes = dataset.scopes || [];
  const scopeLabels = dataset.scope_labels || [];
  const volumes = Math.max(1, positive(dataset.volumes) || 1);
  const canImport = (dataset.modes || []).length > 0;

  const meta = [
    formatNumber(positive(dataset.count)) + (COUNT_UNITS[name] || " 条"),
    formatBytes(positive(dataset.bytes)),
  ];
  if (volumes > 1) meta.push(volumes + " 卷");

  const controls = [];
  if (scopes.length > 1) {
    controls.push(
      segment(
        dataset.label + "导出范围",
        scopes.map((value, index) => ({
          value: value,
          label: scopeLabels[index] || value,
        })),
        scopeFor(dataset),
        (value) => {
          store().scopes[name] = value;
          renderDatasets();
        }
      )
    );
  }
  if (volumes > 1) {
    const options = [];
    for (let index = 1; index <= volumes; index += 1) {
      options.push(
        h("option", {
          value: String(index),
          text: "第 " + index + " 卷",
          selected: index === volumeFor(dataset),
        })
      );
    }
    controls.push(
      h(
        "select",
        {
          class: "select transfer-volume",
          attrs: { "aria-label": dataset.label + "分卷" },
          onchange: (event) => {
            store().volumes[name] = Number(event.target.value) || 1;
          },
        },
        options
      )
    );
  }
  if (positive(dataset.overrides)) {
    controls.push(
      badge("自定义 " + formatNumber(positive(dataset.overrides)), "info", "pencil-line")
    );
  }
  if (!canImport) controls.push(badge("仅导出", "info", "download"));
  if (!ready) controls.push(badge(dataset.note || "暂时不可用", "warn", "circle-alert"));

  const exportPill = h(
    "div",
    {
      class: "transfer-pill",
      attrs: { role: "group", "aria-label": dataset.label + "导出格式" },
    },
    h("span", { class: "transfer-pill-label" }, icon("download")),
    formats.map((format) =>
      h(
        "button",
        {
          type: "button",
          class: "transfer-pill-chip",
          disabled: !ready,
          attrs: {
            title:
              (volumes > 1 ? "下载所选分卷 · " : "导出 ") +
              (FORMAT_LABELS[format] || format),
          },
          onclick: (event) => exportDataset(dataset, format, event.currentTarget),
        },
        h("span", { text: FORMAT_LABELS[format] || format })
      )
    )
  );

  return h(
    "article",
    {
      class: ready ? "transfer-row" : "transfer-row is-muted",
      attrs: dataset.hint ? { title: dataset.hint } : null,
    },
    h("span", { class: "transfer-row-icon" }, icon(dataset.icon || "database")),
    h(
      "div",
      { class: "transfer-row-copy" },
      h("strong", { text: dataset.label }),
      h("small", { text: meta.join(" · ") }),
      dataset.hint ? h("span", { class: "transfer-row-hint", text: dataset.hint }) : null
    ),
    controls.length ? h("div", { class: "transfer-row-controls" }, controls) : null,
    h(
      "div",
      { class: "transfer-row-actions" },
      exportPill,
      canImport
        ? h(
            "button",
            {
              type: "button",
              class: "transfer-row-import",
              attrs: { title: "从备份文件导入" + dataset.label },
              onclick: () => requestImport(dataset),
            },
            icon("upload"),
            h("span", { text: "导入" })
          )
        : null
    )
  );
}

function renderDatasets() {
  const container = qs("#transferDatasets");
  if (!container) return;
  const datasets = store().datasets || [];
  if (!datasets.length) {
    renderEmptyState(container, {
      glyph: "database",
      title: "没有可备份的数据",
      message: "先在素材库或名称库里添加内容，再回到这里导出。",
      tall: true,
    });
    return;
  }
  replaceChildren(container, datasets.map(datasetRow));
}

function summaryGrid(record, mode) {
  const summary = summaryFor(record, mode);
  const cells = [
    h(
      "div",
      { class: "transfer-summary-cell" },
      h("span", { text: "总计" }),
      h("strong", { text: formatNumber(countOf(summary, "total")) })
    ),
  ];
  SUMMARY_FIELDS.forEach(([key, label, tone]) => {
    const value = countOf(summary, key);
    if (!value) return;
    cells.push(
      h(
        "div",
        { class: tone ? "transfer-summary-cell is-" + tone : "transfer-summary-cell" },
        h("span", { text: label }),
        h("strong", { text: formatNumber(value) })
      )
    );
  });
  return h("div", { class: "transfer-summary" }, cells);
}

function sampleChips(record) {
  const samples = Array.isArray(record.samples) ? record.samples : [];
  if (!samples.length) return null;
  return h(
    "div",
    { class: "transfer-samples" },
    samples.slice(0, MAX_SAMPLES).map((sample) => {
      const label = String(sample.label || sample.id || "");
      const key = String(sample.state || "");
      return badge((STATE_LABELS[key] || key) + " · " + label, STATE_TONES[key] || "", "");
    })
  );
}

function noticeList(className, glyph, items) {
  const rows = Array.isArray(items) ? items.filter(Boolean) : [];
  if (!rows.length) return null;
  return h(
    "ul",
    { class: className },
    rows.map((line) => h("li", {}, icon(glyph), h("span", { text: String(line) })))
  );
}

function uploadingCard() {
  return h(
    "article",
    { class: "transfer-pending-card is-uploading" },
    h(
      "header",
      { class: "transfer-card-head" },
      h(
        "span",
        { class: "transfer-card-icon" },
        icon("loader-circle", { className: "icon is-spinning" })
      ),
      h(
        "div",
        { class: "transfer-card-copy" },
        h("strong", { text: "正在上传并预检" }),
        h("small", { text: uploadingLabel })
      )
    ),
    h("p", {
      class: "transfer-card-hint",
      text: "文件会先落到暂存目录再解析，这一步不会修改任何现有数据。",
    })
  );
}

function pendingCard(record) {
  const mode = modeFor(record);
  const modes = record.modes || [];
  const modeLabels = record.mode_labels || [];
  const meta = [record.filename || "", formatBytes(positive(record.size))].filter(Boolean);

  const controls = modes.length > 1
    ? segment(
        record.label + "导入方式",
        modes.map((value, index) => ({ value: value, label: modeLabels[index] || value })),
        mode,
        (value) => {
          store().modes[record.token] = value;
          renderPending();
        }
      )
    : badge(modeLabelFor(record, mode), "info", "arrow-right");

  return h(
    "article",
    {
      class:
        mode === "replace"
          ? "transfer-pending-card is-replace"
          : "transfer-pending-card",
    },
    h(
      "header",
      { class: "transfer-card-head" },
      h("span", { class: "transfer-card-icon" }, icon(record.icon || "database")),
      h(
        "div",
        { class: "transfer-card-copy" },
        h("strong", { text: record.label || record.dataset || "" }),
        h("small", { text: meta.join(" · ") })
      ),
      h("span", { class: "transfer-expiry", text: formatExpiry(record.expires_in) })
    ),
    summaryGrid(record, mode),
    h("div", { class: "transfer-card-controls" }, controls),
    sampleChips(record),
    noticeList("transfer-warnings", "triangle-alert", record.warnings),
    noticeList("transfer-notes", "info", record.notes),
    h(
      "footer",
      { class: "transfer-card-actions" },
      h(
        "div",
        { class: "transfer-export-group" },
        h(
          "button",
          {
            type: "button",
            class: "button danger-ghost small",
            onclick: (event) => discardPending(record, event.currentTarget),
          },
          icon("trash-2"),
          h("span", { text: "丢弃" })
        )
      ),
      h(
        "button",
        {
          type: "button",
          class: "button primary small",
          onclick: (event) => applyPending(record, event.currentTarget),
        },
        icon("database"),
        h("span", { text: "确认导入" })
      )
    )
  );
}

function renderPending() {
  const container = qs("#transferPending");
  const empty = qs("#transferPendingEmpty");
  if (!container) return;
  const pending = store().pending || [];
  const cards = [];
  if (uploadingLabel) cards.push(uploadingCard());
  pending.forEach((record) => cards.push(pendingCard(record)));
  if (!cards.length) {
    replaceChildren(container, []);
    /* 空容器仍然算一个 flex 项，会在滚动区里留下一段空 gap。 */
    setHidden(container, true);
    if (empty) {
      setHidden(empty, false);
      renderEmptyState(empty, {
        glyph: "shield-check",
        title: "没有等待确认的导入",
        message: "上传备份文件后，预检结果会先显示在这里。",
      });
    }
    return;
  }
  if (empty) {
    setHidden(empty, true);
    replaceChildren(empty, []);
  }
  setHidden(container, false);
  replaceChildren(container, cards);
}

function renderLimits() {
  const container = qs("#transferLimits");
  if (!container) return;
  const limits = store().limits || {};
  const ttlHours = Math.max(1, Math.round(positive(limits.stage_ttl) / 3600));
  replaceChildren(
    container,
    h("strong", {}, icon("lightbulb"), h("span", { text: "换服务器时的推荐顺序" })),
    h(
      "ol",
      {},
      h("li", { text: "旧机器：把「素材图片」的每一卷都下载下来，再导出「配置整包」。" }),
      h("li", { text: "新机器：先逐卷导入素材图片，再导入配置整包，这样图片和索引不会错位。" }),
      h("li", {
        text:
          "单个文本文件上限 " +
          formatBytes(positive(limits.text_bytes)) +
          "，压缩包上限 " +
          formatBytes(positive(limits.archive_bytes)) +
          "，素材每卷约 " +
          formatBytes(positive(limits.volume_bytes)) +
          "。",
      }),
      h("li", { text: "暂存的上传 " + ttlHours + " 小时后自动清理，确认或丢弃都不着急。" })
    )
  );
}

function renderStageLabel() {
  const pending = store().pending || [];
  const node = qs("#transferStageLabel");
  if (node) {
    const text = uploadingLabel
      ? "正在上传 " + uploadingLabel
      : pending.length
        ? pending.length + " 个导入待确认"
        : "暂存区空闲";
    setText(node, text);
    node.classList.toggle("is-active", Boolean(uploadingLabel) || pending.length > 0);
  }
  const nav = qs("#navBadgeTransfer");
  if (nav) {
    if (!pending.length) {
      setHidden(nav, true);
    } else {
      setText(nav, pending.length > 99 ? "99+" : String(pending.length));
      setHidden(nav, false);
    }
  }
}

function renderTransfer() {
  renderTransferMetrics();
  renderDatasets();
  renderPending();
  renderLimits();
  renderStageLabel();
}

async function exportDataset(dataset, format, button) {
  const params = { dataset: dataset.name, format: format };
  if ((dataset.scopes || []).length > 1) params.scope = scopeFor(dataset);
  const volumes = Math.max(1, positive(dataset.volumes) || 1);
  if (volumes > 1) params.volume = volumeFor(dataset);
  setButtonBusy(button, true, "打包中");
  try {
    await apiDownload(
      "admin/transfer/download",
      params,
      exportFilename(dataset, format, params.volume)
    );
    toast("已开始下载", dataset.label + " · " + (FORMAT_LABELS[format] || format), "success");
  } catch (error) {
    toastError("导出失败", error);
  } finally {
    setButtonBusy(button, false);
  }
}

function requestImport(dataset) {
  const input = qs("#transferImportInput");
  if (!input) return;
  store().importTarget = dataset.name;
  input.accept = (dataset.formats || []).map((format) => "." + format).join(",");
  input.value = "";
  input.click();
}

/**
 * Phase one of an import: upload, parse and diff on the server, then show the
 * result. Nothing is written to the live data by this call.
 */
async function stageImport(file) {
  const target = store().importTarget;
  store().importTarget = "";
  const dataset = target ? datasetByName(target) : null;
  if (!dataset) {
    toastError("导入失败", new Error("请重新点击对应数据的导入按钮"));
    return;
  }
  const formats = dataset.formats || [];
  const suffix = (file.name.split(".").pop() || "").toLowerCase();
  if (formats.indexOf(suffix) < 0) {
    toast(
      "文件类型不匹配",
      dataset.label + "只接受 " + formats.map((format) => "." + format).join(" / "),
      "warning"
    );
    return;
  }
  const limits = store().limits || {};
  const limit = positive(suffix === "zip" ? limits.archive_bytes : limits.text_bytes);
  if (limit && file.size > limit) {
    toast(
      "文件太大",
      "上限 " + formatBytes(limit) + "，当前 " + formatBytes(file.size),
      "warning"
    );
    return;
  }

  uploadingLabel = dataset.label + " · " + file.name;
  renderStageLabel();
  renderPending();
  let record = null;
  try {
    record = await apiUpload("admin/transfer/stage/" + dataset.name, file);
  } catch (error) {
    uploadingLabel = "";
    renderStageLabel();
    renderPending();
    toastError("预检失败", error);
    return;
  }
  uploadingLabel = "";
  const rest = (store().pending || []).filter((item) => item.token !== record.token);
  store().pending = [record].concat(rest);
  toast("已完成预检", "确认后才会写入" + dataset.label, "success");
  renderStageLabel();
  renderTransferMetrics();
  renderPending();
}

/** Phase two: the operator has seen the diff and accepts it. */
async function applyPending(record, button) {
  const mode = modeFor(record);
  const summary = summaryFor(record, mode);
  const replace = mode === "replace";
  const accepted = await confirmAction({
    title: replace ? "整体替换 " + record.label + "？" : "导入到 " + record.label + "？",
    message: replace
      ? "现有的自定义内容会被这个文件完全覆盖，不在文件里的条目将被移除。共 " +
        formatNumber(countOf(summary, "total")) +
        " 条，其中移除 " +
        formatNumber(countOf(summary, "removed")) +
        " 条。"
      : "将新增 " +
        formatNumber(countOf(summary, "added")) +
        " 条、更新 " +
        formatNumber(countOf(summary, "updated")) +
        " 条，其余保持不变。",
    acceptLabel: replace ? "替换" : "导入",
    tone: replace ? "danger" : "warning",
    glyph: "database",
  });
  if (!accepted) return;

  setButtonBusy(button, true, "导入中");
  try {
    const result = await apiPost("admin/transfer/apply", { token: record.token, mode: mode });
    delete store().modes[record.token];
    store().pending = Array.isArray(result && result.pending) ? result.pending : [];
    const applied = (result && result.summary) || {};
    toast(
      "导入完成",
      (result && result.label ? result.label : record.label) +
        " · 新增 " +
        formatNumber(countOf(applied, "added")) +
        " / 更新 " +
        formatNumber(countOf(applied, "updated")),
      "success"
    );
    (result && Array.isArray(result.warnings) ? result.warnings : [])
      .slice(0, 3)
      .forEach((line) => toast("导入提示", String(line), "warning"));
    invalidateView(
      "overview",
      "catalog",
      "terminology",
      "wiki-index",
      "groups",
      "trash",
      "audit"
    );
    await Promise.all([loadTransfer(), refreshSummary()]);
  } catch (error) {
    toastError("导入失败", error);
  } finally {
    setButtonBusy(button, false);
  }
}

async function discardPending(record, button) {
  const accepted = await confirmAction({
    title: "丢弃这次上传？",
    message: (record.filename || record.label) + " 会从暂存目录里删除，现有数据不受影响。",
    acceptLabel: "丢弃",
    tone: "danger",
    glyph: "trash-2",
  });
  if (!accepted) return;
  setButtonBusy(button, true, "清理中");
  try {
    const result = await apiPost("admin/transfer/discard", { token: record.token });
    delete store().modes[record.token];
    store().pending = Array.isArray(result && result.pending) ? result.pending : [];
    toast("已丢弃", "暂存文件已删除", "info");
    renderStageLabel();
    renderTransferMetrics();
    renderPending();
  } catch (error) {
    toastError("清理失败", error);
  } finally {
    setButtonBusy(button, false);
  }
}

export async function loadTransfer() {
  const token = nextSequence("transfer");
  const datasets = qs("#transferDatasets");
  setHidden(qs("#transferPendingEmpty"), true);
  if (!store().datasets.length && datasets) renderSkeletonStack(datasets, 6);
  try {
    const payload = await apiGet("admin/transfer/manifest");
    if (!isCurrentSequence("transfer", token)) return;
    const box = store();
    box.manifest = payload || null;
    box.datasets = Array.isArray(payload && payload.datasets) ? payload.datasets : [];
    box.pending = Array.isArray(payload && payload.pending) ? payload.pending : [];
    box.limits = (payload && payload.limits) || {};
    box.assets = (payload && payload.assets) || {};
    renderTransfer();
  } catch (error) {
    if (!isCurrentSequence("transfer", token)) return;
    if (datasets) {
      renderEmptyState(datasets, {
        glyph: "circle-alert",
        title: "无法读取备份清单",
        message: error && error.message ? error.message : "请稍后重试。",
        tall: true,
      });
    }
    toastError("读取备份清单失败", error);
  }
}

export function initTransfer() {
  const refresh = qs("#refreshTransferButton");
  if (refresh) {
    refresh.addEventListener("click", async () => {
      setButtonBusy(refresh, true, "刷新中");
      try {
        await loadTransfer();
      } finally {
        setButtonBusy(refresh, false);
      }
    });
  }
  const input = qs("#transferImportInput");
  if (input) {
    input.addEventListener("change", () => {
      const file = input.files && input.files[0];
      input.value = "";
      if (file) stageImport(file);
    });
  }
}
