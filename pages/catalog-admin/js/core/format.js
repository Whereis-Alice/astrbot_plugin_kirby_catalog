/** Shared formatting and timing helpers. */

const numberFormatter = new Intl.NumberFormat("zh-CN");

/** 12345 -> "12,345" */
export function formatNumber(value) {
  const numeric = Number(value);
  return numberFormatter.format(Number.isFinite(numeric) ? numeric : 0);
}

/** 87.4 -> "87%" */
export function formatPercent(value) {
  const numeric = Number(value);
  return (Number.isFinite(numeric) ? Math.round(numeric) : 0) + "%";
}

/**
 * The backend emits either naive local timestamps or ISO strings with an
 * offset. Trim any trailing offset (not just the literal +08:00 the old build
 * special-cased) and drop the ISO "T" separator.
 */
export function formatDateTime(value) {
  const text = String(value === null || value === undefined ? "" : value).trim();
  if (!text) {
    return "--";
  }
  return text
    .replace("T", " ")
    .replace(/\.\d+$/, "")
    .replace(/\s*(?:Z|[+-]\d{2}:?\d{2})$/i, "")
    .trim();
}

/** Formats a byte count for upload hints. */
export function formatBytes(value) {
  const bytes = Number(value);
  if (!Number.isFinite(bytes) || bytes <= 0) {
    return "0 B";
  }
  const units = ["B", "KB", "MB", "GB"];
  let index = 0;
  let size = bytes;
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024;
    index += 1;
  }
  const digits = size >= 100 || index === 0 ? 0 : 1;
  return size.toFixed(digits) + " " + units[index];
}

/** Trailing-edge debounce; 280 ms matches the original search feel. */
export function debounce(callback, delay) {
  let timer = 0;
  const wait = typeof delay === "number" ? delay : 280;
  return function debounced(...args) {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => callback.apply(this, args), wait);
  };
}

/** Splits a textarea value into trimmed lines, also honouring "|" separators. */
export function splitLines(value) {
  return String(value === null || value === undefined ? "" : value)
    .split(/[\r\n|]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

/** Clamps a number into an inclusive range, falling back on NaN. */
export function clampNumber(value, minimum, maximum, fallback) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return fallback;
  }
  return Math.min(maximum, Math.max(minimum, Math.trunc(numeric)));
}

/** Coerces anything the API may hand back into printable text. */
export function stringifyValue(value) {
  if (value === null || value === undefined || value === "") {
    return "";
  }
  if (typeof value === "string") {
    return value;
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  if (Array.isArray(value)) {
    return value.map((item) => stringifyValue(item)).filter(Boolean).join("、");
  }
  if (typeof value === "object") {
    try {
      return JSON.stringify(value);
    } catch (error) {
      return String(value);
    }
  }
  return String(value);
}
