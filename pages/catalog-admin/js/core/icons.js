/**
 * Inline SVG icon factory.
 *
 * The previous build shipped the full Lucide runtime (~400 kB) and called
 * lucide.createIcons() after every render, which re-scanned the whole document.
 * Here each glyph is stamped once into a detached template and afterwards only
 * cloned, so rendering a table of 30 rows costs 30 cloneNode() calls.
 */

import { ICON_GLYPHS, ICON_NAMES } from "./icon-glyphs.js";

const SVG_NS = "http://www.w3.org/2000/svg";
const templates = new Map();

const FALLBACK = "circle-help";

function buildTemplate(name) {
  const glyph = ICON_GLYPHS[name] || ICON_GLYPHS[FALLBACK] || "";
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "1.75");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  svg.classList.add("icon");
  // Glyph data is a build-time constant extracted from Lucide, never user input.
  svg.innerHTML = glyph;
  return svg;
}

/**
 * Returns a fresh svg.icon node for the given glyph name.
 *
 * @param {string} name Lucide glyph name.
 * @param {{className?: string, size?: number, title?: string}} [options]
 * @returns {SVGElement}
 */
export function icon(name, options) {
  const key = ICON_GLYPHS[name] ? name : FALLBACK;
  let template = templates.get(key);
  if (!template) {
    template = buildTemplate(key);
    templates.set(key, template);
  }
  const node = template.cloneNode(true);
  const opts = options || {};
  if (opts.className) {
    const extra = String(opts.className).split(/\s+/).filter(Boolean);
    for (const cls of extra) {
      node.classList.add(cls);
    }
  }
  if (opts.size) {
    node.style.width = opts.size + "px";
    node.style.height = opts.size + "px";
  }
  if (opts.title) {
    const title = document.createElementNS(SVG_NS, "title");
    title.textContent = opts.title;
    node.insertBefore(title, node.firstChild);
    node.removeAttribute("aria-hidden");
    node.setAttribute("role", "img");
  }
  return node;
}

/**
 * Swaps every [data-icon] placeholder below the given root for a real SVG.
 * Called once for the static shell; dynamic renderers build icons directly.
 *
 * @param {ParentNode} [root=document]
 */
export function hydrateIcons(root) {
  const scope = root || document;
  const nodes = scope.querySelectorAll("[data-icon]");
  for (const node of nodes) {
    const name = node.getAttribute("data-icon");
    const replacement = icon(name);
    const extra = node.getAttribute("class");
    if (extra) {
      for (const cls of extra.split(/\s+/).filter(Boolean)) {
        replacement.classList.add(cls);
      }
    }
    node.replaceWith(replacement);
  }
}

/**
 * Replaces the icon currently rendered inside the host element with a
 * different glyph, preserving position. Used by the toast/confirm widgets.
 *
 * @param {Element} host
 * @param {string} name
 */
export function swapIcon(host, name) {
  if (!host) {
    return;
  }
  const current = host.querySelector("svg.icon");
  const replacement = icon(name);
  if (current) {
    current.replaceWith(replacement);
  } else {
    host.appendChild(replacement);
  }
}

export { ICON_NAMES };
