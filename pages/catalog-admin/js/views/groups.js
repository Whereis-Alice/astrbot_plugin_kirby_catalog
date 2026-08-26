/**
 * Group records view: group index on the left, member table on the right, and
 * the member drawer that edits unlocks / counters.
 *
 * The current-ally selection is tri-state (state.userCurrentSelection) so that
 * merely typing in the search box can never clear a stored ally.
 */

import { h, qs, clear, setHidden, replaceChildren, setText, delegate } from "../core/dom.js";
import { apiGet, apiPost } from "../core/bridge.js";
import { state, nextSequence, isCurrentSequence, invalidateView } from "../core/state.js";
import { debounce, formatNumber, formatPercent, clampNumber } from "../core/format.js";
import { refreshSummary, summaryToday } from "../core/summary.js";
import { icon } from "../core/icons.js";
import {
  badge,
  badgeRow,
  formSection,
  miniProgress,
  primaryCell,
  renderEmptyState,
  renderSkeletonRows,
  renderSkeletonStack,
  rowAction,
  setButtonBusy,
  thumbFrame,
} from "../ui/widgets.js";
import { renderPagination } from "../ui/pagination.js";
import { openDrawer, closeDrawer, onDrawerClose } from "../ui/drawer.js";
import { confirmAction } from "../ui/confirm.js";
import { toast, toastError } from "../ui/toast.js";
import { createEntryCombo } from "../ui/combo.js";

const GROUP_PAGE_SIZE = 20;
const USER_PAGE_SIZE = 20;
/** Unlock rows rendered before the "show all" button appears. */
const UNLOCK_PREVIEW = 60;

/** Live references into the member drawer, refreshed on every drawer render. */
let currentAllyNode = null;
let unlockListNode = null;
let unlockCountNode = null;
let unlockMoreNode = null;
/** Entry payload shown in the current-ally block (display only). */
let currentAllyDisplay = null;
let unlockLimit = UNLOCK_PREVIEW;
/** Combo controllers, closed and reset when the drawer is re-rendered. */
let currentCombo = null;
let unlockCombo = null;

/* ==========================================================================
   Group index
   ========================================================================== */

function groupItem(group) {
  const id = String(group.group_id || "");
  const node = h(
    "button",
    {
      type: "button",
      class: id === String(state.selectedGroupId || "") ? "group-item is-active" : "group-item",
      dataset: { groupId: id },
      "aria-pressed": id === String(state.selectedGroupId || "") ? "true" : "false",
    },
    h("span", { class: "group-avatar", text: id.slice(-4) || "--" }),
    h(
      "span",
      { class: "group-main" },
      h("strong", { text: id || "未知群" }),
      h("span", {
        text:
          formatNumber(group.users) +
          " 位成员 · " +
          formatPercent(group.completion) +
          " 收集",
      })
    ),
    h(
      "span",
      { class: "group-stat" },
      h("strong", { text: formatNumber(group.unique_unlocks) }),
      h("span", { text: "unique" })
    )
  );
  return node;
}

function renderGroups() {
  const container = qs("#groupList");
  const items = state.groups.items;

  setText(qs("#groupTotalLabel"), formatNumber(state.groups.total) + " 个群组");

  if (!items.length) {
    renderEmptyState(container, {
      glyph: "messages-square",
      title: "还没有群数据",
      message: "群成员抽取后会自动出现",
    });
  } else {
    replaceChildren(container, items.map(groupItem));
  }

  renderPagination(
    qs("#groupPagination"),
    { page: state.groups.page, pages: state.groups.pages, total: state.groups.total },
    (page) => {
      state.groups.page = page;
      loadGroups().catch((error) => console.error(error));
    },
    { compact: true, unit: "个群" }
  );
}

/** Fetches one page of groups and keeps the selection in sync. */
export async function loadGroups() {
  const container = qs("#groupList");
  const token = nextSequence("groups");
  if (!state.groups.items.length) {
    replaceChildren(
      container,
      h("div", { class: "skeleton-stack", "aria-hidden": "true" },
        [0, 1, 2, 3, 4].map(() => h("span", { class: "skeleton skeleton-line" })))
    );
  }

  let payload = null;
  try {
    payload = await apiGet("admin/groups", {
      query: state.groups.filters.query,
      page: state.groups.page,
      page_size: GROUP_PAGE_SIZE,
    });
  } catch (error) {
    if (!isCurrentSequence("groups", token)) {
      return;
    }
    renderEmptyState(container, {
      glyph: "circle-alert",
      title: "无法读取群数据",
      message: error && error.message ? error.message : "请稍后重试。",
    });
    toastError("读取群数据失败", error);
    throw error;
  }
  if (!isCurrentSequence("groups", token)) {
    return;
  }

  const items = Array.isArray(payload && payload.items) ? payload.items : [];
  const total = Number(payload && payload.total) || 0;
  const pages = Math.max(1, Number(payload && payload.pages) || 1);

  if (total > 0 && !items.length && state.groups.page > pages) {
    state.groups.page = pages;
    return loadGroups();
  }

  state.groups.items = items;
  state.groups.total = total;
  state.groups.pages = pages;
  state.groups.page = Math.min(pages, Math.max(1, Number(payload && payload.page) || 1));

  // Drop a selection that vanished, then fall back to the first visible group.
  const selected = String(state.selectedGroupId || "");
  const stillVisible = items.some((item) => String(item.group_id) === selected);
  if (!items.length) {
    state.selectedGroupId = null;
  } else if (!selected || !stillVisible) {
    state.selectedGroupId = String(items[0].group_id);
  }

  renderGroups();

  if (!state.selectedGroupId) {
    renderSelectionEmpty();
    return;
  }
  const active = items.find((item) => String(item.group_id) === String(state.selectedGroupId));
  renderSelectionHead(active || null);
  await loadUsers().catch((error) => console.error(error));
}

function renderSelectionEmpty() {
  setHidden(qs("#groupSelectionContent"), true);
  renderEmptyState(qs("#groupSelectionEmpty"), {
    glyph: "users-round",
    title: "选择一个群组",
    message: "成员图鉴数据将在此处显示",
    tall: true,
  });
}

/** Shows the right-hand panel header for the selected group. */
function renderSelectionHead(group) {
  const empty = qs("#groupSelectionEmpty");
  clear(empty);
  setHidden(qs("#groupSelectionContent"), false);
  setText(qs("#selectedGroupId"), state.selectedGroupId || "--");
  if (!group) {
    setText(qs("#selectedGroupSummary"), "--");
    return;
  }
  setText(
    qs("#selectedGroupSummary"),
    formatNumber(group.users) +
      " 位成员 · " +
      formatNumber(group.unlock_records) +
      " 条解锁 · 今日 " +
      formatNumber(group.draws_today) +
      " 抽"
  );
}

/** Switches the selected group and reloads the member table. */
function selectGroup(groupId) {
  const id = String(groupId || "");
  if (!id || id === String(state.selectedGroupId || "")) {
    return;
  }
  state.selectedGroupId = id;
  state.users.page = 1;
  state.users.items = [];
  renderGroups();
  const group = state.groups.items.find((item) => String(item.group_id) === id);
  renderSelectionHead(group || null);
  loadUsers().catch((error) => console.error(error));
}

/* ==========================================================================
   Member table
   ========================================================================== */

/** Normalises a backend date into the value an input[type=date] accepts. */
function isoDate(value) {
  const text = typeof value === "string" ? value.trim() : "";
  if (text.length >= 10 && text[4] === "-" && text[7] === "-") {
    return text.slice(0, 10);
  }
  return "";
}

function userRow(user) {
  const current = user.current;
  const draws = Math.max(0, Number(user.draw_count) || 0);
  const bonus = Math.max(0, Number(user.draw_bonus) || 0);
  const nickname = user.nickname || "未命名";
  const stamp = isoDate(user.current_date);

  const row = h(
    "tr",
    {
      class: "is-clickable",
      role: "button",
      tabindex: "0",
      "aria-label": "编辑 " + nickname + " 的图鉴记录",
    },
    h("td", null, primaryCell(nickname, user.user_id)),
    h(
      "td",
      null,
      miniProgress(
        user.completion,
        formatNumber(user.unlocked) + " / " + formatNumber(user.total)
      )
    ),
    h(
      "td",
      null,
      current
        ? h(
            "div",
            { class: "ally-cell" },
            thumbFrame(current, { small: true }),
            h(
              "div",
              { class: "primary-cell" },
              h("strong", { text: current.name || "未命名" }),
              h("span", { text: "#" + current.id + (stamp ? " · " + stamp : "") })
            )
          )
        : h("span", { class: "cell-muted", text: "尚未抽取" })
    ),
    h(
      "td",
      null,
      h(
        "div",
        { class: "count-pair" },
        badge(formatNumber(draws) + " 抽", draws ? "info" : "", "dices"),
        bonus ? badge("额外 " + formatNumber(bonus), "good", "sparkle") : null,
        Number(user.no_new_count) > 0
          ? badge("连续未出新 " + formatNumber(user.no_new_count), "warn", "equal-not")
          : null
      )
    )
  );

  const open = () => openUserDrawer(user.group_id, user.user_id);
  row.addEventListener("click", open);
  row.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      open();
    }
  });
  return row;
}

function renderUsers() {
  const tbody = qs("#userRows");
  const empty = qs("#userEmpty");
  const items = state.users.items;

  clear(tbody);
  if (!items.length) {
    setHidden(empty, false);
    renderEmptyState(empty, {
      glyph: "user-round-search",
      title: "没有匹配的成员",
      message: state.users.filters.query ? "换个用户 ID 或昵称试试" : "该群还没有成员记录",
      tall: true,
    });
  } else {
    setHidden(empty, true);
    clear(empty);
    for (const user of items) {
      tbody.appendChild(userRow(user));
    }
  }

  renderPagination(
    qs("#userPagination"),
    { page: state.users.page, pages: state.users.pages, total: state.users.total },
    (page) => {
      state.users.page = page;
      loadUsers().catch((error) => console.error(error));
    },
    { unit: "位成员" }
  );
}

/** Fetches one page of members for the selected group. */
export async function loadUsers() {
  const groupId = state.selectedGroupId;
  if (!groupId) {
    renderSelectionEmpty();
    return;
  }
  const tbody = qs("#userRows");
  const empty = qs("#userEmpty");
  const token = nextSequence("users");

  setHidden(empty, true);
  renderSkeletonRows(tbody, 6, 4);

  let payload = null;
  try {
    payload = await apiGet("admin/groups/users", {
      group_id: groupId,
      query: state.users.filters.query,
      page: state.users.page,
      page_size: USER_PAGE_SIZE,
    });
  } catch (error) {
    if (!isCurrentSequence("users", token)) {
      return;
    }
    clear(tbody);
    setHidden(empty, false);
    renderEmptyState(empty, {
      glyph: "circle-alert",
      title: "无法读取成员数据",
      message: error && error.message ? error.message : "请稍后重试。",
      tall: true,
    });
    toastError("读取成员数据失败", error);
    throw error;
  }
  if (!isCurrentSequence("users", token)) {
    return;
  }

  const items = Array.isArray(payload && payload.items) ? payload.items : [];
  const total = Number(payload && payload.total) || 0;
  const pages = Math.max(1, Number(payload && payload.pages) || 1);

  // The requested page can disappear when the last member on it is deleted.
  if (total > 0 && !items.length && state.users.page > pages) {
    state.users.page = pages;
    return loadUsers();
  }

  state.users.items = items;
  state.users.total = total;
  state.users.pages = pages;
  state.users.page = Math.min(pages, Math.max(1, Number(payload && payload.page) || 1));
  if (payload && payload.group) {
    state.users.group = payload.group;
    renderSelectionHead(payload.group);
  }
  renderUsers();
}

/* ==========================================================================
   Member drawer
   ========================================================================== */

function numberField(id, label, value, hint) {
  const input = h("input", {
    type: "number",
    class: "input",
    id,
    min: "0",
    step: "1",
    inputmode: "numeric",
    value: String(Math.max(0, Number(value) || 0)),
  });
  return h(
    "label",
    { class: "field" },
    h("span", { text: label }),
    input,
    hint ? h("small", { class: "field-hint", text: hint }) : null
  );
}

/** Snapshot of the editable drawer fields so a re-render can restore them. */
function captureUserDraft() {
  const nickname = qs("#userNicknameInput");
  if (!nickname) {
    return null;
  }
  const read = (selector) => {
    const node = qs(selector);
    return node ? node.value : "";
  };
  return {
    nickname: nickname.value,
    noNew: read("#userNoNewInput"),
    drawCount: read("#userDrawCountInput"),
    drawBonus: read("#userDrawBonusInput"),
    currentDate: read("#userCurrentDateInput"),
    unlockDate: read("#unlockDateInput"),
  };
}

/** The current-ally card. Reflects the pending tri-state selection. */
function allyBlock() {
  const cleared = state.userCurrentSelection === null;
  const entry = cleared ? null : currentAllyDisplay;
  const pending = state.userCurrentSelection !== undefined;

  if (!entry) {
    return h(
      "div",
      { class: "current-ally-block" },
      h("span", { class: "thumb-frame" }, icon("image-off")),
      h(
        "div",
        { class: "current-ally-copy" },
        h("strong", { text: cleared ? "保存后将清空当前盟友" : "尚未设置当前盟友" }),
        h("span", {
          text: cleared ? "该成员下次抽取时会重新记录" : "在下方搜索图鉴即可指定",
        }),
        pending ? badgeRow(badge("待保存", "warn", "clock")) : null
      )
    );
  }

  return h(
    "div",
    { class: "current-ally-block" },
    thumbFrame(entry),
    h(
      "div",
      { class: "current-ally-copy" },
      h("strong", { text: entry.name || "未命名" }),
      h("span", { text: "#" + entry.id + (entry.source ? " · " + entry.source : "") }),
      pending ? badgeRow(badge("待保存", "warn", "clock")) : null
    )
  );
}

function refreshAllyBlock() {
  if (!currentAllyNode || !currentAllyNode.parentNode) {
    return;
  }
  const next = allyBlock();
  currentAllyNode.parentNode.replaceChild(next, currentAllyNode);
  currentAllyNode = next;
}

function unlockItem(record) {
  return h(
    "div",
    { class: record.missing ? "unlock-item missing" : "unlock-item" },
    thumbFrame(record),
    h(
      "div",
      { class: "unlock-copy" },
      h("strong", { text: record.name || "未命名" }),
      h("span", { text: "#" + record.id + (record.source ? " · " + record.source : "") })
    ),
    h("span", { class: "unlock-date", text: isoDate(record.unlock_date) || "--" }),
    rowAction("circle-slash", "移除该解锁记录", () => changeUnlock(record.id, "remove"))
  );
}

/** Re-renders only the unlock block, keeping the form fields untouched. */
function renderUnlocks() {
  const user = state.activeUser;
  const unlocks = user && Array.isArray(user.unlocks) ? user.unlocks : [];

  if (unlockCountNode) {
    replaceChildren(
      unlockCountNode,
      icon("library-big"),
      h("span", {
        text:
          formatNumber(unlocks.length) +
          " / " +
          formatNumber(user ? user.total : 0) +
          " · " +
          formatPercent(user ? user.completion : 0),
      })
    );
  }

  if (unlockListNode) {
    if (!unlocks.length) {
      renderEmptyState(unlockListNode, {
        glyph: "egg",
        title: "尚未解锁图鉴",
        message: "用上方搜索框补录解锁记录",
      });
    } else {
      // Bounded render: very large collections stay responsive.
      replaceChildren(unlockListNode, unlocks.slice(0, unlockLimit).map(unlockItem));
    }
  }

  if (unlockMoreNode) {
    clear(unlockMoreNode);
    const remaining = unlocks.length - unlockLimit;
    if (remaining > 0) {
      const expand = h(
        "button",
        { type: "button", class: "text-button" },
        h("span", { text: "展开其余 " + formatNumber(remaining) + " 条" }),
        icon("chevron-down")
      );
      expand.addEventListener("click", () => {
        unlockLimit = unlocks.length;
        renderUnlocks();
      });
      unlockMoreNode.appendChild(expand);
    } else if (unlockLimit > UNLOCK_PREVIEW && unlocks.length > UNLOCK_PREVIEW) {
      const collapse = h(
        "button",
        { type: "button", class: "text-button" },
        h("span", { text: "只看前 " + formatNumber(UNLOCK_PREVIEW) + " 条" }),
        icon("minus")
      );
      collapse.addEventListener("click", () => {
        unlockLimit = UNLOCK_PREVIEW;
        renderUnlocks();
      });
      unlockMoreNode.appendChild(collapse);
    }
  }
}

function renderUserDrawer(user, draft) {
  const body = qs("#userDrawerBody");
  if (!body) {
    return;
  }
  setText(qs("#userDrawerTitle"), (user.nickname || "未命名") + " · " + user.user_id);

  if (currentCombo) {
    currentCombo.close();
    currentCombo = null;
  }
  if (unlockCombo) {
    unlockCombo.close();
    unlockCombo = null;
  }
  if (state.userCurrentSelection === undefined) {
    currentAllyDisplay = user.current || null;
  }

  const nicknameInput = h("input", {
    type: "text",
    class: "input",
    id: "userNicknameInput",
    maxlength: "160",
    required: true,
    autocomplete: "off",
    value: draft ? draft.nickname : user.nickname || "",
  });
  const currentDateInput = h("input", {
    type: "date",
    class: "input",
    id: "userCurrentDateInput",
    value: draft ? draft.currentDate : isoDate(user.current_date),
  });

  currentAllyNode = allyBlock();

  const currentSearch = h("input", {
    type: "search",
    class: "input",
    id: "currentEntrySearch",
    placeholder: "搜索图鉴以设为当前盟友",
    autocomplete: "off",
  });
  const currentResults = h("div", {
    class: "combo-results",
    id: "currentEntryResults",
    hidden: true,
  });
  const clearCurrentButton = h(
    "button",
    {
      type: "button",
      class: "icon-button",
      id: "clearCurrentEntryButton",
      "aria-label": "清空当前盟友",
      title: "清空当前盟友",
    },
    icon("circle-slash")
  );
  clearCurrentButton.addEventListener("click", () => {
    state.userCurrentSelection = null;
    currentAllyDisplay = null;
    refreshAllyBlock();
    if (currentCombo) {
      currentCombo.reset();
    }
  });

  const unlockSearch = h("input", {
    type: "search",
    class: "input",
    id: "unlockEntrySearch",
    placeholder: "搜索图鉴以补录解锁",
    autocomplete: "off",
  });
  const unlockResults = h("div", {
    class: "combo-results",
    id: "unlockEntryResults",
    hidden: true,
  });
  const unlockDateInput = h("input", {
    type: "date",
    class: "input",
    id: "unlockDateInput",
    value: draft && draft.unlockDate ? draft.unlockDate : summaryToday(),
  });

  unlockCountNode = h("div", { class: "unlock-count" });
  unlockListNode = h("div", { class: "unlock-list" });
  unlockMoreNode = h("div", { class: "unlock-more" });

  replaceChildren(
    body,
    formSection(
      "成员资料",
      "昵称会出现在抽取消息里",
      h(
        "div",
        { class: "form-grid two-columns" },
        h("label", { class: "field" }, h("span", { text: "昵称" }), nicknameInput),
        numberField(
          "userNoNewInput",
          "连续未出新",
          draft ? draft.noNew : user.no_new_count
        ),
        numberField(
          "userDrawCountInput",
          "今日抽取次数",
          draft ? draft.drawCount : user.draw_count
        ),
        numberField(
          "userDrawBonusInput",
          "今日额外次数",
          draft ? draft.drawBonus : user.draw_bonus
        ),
        h("label", { class: "field" }, h("span", { text: "当前盟友日期" }), currentDateInput)
      ),
      h("p", {
        class: "form-note",
        text: "次数与日期仅用于修正统计；日期留空表示不记录。",
      })
    ),
    formSection(
      "当前盟友",
      "抽取消息中展示的角色",
      currentAllyNode,
      h(
        "div",
        { class: "combo-wrap" },
        h("div", { class: "combo-input-row" }, currentSearch, clearCurrentButton),
        currentResults
      ),
      h("p", {
        class: "form-note",
        text: "只在这里改动过时才会写入，否则保存不会碰原有记录。",
      })
    ),
    formSection(
      "解锁记录",
      formatNumber(user.unlock_records) + " 条记录",
      h(
        "div",
        { class: "form-grid two-columns" },
        h(
          "div",
          { class: "field" },
          h("span", { text: "补录解锁" }),
          h("div", { class: "combo-wrap" }, unlockSearch, unlockResults)
        ),
        h("label", { class: "field" }, h("span", { text: "解锁日期" }), unlockDateInput)
      ),
      unlockCountNode,
      unlockListNode,
      unlockMoreNode
    )
  );

  currentCombo = createEntryCombo({
    input: currentSearch,
    results: currentResults,
    onSelect: (entry) => {
      state.userCurrentSelection = String(entry.id);
      currentAllyDisplay = entry;
      refreshAllyBlock();
      if (currentCombo) {
        currentCombo.reset();
      }
    },
  });
  unlockCombo = createEntryCombo({
    input: unlockSearch,
    results: unlockResults,
    onSelect: (entry) => {
      if (unlockCombo) {
        unlockCombo.reset();
      }
      changeUnlock(entry.id, "add");
    },
  });

  renderUnlocks();
}

/** Loads a member detail payload and opens the drawer. */
export async function openUserDrawer(groupId, userId) {
  state.userCurrentSelection = undefined;
  currentAllyDisplay = null;
  unlockLimit = UNLOCK_PREVIEW;
  renderSkeletonStack(qs("#userDrawerBody"), 8);
  openDrawer("userDrawer");
  try {
    const user = await apiGet("admin/groups/user", {
      group_id: groupId,
      user_id: userId,
    });
    state.activeUser = user;
    renderUserDrawer(user);
  } catch (error) {
    toastError("读取成员数据失败", error);
    closeDrawer();
  }
}


/* ==========================================================================
   Write operations
   ========================================================================== */

/** Persists the drawer fields. current_id is only sent when actually touched. */
async function saveUser() {
  const user = state.activeUser;
  if (!user) {
    return;
  }
  const nicknameInput = qs("#userNicknameInput");
  const nickname = nicknameInput ? nicknameInput.value.trim() : "";
  if (!nickname) {
    toast("请填写昵称", "昵称不能为空", "warning");
    if (nicknameInput) {
      nicknameInput.focus();
    }
    return;
  }

  const read = (selector) => {
    const node = qs(selector);
    return node ? node.value : "";
  };
  const payload = {
    group_id: user.group_id,
    user_id: user.user_id,
    nickname: nickname,
    no_new_count: clampNumber(read("#userNoNewInput"), 0, 1000000, 0),
    draw_count: clampNumber(read("#userDrawCountInput"), 0, 1000000, 0),
    draw_bonus: clampNumber(read("#userDrawBonusInput"), 0, 1000000, 0),
    current_date: read("#userCurrentDateInput"),
  };
  if (state.userCurrentSelection !== undefined) {
    payload.current_id =
      state.userCurrentSelection === null ? "" : state.userCurrentSelection;
  }

  const button = qs("#saveUserButton");
  setButtonBusy(button, true, "保存中");
  let updated = null;
  try {
    updated = await apiPost("admin/groups/user/save", payload);
  } catch (error) {
    toastError("保存成员数据失败", error);
    return;
  } finally {
    setButtonBusy(button, false);
  }

  toast("已保存", nickname + " 的数据已更新", "success");
  state.activeUser = updated;
  state.userCurrentSelection = undefined;
  currentAllyDisplay = (updated && updated.current) || null;
  renderUserDrawer(updated);
  invalidateView("audit");
  await Promise.all([
    loadUsers().catch((error) => toastError("刷新成员列表失败", error)),
    refreshSummary().catch(() => {}),
  ]);
}

/** Adds or removes one unlock record for the open member. */
async function changeUnlock(entryId, action) {
  const user = state.activeUser;
  if (!user) {
    return;
  }
  const draft = captureUserDraft();
  const token = nextSequence("unlock");
  const payload = {
    group_id: user.group_id,
    user_id: user.user_id,
    entry_id: entryId,
    action: action,
  };
  if (action === "add") {
    payload.unlock_date = (draft && draft.unlockDate) || summaryToday();
  }

  let updated = null;
  try {
    updated = await apiPost("admin/groups/user/unlock", payload);
  } catch (error) {
    toastError(action === "add" ? "补录解锁失败" : "移除解锁失败", error);
    return;
  }
  if (!isCurrentSequence("unlock", token)) {
    return;
  }

  toast(
    action === "add" ? "已补录解锁" : "已移除解锁",
    "#" + entryId + (action === "add" ? " 已加入该成员图鉴" : " 已从该成员图鉴移除"),
    "success"
  );
  state.activeUser = updated;
  renderUserDrawer(updated, draft);
  invalidateView("audit");
  await Promise.all([
    loadUsers().catch((error) => toastError("刷新成员列表失败", error)),
    refreshSummary().catch(() => {}),
  ]);
}

/** Wipes every catalog record this member has in the selected group. */
async function deleteUser() {
  const user = state.activeUser;
  if (!user) {
    return;
  }
  const label = user.nickname || "未命名";
  const accepted = await confirmAction({
    title: "删除该成员的图鉴数据？",
    message:
      label +
      "（" +
      user.user_id +
      "）在本群的 " +
      formatNumber(user.unlock_records) +
      " 条解锁记录会被永久删除，此操作无法撤销。",
    acceptLabel: "永久删除",
    glyph: "user-round-x",
  });
  if (!accepted) {
    return;
  }

  const button = qs("#deleteUserButton");
  setButtonBusy(button, true, "删除中");
  try {
    await apiPost("admin/groups/user/delete", {
      group_id: user.group_id,
      user_id: user.user_id,
    });
  } catch (error) {
    toastError("删除成员数据失败", error);
    return;
  } finally {
    setButtonBusy(button, false);
  }

  toast("已删除", label + " 在本群的数据已清空", "success");
  state.activeUser = null;
  closeDrawer();
  invalidateView("audit");
  await Promise.all([
    loadUsers().catch(() => {}),
    loadGroups().catch(() => {}),
    refreshSummary().catch(() => {}),
  ]);
}

/** Clears today draw counters for every member of the selected group. */
async function resetGroupDraws() {
  const groupId = state.selectedGroupId;
  if (!groupId) {
    return;
  }
  const accepted = await confirmAction({
    title: "重置今日抽取次数？",
    message:
      "群 " +
      groupId +
      " 内所有成员的今日抽取次数与额外次数会被清零，已解锁的图鉴不受影响。",
    acceptLabel: "重置次数",
    tone: "warning",
    glyph: "rotate-ccw",
  });
  if (!accepted) {
    return;
  }

  const button = qs("#resetGroupDrawsButton");
  setButtonBusy(button, true, "重置中");
  let result = null;
  try {
    result = await apiPost("admin/groups/reset-draws", { group_id: groupId });
  } catch (error) {
    toastError("重置今日次数失败", error);
    return;
  } finally {
    setButtonBusy(button, false);
  }

  toast(
    "已重置今日次数",
    "影响 " + formatNumber(result && result.users) + " 位成员",
    "success"
  );
  invalidateView("audit");
  await Promise.all([
    loadUsers().catch(() => {}),
    loadGroups().catch(() => {}),
    refreshSummary().catch(() => {}),
  ]);
}

/* ==========================================================================
   Bootstrap
   ========================================================================== */

export function initGroups() {
  const groupSearch = qs("#groupSearch");
  if (groupSearch) {
    const runGroupSearch = debounce(() => {
      state.groups.page = 1;
      loadGroups().catch((error) => toastError("加载群组列表失败", error));
    }, 280);
    groupSearch.addEventListener("input", () => {
      state.groups.filters.query = groupSearch.value.trim();
      runGroupSearch();
    });
  }

  const groupList = qs("#groupList");
  if (groupList) {
    delegate(groupList, "click", ".group-item", (event, element) => {
      selectGroup(element.dataset.groupId);
    });
  }

  const userSearch = qs("#userSearch");
  if (userSearch) {
    const runUserSearch = debounce(() => {
      state.users.page = 1;
      loadUsers().catch((error) => toastError("加载成员列表失败", error));
    }, 280);
    userSearch.addEventListener("input", () => {
      state.users.filters.query = userSearch.value.trim();
      runUserSearch();
    });
  }

  const saveButton = qs("#saveUserButton");
  if (saveButton) {
    saveButton.addEventListener("click", () => {
      saveUser();
    });
  }
  const deleteButton = qs("#deleteUserButton");
  if (deleteButton) {
    deleteButton.addEventListener("click", () => {
      deleteUser();
    });
  }
  const resetButton = qs("#resetGroupDrawsButton");
  if (resetButton) {
    resetButton.addEventListener("click", () => {
      resetGroupDraws();
    });
  }

  onDrawerClose("userDrawer", () => {
    state.activeUser = null;
    state.userCurrentSelection = undefined;
    currentAllyDisplay = null;
    unlockLimit = UNLOCK_PREVIEW;
    if (currentCombo) {
      currentCombo.close();
      currentCombo = null;
    }
    if (unlockCombo) {
      unlockCombo.close();
      unlockCombo = null;
    }
    currentAllyNode = null;
    unlockListNode = null;
    unlockCountNode = null;
    unlockMoreNode = null;
  });
}
