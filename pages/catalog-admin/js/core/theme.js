/**
 * Skin handling.
 *
 * The AstrBot host rewrites <html data-theme> on every render and strips any
 * color-scheme meta tag, so this page never touches either. The resolved skin
 * lives on <html data-kirby-skin> (what the CSS selects on) while the raw user
 * preference stays on <html data-kirby-theme> (what the segmented control
 * highlights).
 */

import { state } from "./state.js";

export const THEME_OPTIONS = Object.freeze(["auto", "dreamland", "starlight", "metaknight"]);

const LEGACY_ALIASES = Object.freeze({
  kirby: "dreamland",
  light: "dreamland",
  dark: "starlight",
  meta: "metaknight",
});

/** Normalises a stored preference, tolerating the pre-4.0 theme names. */
export function normalizeTheme(value) {
  const text = String(value === null || value === undefined ? "" : value).trim().toLowerCase();
  if (!text) {
    return "auto";
  }
  if (THEME_OPTIONS.includes(text)) {
    return text;
  }
  return LEGACY_ALIASES[text] || "auto";
}

/** Resolves "auto" against the host appearance. */
export function resolveSkin(theme, hostDark) {
  const normalized = normalizeTheme(theme);
  if (normalized !== "auto") {
    return normalized;
  }
  return hostDark ? "starlight" : "dreamland";
}

/**
 * Writes the skin attributes and syncs the segmented control.
 *
 * @param {string} theme Raw preference.
 * @param {{persistedTheme?: string}} [options]
 */
export function applyTheme(theme, options) {
  const normalized = normalizeTheme(theme);
  state.theme = normalized;
  const root = document.documentElement;
  root.setAttribute("data-kirby-theme", normalized);
  root.setAttribute("data-kirby-skin", resolveSkin(normalized, state.hostDark));

  const chips = document.querySelectorAll("[data-theme-option]");
  for (const chip of chips) {
    const isActive = chip.dataset.themeOption === normalized;
    chip.classList.toggle("is-active", isActive);
    chip.setAttribute("aria-pressed", isActive ? "true" : "false");
  }
  return normalized;
}

/** Re-resolves "auto" after the host toggles light/dark. */
export function refreshHostAppearance(hostDark) {
  state.hostDark = Boolean(hostDark);
  applyTheme(state.theme);
}
