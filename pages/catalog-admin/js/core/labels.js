/**
 * Every human-readable enum label lives here.
 *
 * Centralising them keeps the views free of magic strings and leaves a single
 * place to hook the host bridge t() helper if this page ever ships en-US copy.
 */

export const actionLabels = Object.freeze({
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
  "wiki-index.update": "更新百科序号",
  "wiki-index.restore": "恢复百科内置序号",
});

export const kindLabels = Object.freeze({
  base: "基础角色",
  ability: "能力形态",
  evolution: "进化形态",
  special_form: "特殊形态",
  transformed: "变身形态",
  variant: "角色变体",
  phase: "阶段形态",
  manual: "手动新增",
  legacy: "历史条目",
});

export const terminologyCategoryLabels = Object.freeze({
  character: "角色",
  form: "形态",
  ability: "能力",
  work: "作品",
  location: "地点",
  mechanic: "机制",
  mode: "模式",
  title: "称号",
  special: "专有名词",
});

export const terminologyOriginLabels = Object.freeze({
  bundled: "内置",
  override: "已覆盖",
  custom: "自定义",
});

export const terminologyStatusLabels = Object.freeze({
  official: "官方译名",
  official_reused: "沿用官译",
  project: "项目自译",
  transliterated: "音译",
  unchanged: "原文保留",
  unknown: "未标注",
});

const FALLBACK_WIKI_SITE_LABELS = Object.freeze({
  wikirby: "WiKirby",
  fandom: "Kirby Fandom",
  shinkaku: "真格攻略 Wiki",
});

/** Human label for an audit action id. */
export function actionLabel(action) {
  const key = String(action || "");
  return actionLabels[key] || key || "管理操作";
}

/** Glyph name for an audit action id. */
export function actionIcon(action) {
  const key = String(action || "");
  if (key.includes("delete")) {
    return "trash-2";
  }
  if (key.includes("restore")) {
    return "archive-restore";
  }
  if (key.includes("image")) {
    return "image";
  }
  if (key.includes("unlock")) {
    return "badge-check";
  }
  if (key.includes("group")) {
    return "users-round";
  }
  if (key.includes("add")) {
    return "plus";
  }
  return "pencil";
}

/** Catalog kind label with graceful passthrough. */
export function kindLabel(kind) {
  const key = String(kind || "");
  return kindLabels[key] || key || "未分类";
}

export function terminologyCategoryLabel(category) {
  const key = String(category || "");
  return terminologyCategoryLabels[key] || key || "未分类";
}

export function terminologyOriginLabel(origin) {
  const key = String(origin || "");
  return terminologyOriginLabels[key] || key || "内置";
}

export function terminologyStatusLabel(status) {
  const key = String(status || "");
  return terminologyStatusLabels[key] || key || "未标注";
}

/**
 * Site labels come from the backend so newly bundled wikis show up without a
 * frontend release; the hardcoded table is only a boot-time fallback.
 *
 * @param {string} site
 * @param {Array<{value: string, label: string}>} [sites]
 */
export function wikiSiteLabel(site, sites) {
  const key = String(site || "");
  if (Array.isArray(sites)) {
    const match = sites.find((item) => item && item.value === key);
    if (match && match.label) {
      return match.label;
    }
  }
  return FALLBACK_WIKI_SITE_LABELS[key] || key || "未知百科";
}

/** Glyph for a wiki site card, cycling through a small themed set. */
export function wikiSiteIcon(site) {
  const key = String(site || "");
  if (key === "wikirby") {
    return "book-open";
  }
  if (key === "fandom") {
    return "globe";
  }
  if (key === "shinkaku") {
    return "swords";
  }
  return "library-big";
}

/** Accent variable for a wiki site card. */
export function wikiSiteColor(site, index) {
  const palette = ["--cyan", "--green", "--yellow", "--purple", "--blue"];
  const key = String(site || "");
  if (key === "wikirby") {
    return "--cyan";
  }
  if (key === "fandom") {
    return "--green";
  }
  if (key === "shinkaku") {
    return "--yellow";
  }
  return palette[index % palette.length];
}
