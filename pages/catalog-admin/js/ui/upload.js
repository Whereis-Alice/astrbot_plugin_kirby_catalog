/**
 * Image upload helpers.
 *
 * Validation happens client-side first so a 40 MB PSD never leaves the browser;
 * drag-and-drop is checked with the same rules as the file picker, which the
 * previous build skipped entirely.
 */

import { formatBytes } from "../core/format.js";

export const MAX_UPLOAD_BYTES = 16 * 1024 * 1024;

const ALLOWED_TYPES = Object.freeze([
  "image/png",
  "image/jpeg",
  "image/gif",
  "image/bmp",
  "image/webp",
]);

const ALLOWED_EXTENSIONS = Object.freeze([".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"]);

export const IMAGE_ACCEPT = ALLOWED_TYPES.join(",");

/**
 * @param {File} file
 * @returns {{ok: boolean, message?: string}}
 */
export function validateImageFile(file) {
  if (!file) {
    return { ok: false, message: "没有选择文件" };
  }
  const name = String(file.name || "").toLowerCase();
  const typeOk = ALLOWED_TYPES.includes(String(file.type || "").toLowerCase());
  const extensionOk = ALLOWED_EXTENSIONS.some((extension) => name.endsWith(extension));
  if (!typeOk && !extensionOk) {
    return { ok: false, message: "仅支持 PNG、JPG、GIF、BMP 或 WebP 图片" };
  }
  if (file.size > MAX_UPLOAD_BYTES) {
    return {
      ok: false,
      message: "图片体积 " + formatBytes(file.size) + "，超过 " + formatBytes(MAX_UPLOAD_BYTES) + " 上限",
    };
  }
  if (file.size === 0) {
    return { ok: false, message: "图片内容为空" };
  }
  return { ok: true };
}

/**
 * Wires a click/keyboard/drag-and-drop upload target.
 *
 * @param {{zone: HTMLElement, input: HTMLInputElement,
 *          onFile: (file: File) => void, onReject: (message: string) => void}} options
 * @returns {() => void} Detach function.
 */
export function bindUploadZone(options) {
  const zone = options.zone;
  const input = options.input;

  const accept = (file) => {
    const result = validateImageFile(file);
    if (!result.ok) {
      options.onReject(result.message);
      return;
    }
    options.onFile(file);
  };

  const onZoneClick = () => input.click();
  const onInputChange = () => {
    const file = input.files && input.files[0];
    if (file) {
      accept(file);
    }
    input.value = "";
  };
  const onDragOver = (event) => {
    event.preventDefault();
    zone.classList.add("is-dragging");
  };
  const onDragLeave = () => zone.classList.remove("is-dragging");
  const onDrop = (event) => {
    event.preventDefault();
    zone.classList.remove("is-dragging");
    const file = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
    if (file) {
      accept(file);
    }
  };

  zone.addEventListener("click", onZoneClick);
  input.addEventListener("change", onInputChange);
  zone.addEventListener("dragover", onDragOver);
  zone.addEventListener("dragleave", onDragLeave);
  zone.addEventListener("drop", onDrop);

  return () => {
    zone.removeEventListener("click", onZoneClick);
    input.removeEventListener("change", onInputChange);
    zone.removeEventListener("dragover", onDragOver);
    zone.removeEventListener("dragleave", onDragLeave);
    zone.removeEventListener("drop", onDrop);
  };
}

/**
 * Opens a one-shot file picker and resolves with a validated file.
 *
 * @returns {Promise<File|null>}
 */
export function pickImageFile() {
  return new Promise((resolve) => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = IMAGE_ACCEPT;
    input.hidden = true;
    document.body.appendChild(input);
    let settled = false;
    const finish = (value) => {
      if (settled) {
        return;
      }
      settled = true;
      input.remove();
      resolve(value);
    };
    input.addEventListener("change", () => finish((input.files && input.files[0]) || null));
    input.addEventListener("cancel", () => finish(null));
    input.click();
  });
}
