/**
 * Catalog entry picker used by the member drawer (current ally + unlocks).
 *
 * Selection state is owned by the caller; typing in the box never mutates it,
 * which is what previously wiped a member's current ally on the first keystroke.
 */

import { h, replaceChildren, clear } from "../core/dom.js";
import { icon } from "../core/icons.js";
import { debounce } from "../core/format.js";
import { apiGet } from "../core/bridge.js";
import { thumbFrame } from "./widgets.js";

/**
 * @param {{input: HTMLInputElement, results: HTMLElement,
 *          onSelect: (entry: object) => void}} options
 */
export function createEntryCombo(options) {
  const input = options.input;
  const results = options.results;
  const onSelect = options.onSelect;
  let sequence = 0;
  let items = [];
  let activeIndex = -1;

  input.setAttribute("role", "combobox");
  input.setAttribute("aria-expanded", "false");
  input.setAttribute("aria-autocomplete", "list");
  input.setAttribute("autocomplete", "off");
  results.setAttribute("role", "listbox");

  function close() {
    clear(results);
    results.hidden = true;
    input.setAttribute("aria-expanded", "false");
    items = [];
    activeIndex = -1;
  }

  function setActive(index) {
    const nodes = Array.from(results.querySelectorAll(".combo-option"));
    if (!nodes.length) {
      return;
    }
    activeIndex = (index + nodes.length) % nodes.length;
    nodes.forEach((node, position) => {
      node.classList.toggle("is-active", position === activeIndex);
      node.setAttribute("aria-selected", position === activeIndex ? "true" : "false");
    });
    nodes[activeIndex].scrollIntoView({ block: "nearest" });
  }

  function choose(index) {
    const entry = items[index];
    if (!entry) {
      return;
    }
    close();
    onSelect(entry);
  }

  function renderStatus(text, glyph) {
    replaceChildren(
      results,
      h("div", { class: "combo-status" }, icon(glyph || "info"), h("span", { text }))
    );
    results.hidden = false;
    input.setAttribute("aria-expanded", "true");
  }

  function renderItems(entries) {
    items = entries;
    activeIndex = -1;
    if (!entries.length) {
      renderStatus("没有匹配的素材", "search-x");
      return;
    }
    const nodes = entries.map((entry, index) => {
      const option = h(
        "button",
        { type: "button", class: "combo-option", role: "option", "aria-selected": "false" },
        thumbFrame(entry, { small: true }),
        h(
          "div",
          { class: "primary-cell" },
          h("strong", { text: entry.name || "未命名" }),
          h("span", { text: "#" + entry.id + (entry.source ? " · " + entry.source : "") })
        )
      );
      option.addEventListener("mousedown", (event) => {
        event.preventDefault();
        choose(index);
      });
      return option;
    });
    replaceChildren(results, nodes);
    results.hidden = false;
    input.setAttribute("aria-expanded", "true");
  }

  const search = debounce(async (query) => {
    const token = ++sequence;
    try {
      const payload = await apiGet("admin/entries", {
        query,
        page: 1,
        page_size: 20,
        status: "all",
        sort: "id_asc",
      });
      if (token !== sequence) {
        return;
      }
      renderItems(Array.isArray(payload && payload.items) ? payload.items : []);
    } catch (error) {
      if (token === sequence) {
        renderStatus(error && error.message ? error.message : "搜索失败", "circle-alert");
      }
    }
  }, 260);

  input.addEventListener("input", () => {
    const query = input.value.trim();
    if (!query) {
      close();
      return;
    }
    renderStatus("正在搜索…", "loader-circle");
    search(query);
  });

  input.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActive(activeIndex + 1);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive(activeIndex - 1);
    } else if (event.key === "Enter") {
      if (activeIndex >= 0) {
        event.preventDefault();
        choose(activeIndex);
      }
    } else if (event.key === "Escape") {
      if (!results.hidden) {
        event.stopPropagation();
        close();
      }
    }
  });

  input.addEventListener("blur", () => {
    window.setTimeout(close, 120);
  });

  return { close, reset: () => {
    input.value = "";
    close();
  } };
}
