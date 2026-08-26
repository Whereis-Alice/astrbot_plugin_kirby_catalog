/** Shared presentational building blocks. */

import { h, clear, replaceChildren, safeUrl } from "../core/dom.js";
import { icon } from "../core/icons.js";
import { formatNumber, formatPercent } from "../core/format.js";

/**
 * Renders the metric strip.
 *
 * @param {Element} container
 * @param {Array<{label: string, value: any, note?: string, glyph: string, color?: string}>} metrics
 */
export function renderMetrics(container, metrics) {
  if (!container) {
    return;
  }
  const cards = (metrics || []).map((metric) =>
    h(
      "article",
      {
        class: "metric",
        style: metric.color ? { "--metric-color": "var(" + metric.color + ")" } : null,
      },
      h(
        "span",
        { class: "metric-label" },
        h("span", { text: metric.label }),
        icon(metric.glyph)
      ),
      h("strong", {
        class: "metric-value",
        text: typeof metric.value === "number" ? formatNumber(metric.value) : String(metric.value),
      }),
      metric.note ? h("span", { class: "metric-note", text: metric.note }) : null
    )
  );
  replaceChildren(container, cards);
}

/**
 * Renders an empty-state block.
 *
 * @param {Element} container
 * @param {{glyph: string, title: string, message?: string, tall?: boolean}} options
 */
export function renderEmptyState(container, options) {
  if (!container) {
    return;
  }
  const opts = options || {};
  replaceChildren(
    container,
    h(
      "div",
      { class: opts.tall ? "empty-state tall" : "empty-state" },
      h("span", { class: "empty-mark" }, icon(opts.glyph || "circle-slash")),
      h("strong", { text: opts.title || "暂无数据" }),
      opts.message ? h("span", { text: opts.message }) : null
    )
  );
}

/** A pill badge. */
export function badge(text, tone, glyph) {
  const cls = tone ? "badge " + tone : "badge";
  return h("span", { class: cls }, glyph ? icon(glyph) : null, h("span", { text: text }));
}

/** Wraps several badges in a row, skipping empties. */
export function badgeRow(...badges) {
  return h("div", { class: "badge-row" }, badges.filter(Boolean));
}

/**
 * Thumbnail frame for a catalog entry payload.
 *
 * @param {{thumbnail?: string, name?: string, has_asset?: boolean}} entry
 * @param {{small?: boolean}} [options]
 */
export function thumbFrame(entry, options) {
  const small = options && options.small;
  const cls = small ? "thumb-frame small-thumb" : "thumb-frame";
  const source = entry && typeof entry.thumbnail === "string" && entry.thumbnail.startsWith("data:image/")
    ? entry.thumbnail
    : "";
  if (source) {
    return h("span", { class: cls }, h("img", { src: source, alt: entry.name || "", loading: "lazy" }));
  }
  return h("span", { class: cls }, icon("image-off"));
}

/** A two-line table cell. */
export function primaryCell(title, subtitle) {
  return h(
    "div",
    { class: "primary-cell" },
    h("strong", { text: title || "--" }),
    subtitle ? h("span", { text: subtitle }) : null
  );
}

/** A muted two-line table cell. */
export function secondaryCell(title, subtitle) {
  return h(
    "div",
    { class: "secondary-cell" },
    h("strong", { text: title || "--" }),
    subtitle ? h("span", { text: subtitle }) : null
  );
}

/** Renders the completeness bars used on the overview. */
export function renderHealthList(container, rows) {
  if (!container) {
    return;
  }
  const nodes = (rows || []).map((row) =>
    h(
      "div",
      { class: "health-row", style: { "--fill": "var(" + (row.color || "--accent") + ")" } },
      h(
        "div",
        { class: "health-meta" },
        h("strong", { text: row.label }),
        h("span", { text: formatPercent(row.percent) + " · 缺 " + formatNumber(row.missing) })
      ),
      h(
        "div",
        { class: "progress-track" },
        h("span", { class: "progress-fill", style: { width: Math.max(0, Math.min(100, row.percent)) + "%" } })
      )
    )
  );
  replaceChildren(container, nodes);
}

/**
 * Renders a horizontal distribution list.
 *
 * @param {Element} container
 * @param {Array<{label: string, value: number}>} rows
 * @param {{empty?: string}} [options]
 */
export function renderBarList(container, rows, options) {
  if (!container) {
    return;
  }
  const items = rows || [];
  if (!items.length) {
    renderEmptyState(container, {
      glyph: "chart-no-axes-column-increasing",
      title: (options && options.empty) || "暂无数据",
    });
    return;
  }
  const palette = ["--accent", "--cyan", "--yellow", "--green", "--purple"];
  const maximum = Math.max(1, ...items.map((item) => Number(item.value) || 0));
  const nodes = items.map((item, index) =>
    h(
      "div",
      { class: "bar-row", style: { "--bar-color": "var(" + palette[index % palette.length] + ")" } },
      h(
        "div",
        { class: "bar-meta" },
        h("strong", { text: item.label || "未知" }),
        h("span", { text: formatNumber(item.value) })
      ),
      h(
        "div",
        { class: "bar-track" },
        h("span", {
          class: "bar-fill",
          style: { width: Math.max(3, ((Number(item.value) || 0) * 100) / maximum) + "%" },
        })
      )
    )
  );
  replaceChildren(container, nodes);
}

/** Small inline progress bar for table cells. */
export function miniProgress(percent, caption) {
  return h(
    "div",
    { class: "mini-progress" },
    h(
      "div",
      { class: "progress-track" },
      h("span", {
        class: "progress-fill",
        style: { width: Math.max(0, Math.min(100, Number(percent) || 0)) + "%" },
      })
    ),
    h("span", { text: caption })
  );
}

/** Fills a table body with shimmer rows while a request is in flight. */
export function renderSkeletonRows(tbody, rows, columns) {
  if (!tbody) {
    return;
  }
  clear(tbody);
  const rowCount = rows || 6;
  const columnCount = columns || 4;
  for (let rowIndex = 0; rowIndex < rowCount; rowIndex += 1) {
    const cells = [];
    for (let columnIndex = 0; columnIndex < columnCount; columnIndex += 1) {
      cells.push(
        h(
          "td",
          null,
          h("span", {
            class: columnIndex === 0 ? "skeleton skeleton-line short" : "skeleton skeleton-line",
          })
        )
      );
    }
    tbody.appendChild(h("tr", { "aria-hidden": "true" }, cells));
  }
}

/** Fills a block container with shimmer lines. */
export function renderSkeletonStack(container, rows) {
  if (!container) {
    return;
  }
  const lines = [];
  for (let index = 0; index < (rows || 5); index += 1) {
    lines.push(h("span", { class: index % 3 === 2 ? "skeleton skeleton-line short" : "skeleton skeleton-line" }));
  }
  replaceChildren(container, h("div", { class: "skeleton-stack", "aria-hidden": "true" }, lines));
}

/**
 * Toggles the busy state of a button. The spinner class lands on the SVG that
 * is already in the DOM, which is why the old implementation never animated.
 */
export function setButtonBusy(button, busy, busyText) {
  if (!button) {
    return;
  }
  const label = button.querySelector("span");
  const glyph = button.querySelector("svg.icon");
  if (busy) {
    if (label && !button.dataset.idleLabel) {
      button.dataset.idleLabel = label.textContent || "";
      label.textContent = busyText || "处理中";
    }
    if (glyph) {
      glyph.classList.add("is-spinning");
    }
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    return;
  }
  if (label && button.dataset.idleLabel !== undefined) {
    label.textContent = button.dataset.idleLabel;
    delete button.dataset.idleLabel;
  }
  if (glyph) {
    glyph.classList.remove("is-spinning");
  }
  button.disabled = false;
  button.removeAttribute("aria-busy");
}

/** A row-level ghost action button. */
export function rowAction(glyph, label, handler) {
  const button = h(
    "button",
    { type: "button", class: "icon-button small row-action", "aria-label": label, title: label },
    icon(glyph)
  );
  button.addEventListener("click", (event) => {
    event.stopPropagation();
    handler(event);
  });
  return button;
}

/** External link, silently dropped when the URL is not http(s). */
export function externalLink(url, label) {
  const href = safeUrl(url);
  if (!href) {
    return null;
  }
  return h(
    "a",
    { class: "text-button", href, target: "_blank", rel: "noreferrer noopener" },
    h("span", { text: label || "查看原页面" }),
    icon("external-link")
  );
}

/** Definition list used by drawer metadata blocks. */
export function metadataList(rows) {
  const items = (rows || []).filter((row) => row && row.value !== "" && row.value !== null && row.value !== undefined);
  if (!items.length) {
    return null;
  }
  return h(
    "dl",
    { class: "metadata-list" },
    items.map((row) =>
      h(
        "div",
        { class: "metadata-row" },
        h("dt", { text: row.label }),
        h("dd", { text: String(row.value) })
      )
    )
  );
}

/** Section wrapper used inside drawers. */
export function formSection(title, hint, ...children) {
  return h(
    "section",
    { class: "form-section" },
    h(
      "div",
      { class: "form-section-head" },
      h("h3", null, h("span", { text: title }), hint ? h("small", { text: hint }) : null)
    ),
    children
  );
}
