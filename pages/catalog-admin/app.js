/**
 * Boot sequence for the Kirby catalog admin page.
 *
 * Everything is imported statically: the host asset token expires 60 s after
 * the page is served, so a lazy import() later in the session would 404.
 *
 * Order matters. Overlays (toast / confirm / drawer) are wired before any view
 * so that a failing first request still has somewhere to report itself, icons
 * are hydrated once for the whole document, and the router is only started
 * after every view has registered its loader.
 */

import { qs, setText, setHidden, delegate } from "./js/core/dom.js";
import { waitForBridge, apiPost, detectHostDark } from "./js/core/bridge.js";
import { state } from "./js/core/state.js";
import { formatNumber } from "./js/core/format.js";
import { hydrateIcons } from "./js/core/icons.js";
import {
  THEME_OPTIONS,
  applyTheme,
  normalizeTheme,
  refreshHostAppearance,
} from "./js/core/theme.js";
import { onSummary, refreshSummary } from "./js/core/summary.js";
import { initRouter, registerView, switchView } from "./js/core/router.js";
import { initToasts, toastError } from "./js/ui/toast.js";
import { initConfirm } from "./js/ui/confirm.js";
import { initDrawers } from "./js/ui/drawer.js";
import { initOverview, loadOverview } from "./js/views/overview.js";
import { initCatalog, loadCatalog, openAddEntryDialog } from "./js/views/catalog.js";
import { initTerminology, loadTerminology } from "./js/views/terminology.js";
import { initWikiIndex, loadWikiIndex } from "./js/views/wiki-index.js";
import { initGroups, loadGroups } from "./js/views/groups.js";
import { initTrash, loadTrash } from "./js/views/trash.js";
import { initAudit, loadAudit } from "./js/views/audit.js";

/* ==========================================================================
   Nav badges
   ========================================================================== */

function paintBadge(node, value) {
  if (!node) {
    return;
  }
  const count = Math.max(0, Number(value) || 0);
  if (!count) {
    setText(node, "");
    setHidden(node, true);
    return;
  }
  setText(node, count > 99 ? "99+" : formatNumber(count));
  setHidden(node, false);
}

/** Catalog badge counts anything needing attention; trash badge counts rows. */
function renderNavBadges(summary) {
  const catalog = (summary && summary.catalog) || {};
  const attention =
    Math.max(0, Number(catalog.missing_assets) || 0) +
    Math.max(0, Number(catalog.missing_descriptions) || 0);
  paintBadge(qs("#navBadgeCatalog"), attention);
  paintBadge(qs("#navBadgeTrash"), summary && summary.trash);
}

/* ==========================================================================
   Theme
   ========================================================================== */

/** Optimistic skin switch that rolls back to the server value on failure. */
async function persistTheme(theme) {
  const previous = state.theme;
  if (theme === previous) {
    return;
  }
  applyTheme(theme);
  try {
    const result = await apiPost("admin/preferences", { theme: theme });
    applyTheme((result && result.theme) || theme);
  } catch (error) {
    applyTheme(previous);
    toastError("主题保存失败", error);
  }
}

function initThemeSwitch() {
  const root = qs("#themeSwitch");
  if (!root) {
    return;
  }
  delegate(root, "click", "[data-theme-option]", (event, chip) => {
    const theme = normalizeTheme(chip.dataset.themeOption);
    if (!THEME_OPTIONS.includes(theme)) {
      return;
    }
    persistTheme(theme);
  });
}

/* ==========================================================================
   Views
   ========================================================================== */

function initViews() {
  initOverview();
  initCatalog();
  initTerminology();
  initWikiIndex();
  initGroups();
  initTrash();
  initAudit();

  registerView("overview", loadOverview);
  registerView("catalog", loadCatalog);
  registerView("terminology", loadTerminology);
  registerView("wiki-index", loadWikiIndex);
  registerView("groups", loadGroups);
  registerView("trash", loadTrash);
  registerView("audit", loadAudit);
}

/* ==========================================================================
   Bootstrap
   ========================================================================== */

/** Reacts to the host switching between light and dark while the page is open. */
function applyContext(context) {
  refreshHostAppearance(detectHostDark(context));
}

function revealShell() {
  const shell = qs("#appShell");
  if (shell) {
    shell.setAttribute("aria-busy", "false");
  }
  const loader = qs("#initialLoader");
  if (loader) {
    loader.classList.add("is-hidden");
  }
}

async function bootstrap() {
  initToasts();
  initConfirm();
  initDrawers();
  hydrateIcons(document);

  initViews();
  initThemeSwitch();
  onSummary(renderNavBadges);

  const quickAdd = qs("#quickAddButton");
  if (quickAdd) {
    quickAdd.addEventListener("click", () => {
      openAddEntryDialog();
    });
  }

  // Resolve the requested view before any await so a deep link cannot be lost
  // to a hashchange fired while the bridge handshake is still pending.
  const route = initRouter();

  const bridge = await waitForBridge();
  if (bridge && typeof bridge.ready === "function") {
    try {
      await bridge.ready();
    } catch (error) {
      console.warn("bridge ready() failed", error);
    }
  }
  if (bridge && typeof bridge.getContext === "function") {
    applyContext(bridge.getContext());
  } else {
    applyContext(null);
  }
  // Dashboard 4.22 exposes onContext(); older builds shipped onContextChange().
  const subscribe =
    bridge && typeof bridge.onContext === "function"
      ? bridge.onContext
      : bridge && typeof bridge.onContextChange === "function"
        ? bridge.onContextChange
        : null;
  if (subscribe) {
    subscribe.call(bridge, (context) => applyContext(context));
  }

  const summary = await refreshSummary();
  applyTheme((summary && summary.preferences && summary.preferences.theme) || state.theme);

  await switchView(route.view, { replace: true });
}

bootstrap()
  .catch((error) => {
    console.error("catalog admin bootstrap failed", error);
    toastError("管理台初始化失败", error);
  })
  .finally(() => {
    // The shell must become interactive even after a failed handshake, or the
    // operator is left staring at the splash with no way to retry.
    revealShell();
  });
