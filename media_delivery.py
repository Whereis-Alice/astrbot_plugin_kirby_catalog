from __future__ import annotations

import hashlib
import re
import shutil
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

from PIL import Image, ImageOps


_CLEANUP_LOCK = threading.RLock()
_LAST_CLEANUP_AT: dict[Path, float] = {}


@dataclass(frozen=True)
class ImageMetrics:
    path: Path
    width: int
    height: int
    byte_size: int
    image_format: str

    @property
    def megapixels(self) -> float:
        return self.width * self.height / 1_000_000


def inspect_image(path: Path | str) -> ImageMetrics | None:
    image_path = Path(path)
    try:
        with Image.open(image_path) as image:
            width, height = image.size
            image_format = str(image.format or image_path.suffix.lstrip(".")).upper()
        return ImageMetrics(
            path=image_path,
            width=int(width),
            height=int(height),
            byte_size=image_path.stat().st_size,
            image_format=image_format,
        )
    except (OSError, ValueError):
        return None


def image_limit_reasons(
    metrics: ImageMetrics,
    *,
    max_width: int = 0,
    max_height: int = 0,
    max_megapixels: float = 0,
    max_bytes: int = 0,
) -> list[str]:
    reasons: list[str] = []
    if max_width > 0 and metrics.width > max_width:
        reasons.append(f"width={metrics.width}>{max_width}")
    if max_height > 0 and metrics.height > max_height:
        reasons.append(f"height={metrics.height}>{max_height}")
    if max_megapixels > 0 and metrics.megapixels > max_megapixels:
        reasons.append(
            f"megapixels={metrics.megapixels:.2f}>{max_megapixels:.2f}"
        )
    if max_bytes > 0 and metrics.byte_size > max_bytes:
        reasons.append(f"bytes={metrics.byte_size}>{max_bytes}")
    return reasons


def local_path_from_image_file(value: str, explicit_path: str = "") -> Path | None:
    if explicit_path:
        path = Path(explicit_path)
        if path.is_file():
            return path.resolve()
    raw = str(value or "").strip()
    if not raw or raw.startswith(("http://", "https://", "base64://")):
        return None
    if raw.startswith("file://"):
        parsed = urlparse(raw)
        decoded = unquote(parsed.path)
        if re.match(r"^/[A-Za-z]:/", decoded):
            decoded = decoded[1:]
        raw = decoded
    path = Path(raw)
    return path.resolve() if path.is_file() else None


def file_uri(path: Path | str) -> str:
    value = str(path).replace("\\", "/")
    if re.match(r"^[A-Za-z]:/", value):
        return f"file:///{quote(value, safe='/:')}"
    if not value.startswith("/"):
        value = f"/{value}"
    return f"file://{quote(value, safe='/:')}"


def normalise_jpeg(
    source: Path | str,
    output_dir: Path | str,
    *,
    quality: int = 92,
    prefix: str = "kirby-normalized",
) -> Path:
    source_path = Path(source)
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    output_path = output_root / f"{prefix}-{uuid.uuid4().hex}.jpg"
    with Image.open(source_path) as image:
        image.seek(0)
        image = ImageOps.exif_transpose(image).convert("RGB")
        image.save(
            output_path,
            format="JPEG",
            quality=max(60, min(98, int(quality))),
            optimize=True,
            progressive=False,
            subsampling=0,
        )
    return output_path


def prepare_image_for_delivery(
    source: Path | str,
    output_dir: Path | str,
    *,
    max_width: int = 2160,
    max_height: int = 8000,
    max_megapixels: float = 18,
    max_bytes: int = 8 * 1024 * 1024,
    normalize_jpeg_enabled: bool = True,
    jpeg_quality: int = 92,
) -> Path:
    source_path = Path(source)
    metrics = inspect_image(source_path)
    if metrics is None:
        return source_path
    reasons = image_limit_reasons(
        metrics,
        max_width=max_width,
        max_height=max_height,
        max_megapixels=max_megapixels,
        max_bytes=max_bytes,
    )
    is_jpeg = metrics.image_format in {"JPEG", "JPG"}
    if not reasons and not (normalize_jpeg_enabled and is_jpeg):
        return source_path

    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    stat = source_path.stat()
    cache_key = hashlib.sha256(
        "|".join(
            (
                str(source_path.resolve()),
                str(stat.st_size),
                str(stat.st_mtime_ns),
                str(max_width),
                str(max_height),
                str(max_megapixels),
                str(max_bytes),
                str(bool(normalize_jpeg_enabled)),
                str(jpeg_quality),
            )
        ).encode("utf-8")
    ).hexdigest()[:24]
    output_path = output_root / f"kirby-delivery-{cache_key}.jpg"
    if output_path.is_file():
        cached = inspect_image(output_path)
        if cached and not image_limit_reasons(
            cached,
            max_width=max_width,
            max_height=max_height,
            max_megapixels=max_megapixels,
            max_bytes=max_bytes,
        ):
            try:
                output_path.touch()
            except OSError:
                pass
            return output_path

    with Image.open(source_path) as image:
        image.seek(0)
        prepared = ImageOps.exif_transpose(image).convert("RGBA")
        scale = 1.0
        if max_width > 0:
            scale = min(scale, max_width / prepared.width)
        if max_height > 0:
            scale = min(scale, max_height / prepared.height)
        if max_megapixels > 0:
            scale = min(
                scale,
                (max_megapixels * 1_000_000 / (prepared.width * prepared.height))
                ** 0.5,
            )
        if scale < 1:
            prepared = prepared.resize(
                (
                    max(1, int(prepared.width * scale)),
                    max(1, int(prepared.height * scale)),
                ),
                Image.Resampling.LANCZOS,
            )
        background = Image.new("RGB", prepared.size, "white")
        background.paste(prepared, mask=prepared.getchannel("A"))

    quality = max(60, min(98, int(jpeg_quality)))
    while True:
        background.save(
            output_path,
            format="JPEG",
            quality=quality,
            optimize=True,
            progressive=False,
            subsampling=0,
        )
        if max_bytes <= 0 or output_path.stat().st_size <= max_bytes:
            break
        if quality > 68:
            quality = max(68, quality - 8)
            continue
        if background.width <= 320 or background.height <= 320:
            break
        background = background.resize(
            (
                max(1, int(background.width * 0.85)),
                max(1, int(background.height * 0.85)),
            ),
            Image.Resampling.LANCZOS,
        )
    return output_path


def stage_local_image(
    source: Path | str,
    shared_directory: Path | str,
    *,
    napcat_directory: str = "",
    normalize_jpeg_enabled: bool = True,
    jpeg_quality: int = 92,
    retention_seconds: float = 1800,
    cleanup_interval_seconds: float = 300,
) -> tuple[Path, str]:
    source_path = Path(source)
    shared_root = Path(shared_directory) / "astrbot_plugin_kirby_catalog"
    shared_root.mkdir(parents=True, exist_ok=True)
    cleanup_staged_media_if_due(
        shared_root,
        retention_seconds=retention_seconds,
        min_interval_seconds=cleanup_interval_seconds,
    )

    metrics = inspect_image(source_path)
    is_jpeg = bool(
        metrics and metrics.image_format in {"JPEG", "JPG"}
    ) or source_path.suffix.casefold() in {".jpg", ".jpeg"}
    stat = source_path.stat()
    stage_key = hashlib.sha256(
        "|".join(
            (
                str(source_path.resolve()),
                str(stat.st_size),
                str(stat.st_mtime_ns),
                str(bool(normalize_jpeg_enabled and is_jpeg)),
                str(int(jpeg_quality)),
            )
        ).encode("utf-8")
    ).hexdigest()[:24]
    suffix = ".jpg" if normalize_jpeg_enabled and is_jpeg else (
        source_path.suffix.casefold() or ".img"
    )
    staged_path = shared_root / f"kirby-stage-{stage_key}{suffix}"
    if staged_path.is_file():
        try:
            staged_path.touch()
        except OSError:
            pass
    else:
        if normalize_jpeg_enabled and is_jpeg:
            temporary_path = normalise_jpeg(
                source_path,
                shared_root,
                quality=jpeg_quality,
                prefix=f"kirby-stage-{stage_key}-tmp",
            )
        else:
            temporary_path = shared_root / (
                f"kirby-stage-{stage_key}-tmp-{uuid.uuid4().hex}{suffix}"
            )
            shutil.copy2(source_path, temporary_path)
        try:
            temporary_path.replace(staged_path)
        except OSError:
            if not staged_path.is_file():
                raise
            temporary_path.unlink(missing_ok=True)

    if napcat_directory:
        napcat_path = (
            str(napcat_directory).rstrip("/\\")
            + "/astrbot_plugin_kirby_catalog/"
            + staged_path.name
        )
    else:
        napcat_path = str(staged_path)
    return staged_path, file_uri(napcat_path)


def cleanup_staged_media(
    directory: Path | str,
    *,
    retention_seconds: float = 1800,
) -> int:
    root = Path(directory)
    if not root.is_dir():
        return 0
    cutoff = time.time() - max(60.0, float(retention_seconds))
    removed = 0
    for path in root.glob("kirby-*"):
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def cleanup_staged_media_if_due(
    directory: Path | str,
    *,
    retention_seconds: float = 1800,
    min_interval_seconds: float = 300,
) -> int:
    """Remove expired delivery files at a bounded cadence instead of per message."""

    root = Path(directory)
    try:
        key = root.resolve()
    except OSError:
        key = root
    now = time.monotonic()
    interval = max(0.0, float(min_interval_seconds))
    with _CLEANUP_LOCK:
        last_run = _LAST_CLEANUP_AT.get(key)
        if last_run is not None and interval > 0 and now - last_run < interval:
            return 0
        _LAST_CLEANUP_AT[key] = now
    return cleanup_staged_media(root, retention_seconds=retention_seconds)
