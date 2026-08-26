/** Pager shared by every paginated table. */

import { h, replaceChildren } from "../core/dom.js";
import { icon } from "../core/icons.js";
import { formatNumber } from "../core/format.js";

/**
 * @param {Element} container
 * @param {{page: number, pages: number, total: number}} meta
 * @param {(page: number) => void} onChange
 * @param {{compact?: boolean, unit?: string}} [options]
 */
export function renderPagination(container, meta, onChange, options) {
  if (!container) {
    return;
  }
  const opts = options || {};
  const total = Number(meta && meta.total) || 0;
  const pages = Math.max(1, Number(meta && meta.pages) || 1);
  const page = Math.min(pages, Math.max(1, Number(meta && meta.page) || 1));

  container.classList.toggle("compact-pagination", Boolean(opts.compact));

  if (total === 0) {
    replaceChildren(container);
    container.hidden = true;
    return;
  }
  container.hidden = false;

  const previous = h(
    "button",
    { type: "button", class: "icon-button small", "aria-label": "上一页", disabled: page <= 1 },
    icon("chevron-left")
  );
  const next = h(
    "button",
    { type: "button", class: "icon-button small", "aria-label": "下一页", disabled: page >= pages },
    icon("chevron-right")
  );
  previous.addEventListener("click", () => onChange(page - 1));
  next.addEventListener("click", () => onChange(page + 1));

  replaceChildren(
    container,
    h("span", {
      class: "pagination-meta",
      text: "共 " + formatNumber(total) + " " + (opts.unit || "条"),
    }),
    h(
      "div",
      { class: "pagination-actions" },
      previous,
      h("span", { class: "page-number", text: page + " / " + pages }),
      next
    )
  );
}
