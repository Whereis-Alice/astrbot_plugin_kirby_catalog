/**
 * Minimal hyperscript helpers.
 *
 * Every renderer in this dashboard builds DOM through h() instead of assigning
 * innerHTML. That removes the XSS surface around catalog/terminology text and
 * lets views patch single cells instead of rewriting whole tables, so the
 * search box no longer loses focus while typing.
 */

const SVG_TAGS = new Set(["svg", "path", "circle", "rect", "line", "g"]);

function appendChild(parent, child) {
  if (child === null || child === undefined || child === false || child === true) {
    return;
  }
  if (Array.isArray(child)) {
    for (const item of child) {
      appendChild(parent, item);
    }
    return;
  }
  if (child instanceof Node) {
    parent.appendChild(child);
    return;
  }
  parent.appendChild(document.createTextNode(String(child)));
}

/**
 * Creates an element.
 *
 * Supported props: "class", "text", "dataset", "style", "attrs", any DOM
 * property name, any "onclick"-style listener key, and plain attributes.
 *
 * @param {string} tag
 * @param {Object|null} [props]
 * @param {...any} children
 * @returns {HTMLElement}
 */
export function h(tag, props, ...children) {
  const node = SVG_TAGS.has(tag)
    ? document.createElementNS("http://www.w3.org/2000/svg", tag)
    : document.createElement(tag);

  if (props) {
    for (const key of Object.keys(props)) {
      const value = props[key];
      if (value === null || value === undefined) {
        continue;
      }
      if (key === "class" || key === "className") {
        node.setAttribute("class", String(value));
      } else if (key === "text") {
        node.textContent = String(value);
      } else if (key === "dataset") {
        for (const dataKey of Object.keys(value)) {
          if (value[dataKey] !== null && value[dataKey] !== undefined) {
            node.dataset[dataKey] = String(value[dataKey]);
          }
        }
      } else if (key === "style" && typeof value === "object") {
        for (const styleKey of Object.keys(value)) {
          node.style.setProperty(styleKey, String(value[styleKey]));
        }
      } else if (key === "attrs") {
        for (const attrKey of Object.keys(value)) {
          if (value[attrKey] !== null && value[attrKey] !== undefined) {
            node.setAttribute(attrKey, String(value[attrKey]));
          }
        }
      } else if (key.length > 2 && key.startsWith("on") && typeof value === "function") {
        node.addEventListener(key.slice(2).toLowerCase(), value);
      } else if (typeof value === "boolean") {
        if (key in node) {
          node[key] = value;
        } else if (value) {
          node.setAttribute(key, "");
        }
      } else if (key in node && !(node instanceof SVGElement)) {
        node[key] = value;
      } else {
        node.setAttribute(key, String(value));
      }
    }
  }

  appendChild(node, children);
  return node;
}

/** Removes every child of a node without touching innerHTML. */
export function clear(node) {
  if (!node) {
    return node;
  }
  while (node.firstChild) {
    node.removeChild(node.firstChild);
  }
  return node;
}

/** Replaces the children of a node in one shot. */
export function replaceChildren(node, ...children) {
  clear(node);
  appendChild(node, children);
  return node;
}

/** document.querySelector shorthand. */
export function qs(selector, scope) {
  return (scope || document).querySelector(selector);
}

/** Array-returning document.querySelectorAll shorthand. */
export function qsa(selector, scope) {
  return Array.from((scope || document).querySelectorAll(selector));
}

/**
 * Attaches one delegated listener instead of one listener per row. Keeps long
 * lists (audit trail, unlock list) cheap to re-render.
 *
 * @param {Element} root
 * @param {string} type
 * @param {string} selector
 * @param {(event: Event, matched: Element) => void} handler
 */
export function delegate(root, type, selector, handler) {
  if (!root) {
    return () => {};
  }
  const listener = (event) => {
    const target = event.target;
    if (!(target instanceof Element)) {
      return;
    }
    const matched = target.closest(selector);
    if (matched && root.contains(matched)) {
      handler(event, matched);
    }
  };
  root.addEventListener(type, listener);
  return () => root.removeEventListener(type, listener);
}

/** Sets textContent only when it actually changed, avoiding layout churn. */
export function setText(node, value) {
  if (!node) {
    return;
  }
  const next = value === null || value === undefined ? "" : String(value);
  if (node.textContent !== next) {
    node.textContent = next;
  }
}

/** Toggles the hidden attribute. */
export function setHidden(node, hidden) {
  if (!node) {
    return;
  }
  node.hidden = Boolean(hidden);
}

/**
 * Only http(s) URLs survive; anything else (javascript:, data:, relative junk)
 * collapses to an empty string so it is never handed to an href.
 */
export function safeUrl(value) {
  const text = String(value === null || value === undefined ? "" : value).trim();
  return /^https?:\/\//i.test(text) ? text : "";
}
