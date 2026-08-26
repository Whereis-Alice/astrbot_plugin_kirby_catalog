/**
 * Single mutable store for the dashboard.
 *
 * Filter values live here rather than being read back out of the DOM, which is
 * what previously let the select elements and the requested query drift apart.
 */

export const state = {
  /** Latest admin/summary payload. */
  summary: null,
  /** Whether the AstrBot host is currently rendering in dark mode. */
  hostDark: false,
  /** Raw user preference: auto | dreamland | starlight | metaknight. */
  theme: "auto",
  /** Views whose data has been fetched at least once. */
  loaded: new Set(),

  entries: {
    items: [],
    page: 1,
    pages: 1,
    total: 0,
    pageSize: 30,
    filters: { query: "", source: "", kind: "", status: "all", sort: "id_asc" },
  },

  terminology: {
    items: [],
    page: 1,
    pages: 1,
    total: 0,
    pageSize: 30,
    categories: [],
    revision: null,
    filters: { query: "", category: "", origin: "", status: "", sort: "category" },
  },

  wikiIndex: {
    items: [],
    page: 1,
    pages: 1,
    total: 0,
    pageSize: 30,
    sites: [],
    stats: { total: 0, overrides: 0, conflicts: 0, sites: {} },
    filters: { query: "", site: "", status: "", sort: "number" },
  },

  groups: {
    items: [],
    page: 1,
    pages: 1,
    total: 0,
    pageSize: 20,
    filters: { query: "" },
  },

  users: {
    items: [],
    page: 1,
    pages: 1,
    total: 0,
    pageSize: 20,
    group: null,
    filters: { query: "" },
  },

  trash: { items: [] },
  audit: { items: [], limit: 200 },

  selectedGroupId: null,

  /** Drawer working copies. */
  activeEntry: null,
  activeUser: null,
  activeTerm: null,
  activeWikiRow: null,
  /** True while the terminology drawer is creating a brand-new term. */
  terminologyDraftIsNew: false,

  /**
   * undefined -> leave the stored current ally untouched,
   * null      -> explicitly clear it,
   * string    -> replace it with this entry id.
   */
  userCurrentSelection: undefined,

  /** Upload token returned by admin/uploads/image for the add-entry modal. */
  pendingUpload: null,

  /** Monotonic counters used to discard stale in-flight responses. */
  requestSequence: {
    entries: 0,
    groups: 0,
    users: 0,
    terminology: 0,
    wikiIndex: 0,
    current: 0,
    unlock: 0,
    trash: 0,
    audit: 0,
    summary: 0,
  },
};

/** Bumps and returns the sequence token for a request family. */
export function nextSequence(key) {
  state.requestSequence[key] = (state.requestSequence[key] || 0) + 1;
  return state.requestSequence[key];
}

/** True when the given token is still the newest request for that family. */
export function isCurrentSequence(key, token) {
  return state.requestSequence[key] === token;
}

/** Marks a view as needing a refetch on next activation. */
export function invalidateView(...views) {
  for (const view of views) {
    state.loaded.delete(view);
  }
}
