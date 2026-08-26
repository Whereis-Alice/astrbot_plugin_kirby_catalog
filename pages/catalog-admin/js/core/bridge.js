/**
 * Thin wrapper over window.AstrBotPluginPage.
 *
 * The host injects the bridge just before the page module runs, but the asset
 * token it hands out expires after 60 s, so every module is loaded eagerly at
 * boot and only network calls go through here afterwards.
 */

let bridgeRef = null;

/** Resolves once the host bridge object exists, or rejects after a timeout. */
export function waitForBridge(timeout) {
  const limit = typeof timeout === "number" ? timeout : 6000;
  if (window.AstrBotPluginPage) {
    bridgeRef = window.AstrBotPluginPage;
    return Promise.resolve(bridgeRef);
  }
  return new Promise((resolve, reject) => {
    const started = Date.now();
    const tick = window.setInterval(() => {
      if (window.AstrBotPluginPage) {
        window.clearInterval(tick);
        bridgeRef = window.AstrBotPluginPage;
        resolve(bridgeRef);
        return;
      }
      if (Date.now() - started > limit) {
        window.clearInterval(tick);
        reject(new Error("AstrBot Page Bridge 未加载，请从 AstrBot Dashboard 打开本页面"));
      }
    }, 60);
  });
}

/** The bridge instance, once waitForBridge() has resolved. */
export function getBridge() {
  return bridgeRef || window.AstrBotPluginPage || null;
}

/**
 * The host already unwraps axios envelopes, but plugin handlers may still
 * return {status, data} or {ok: false, message}. Normalise both and turn
 * failures into thrown errors so callers can rely on try/catch.
 */
export function unwrapResponse(value) {
  if (!value || typeof value !== "object") {
    return value;
  }
  if (value.status === "error" || value.ok === false) {
    throw new Error(value.message || value.error || "操作失败");
  }
  if (value.status === "success" && "data" in value) {
    return value.data;
  }
  return value;
}

function requireBridge() {
  const bridge = getBridge();
  if (!bridge) {
    throw new Error("AstrBot Page Bridge 未加载，请从 AstrBot Dashboard 打开本页面");
  }
  return bridge;
}

/** GET a plugin endpoint with query params. */
export async function apiGet(endpoint, params) {
  return unwrapResponse(await requireBridge().apiGet(endpoint, params || {}));
}

/** POST a JSON body to a plugin endpoint. */
export async function apiPost(endpoint, body) {
  return unwrapResponse(await requireBridge().apiPost(endpoint, body || {}));
}

/** Multipart upload; the host always names the form field "file". */
export async function apiUpload(endpoint, file) {
  return unwrapResponse(await requireBridge().upload(endpoint, file));
}

/**
 * Streams a file straight to the browser download manager. Replaces the old
 * base64 + atob path, which decoded megabyte payloads one character at a time
 * on the main thread.
 */
export async function apiDownload(endpoint, params, filename) {
  return requireBridge().download(endpoint, params || {}, filename);
}

/**
 * The host only guarantees context.isDark, but different dashboard versions
 * have also exposed theme/colorScheme/appearance/themeMode/darkMode. Sniff all
 * of them, then fall back to the OS preference.
 */
export function detectHostDark(context) {
  const ctx = context || {};
  const probe = [ctx.theme, ctx.colorScheme, ctx.appearance, ctx.themeMode, ctx.darkMode]
    .filter((item) => item !== null && item !== undefined)
    .map((item) => String(item).toLowerCase())
    .join(" ");
  const combined = probe + " " + (ctx.isDark === true ? "true" : ctx.isDark === false ? "false" : "");
  if (/dark|true/.test(combined)) {
    return true;
  }
  if (/light|false/.test(combined)) {
    return false;
  }
  return Boolean(window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
}
