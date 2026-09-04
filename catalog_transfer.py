"""素材与数据的导入导出服务。

这个模块只做三件事：

1. **导出**：把图鉴资料、名称库、百科序号、群组进度、操作记录打包成 JSON/CSV，
   把 ``config/`` 与 ``webui/`` 打包成配置整包，把 ``img/allies/`` 打包成素材图片包。
2. **预检**：上传的文件先落到 ``webui/imports/`` 暂存区，只解析、不写入，
   算出「新增 / 更新 / 未变 / 跳过」的差异摘要给前端确认。
3. **应用**：用户确认后才真正落盘，并写一条审计记录。

为什么要这样设计（可行性）：

* 本插件的文本数据量在 1.5–4 MB 量级，可以整块序列化，但素材图片有一千多张、
  总体积可能到几百 MB，一次性塞进内存或塞进一个 HTTP 响应都不现实。
  所以导出统一走 :class:`TransferExport` 的分块迭代器，压缩包先落磁盘再流式吐出，
  素材包再按 64 MB 一卷切分，让浏览器可以分卷下载。
* 导入必须是两阶段的。直接「上传即写入」会让用户在看不到影响面的情况下
  覆盖掉上千条数据；先暂存 + 预检 + 确认，才有回头路（``discard``）。
* 素材导入会写入上千个文件，如果每写一张就 ``store.refresh()`` 重扫整个目录，
  复杂度是 O(n²)。所以整批写完只刷新一次。
"""

from __future__ import annotations

import csv
import json
import re
import shutil
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO, StringIO
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

from PIL import Image

from .catalog_core import (
    NON_GROUP_CONFIG_FILENAMES,
    CatalogStore,
    _atomic_write_bytes,
    _atomic_write_json,
    _read_json,
    normalise_group_config,
)
from .terminology import CSV_FIELDS as TERMINOLOGY_CSV_FIELDS
from .terminology import KirbyTerminologyStore, TerminologyError
from .wiki_index import WikiIndexStore

PLUGIN_NAME = "astrbot_plugin_kirby_catalog"
SCHEMA_VERSION = 1

# 单个文本文件（JSON/CSV）的上传上限：名称库 CSV 实测 0.6 MB，图鉴 JSON 3.4 MB，
# 16 MB 已经留了四倍余量，同时和 WebUI 图片上传上限保持一致。
TRANSFER_MAX_TEXT_BYTES = 16 * 1024 * 1024
# 压缩包上传上限。素材包按 64 MB 一卷导出，96 MB 允许用户自己重新打包时略微超出。
TRANSFER_MAX_ARCHIVE_BYTES = 96 * 1024 * 1024
# 素材导出的分卷体积。
ASSET_VOLUME_BYTES = 64 * 1024 * 1024
# 解压侧的防御：成员数量、解压后总体积、单张图片体积。
MAX_ARCHIVE_MEMBERS = 5000
MAX_EXTRACTED_BYTES = 512 * 1024 * 1024
MAX_SINGLE_ASSET_BYTES = 12 * 1024 * 1024
# 图鉴导入一次最多改动多少条，避免误传整库时把审计记录冲爆。
MAX_CATALOG_CHANGES = 600
MAX_DESCRIPTION_CHARS = 30000
# 暂存区的生存时间与总容量。
STAGE_TTL_SECONDS = 6 * 3600
STAGE_CAPACITY_BYTES = 512 * 1024 * 1024
EXPORT_TTL_SECONDS = 3600

CSV_LIST_SEPARATOR = " | "
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"})
ARCHIVE_METADATA_NAMES = frozenset({"manifest.json"})
TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")
DATA_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

CATALOG_FIELDS: Tuple[str, ...] = (
    "id",
    "name",
    "source",
    "filename",
    "entry_key",
    "page_title",
    "variant_key",
    "catalog_kind",
    "asset_set",
    "debut_work",
    "debut_year",
    "kind",
    "pageid",
    "aliases",
    "name_zh",
    "name_en",
    "display_name",
    "display_work",
    "description",
    "description_origin",
    "description_updated_at",
    "description_updated_by",
    "has_asset",
)

WIKI_INDEX_FIELDS: Tuple[str, ...] = (
    "site",
    "key",
    "number",
    "target",
    "enabled",
    "label_zh",
    "label_en",
    "label_ja",
    "context",
    "catalog_id",
    "default_number",
    "default_target",
    "default_enabled",
    "origin",
    "updated_at",
    "updated_by",
)

GROUP_FIELDS: Tuple[str, ...] = (
    "group_id",
    "user_id",
    "nickname",
    "current_ally",
    "current_date",
    "no_new_count",
    "unlocked_count",
    "unlocked",
)

AUDIT_FIELDS: Tuple[str, ...] = (
    "timestamp",
    "username",
    "action",
    "target",
    "summary",
    "id",
)

CSV_FIELD_MAP: Dict[str, Tuple[str, ...]] = {
    "catalog": CATALOG_FIELDS,
    "terminology": tuple(TERMINOLOGY_CSV_FIELDS),
    "wiki-index": WIKI_INDEX_FIELDS,
    "groups": GROUP_FIELDS,
    "audit": AUDIT_FIELDS,
}

CSV_LIST_FIELDS: Dict[str, Tuple[str, ...]] = {
    "catalog": ("aliases",),
    "groups": ("unlocked",),
}

SUMMARY_KEYS: Tuple[str, ...] = (
    "total",
    "added",
    "updated",
    "unchanged",
    "removed",
    "skipped",
)

DATASET_SPECS: Dict[str, Dict[str, Any]] = {
    "catalog": {
        "label": "图鉴资料",
        "icon": "library-big",
        "hint": "角色名称、所属作品与简介；导入只会更新已存在的素材。",
        "formats": ("json", "csv"),
        "modes": ("merge",),
        "scopes": ("merged", "overrides"),
    },
    "terminology": {
        "label": "名称库",
        "icon": "languages",
        "hint": "中日英对照的规范译名与别名。",
        "formats": ("json", "csv"),
        "modes": ("merge", "replace"),
        "scopes": ("merged", "overrides"),
    },
    "wiki-index": {
        "label": "百科序号",
        "icon": "list-ordered",
        "hint": "三个百科站点的记录集序号与查询目标。",
        "formats": ("json", "csv"),
        "modes": ("merge", "replace"),
        "scopes": ("merged", "overrides"),
    },
    "groups": {
        "label": "群组进度",
        "icon": "users-round",
        "hint": "每个群里成员的当前盟友与解锁记录。",
        "formats": ("json", "csv"),
        "modes": ("merge", "replace"),
        "scopes": ("merged",),
    },
    "audit": {
        "label": "操作记录",
        "icon": "file-clock",
        "hint": "最近 500 条后台操作日志，只支持导出。",
        "formats": ("json", "csv"),
        "modes": (),
        "scopes": ("merged",),
    },
    "bundle": {
        "label": "配置整包",
        "icon": "database",
        "hint": "catalog.json、config/ 与 webui/ 的完整快照，换服务器时用这个。",
        "formats": ("zip",),
        "modes": ("merge", "replace"),
        "scopes": ("merged",),
    },
    "assets": {
        "label": "素材图片",
        "icon": "image",
        "hint": "img/allies/ 下的全部图片，体积大，按分卷下载。",
        "formats": ("zip",),
        "modes": ("merge",),
        "scopes": ("merged",),
    },
}

MODE_LABELS: Dict[str, str] = {"merge": "合并导入", "replace": "整体替换"}
SCOPE_LABELS: Dict[str, str] = {"merged": "全部数据", "overrides": "仅自定义"}
BUNDLE_KIND_LABELS: Dict[str, str] = {
    "catalog": "图鉴索引",
    "config": "自定义配置",
    "group": "群组进度",
    "webui": "运行记录",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _bool(value: Any, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    lowered = _text(value).lower()
    if lowered in {"1", "true", "yes", "y", "on", "是", "开", "启用"}:
        return True
    if lowered in {"0", "false", "no", "n", "off", "否", "关", "停用"}:
        return False
    return default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        parts = re.split(r"[\r\n|]", value)
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        parts = [str(item) for item in value]
    else:
        parts = [str(value)]
    seen: List[str] = []
    for part in parts:
        cleaned = part.strip()
        if cleaned and cleaned not in seen:
            seen.append(cleaned)
    return seen


def _join(values: Any) -> str:
    return CSV_LIST_SEPARATOR.join(_list(values))


def _csv_value(value: Any) -> str:
    """把任意字段压成一个 CSV 单元格。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, Mapping):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if isinstance(value, (list, tuple, set)):
        return _join(value)
    return str(value)


def _slug(value: Any) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "", _text(value))[:32]


def _empty_summary() -> Dict[str, int]:
    return {key: 0 for key in SUMMARY_KEYS}


def _summary(raw: Mapping[str, Any]) -> Dict[str, int]:
    """把各个 store 返回的摘要统一压成同一组键。"""
    result = _empty_summary()
    for key in SUMMARY_KEYS:
        result[key] = max(0, _int(raw.get(key), 0))
    return result


def _safe_member(name: str) -> str:
    """挡掉 zip-slip：拒绝绝对路径、盘符与 ``..`` 段。"""
    cleaned = _text(name).replace("\\", "/")
    if not cleaned or cleaned.endswith("/"):
        return ""
    if cleaned.startswith("/") or re.match(r"^[A-Za-z]:", cleaned):
        return ""
    parts = [part for part in cleaned.split("/") if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        return ""
    return "/".join(parts)


def _unlocked_from_row(value: Any) -> List[Dict[str, str]]:
    """解析解锁记录：既吃 JSON 的对象数组，也吃 CSV 的 ``名字@日期`` 串。"""
    records: List[Dict[str, str]] = []
    if isinstance(value, Mapping):
        candidates: List[Any] = [value]
    elif isinstance(value, str):
        candidates = _list(value)
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        candidates = list(value)
    elif value is None:
        candidates = []
    else:
        candidates = [value]
    for item in candidates:
        if isinstance(item, Mapping):
            filename = _text(item.get("ally_filename") or item.get("wife_name"))
            date = _text(item.get("unlock_date") or item.get("date"))
        else:
            head, _, tail = _text(item).rpartition("@")
            filename = head or _text(item)
            date = tail if head else ""
        if not filename:
            continue
        records.append({"ally_filename": Path(filename).name, "unlock_date": date})
    return records


@dataclass
class TransferExport:
    """一次导出的结果：小文件直接给 bytes，压缩包给磁盘路径。"""

    filename: str
    mime_type: str
    total_bytes: int
    data: Optional[bytes] = None
    path: Optional[Path] = None

    def chunks(self, chunk_size: int = 256 * 1024) -> Iterator[bytes]:
        if self.data is not None:
            for offset in range(0, len(self.data), chunk_size):
                yield self.data[offset : offset + chunk_size]
            return
        if self.path is None:
            return
        try:
            with self.path.open("rb") as handle:
                while True:
                    block = handle.read(chunk_size)
                    if not block:
                        break
                    yield block
        finally:
            self.path.unlink(missing_ok=True)


class CatalogTransferService:
    """把图鉴数据搬进搬出的唯一入口。

    导出走 :meth:`export`，导入固定是 :meth:`begin_upload` → :meth:`stage`
    → :meth:`apply` / :meth:`discard` 三步。之所以不做「上传即导入」，是因为
    一次误操作可能重写上千条数据；分两步以后前端可以先把差异摆给用户看。

    暂存文件落在 ``webui/imports/``，导出的临时压缩包落在 ``webui/exports/``；
    两个目录都会在每次操作前按 TTL 清理，所以哪怕进程被强杀也不会攒垃圾。
    上传上限：文本 16 MB、压缩包 96 MB 的素材包。
    """

    def __init__(
        self,
        store: CatalogStore,
        terminology: Optional[KirbyTerminologyStore] = None,
        wiki_index: Optional[WikiIndexStore] = None,
    ) -> None:
        self.store = store
        self.terminology = terminology
        self.wiki_index = wiki_index
        self.stage_dir = store.webui_dir / "imports"
        self.export_dir = store.webui_dir / "exports"
        self.stage_dir.mkdir(parents=True, exist_ok=True)
        self.export_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._metrics_cache: Optional[Tuple[float, Dict[str, Any]]] = None
        self.cleanup()

    # ------------------------------------------------------------------ 校验

    @staticmethod
    def _dataset_name(value: Any) -> str:
        name = _text(value).lower()
        if name not in DATASET_SPECS:
            raise ValueError("不支持的数据类型")
        return name

    @staticmethod
    def _format_name(dataset: str, value: Any) -> str:
        formats = DATASET_SPECS[dataset]["formats"]
        name = _text(value).lower() or formats[0]
        if name not in formats:
            raise ValueError("不支持的导出格式")
        return name

    @staticmethod
    def _scope_name(dataset: str, value: Any) -> str:
        scopes = DATASET_SPECS[dataset]["scopes"]
        name = _text(value).lower() or scopes[0]
        if name not in scopes:
            raise ValueError("不支持的导出范围")
        return name

    @staticmethod
    def _mode_name(dataset: str, value: Any) -> str:
        modes = DATASET_SPECS[dataset]["modes"]
        if not modes:
            raise ValueError("该数据只支持导出")
        name = _text(value).lower() or modes[0]
        if name not in modes:
            raise ValueError("不支持的导入方式")
        return name

    @staticmethod
    def _suffix_format(filename: str) -> str:
        suffix = Path(_text(filename)).suffix.lower()
        if suffix == ".json":
            return "json"
        if suffix == ".csv":
            return "csv"
        if suffix == ".zip":
            return "zip"
        raise ValueError("只支持 .json、.csv 或 .zip 文件")

    @staticmethod
    def _guess_dataset(filename: str) -> str:
        stem = Path(_text(filename)).stem.lower()
        if stem.startswith("kirby-"):
            stem = stem[6:]
        best = ""
        for name in DATASET_SPECS:
            if stem.startswith(name) and len(name) > len(best):
                best = name
        return best

    def _require_terminology(self) -> KirbyTerminologyStore:
        if self.terminology is None:
            raise ValueError("名称库尚未启用")
        return self.terminology

    def _require_wiki_index(self) -> WikiIndexStore:
        if self.wiki_index is None:
            raise ValueError("百科序号尚未启用")
        return self.wiki_index

    def invalidate(self) -> None:
        """丢掉体积统计缓存，下一次 manifest 会重新算。"""
        with self._lock:
            self._metrics_cache = None

    # ------------------------------------------------------------------ 总览

    def manifest(self) -> Dict[str, Any]:
        """给前端的总览：每类数据有多少条、多大、支持哪些格式和导入方式。"""
        metrics = self._metrics()
        datasets: List[Dict[str, Any]] = []
        for name, spec in DATASET_SPECS.items():
            info = metrics.get(name) or {}
            modes = list(spec["modes"])
            scopes = list(spec["scopes"])
            datasets.append(
                {
                    "name": name,
                    "label": spec["label"],
                    "icon": spec["icon"],
                    "hint": spec["hint"],
                    "formats": list(spec["formats"]),
                    "modes": modes,
                    "mode_labels": [MODE_LABELS[mode] for mode in modes],
                    "scopes": scopes,
                    "scope_labels": [SCOPE_LABELS[scope] for scope in scopes],
                    "count": _int(info.get("count"), 0),
                    "overrides": _int(info.get("overrides"), 0),
                    "bytes": _int(info.get("bytes"), 0),
                    "volumes": max(1, _int(info.get("volumes"), 1)),
                    "ready": bool(info.get("ready", True)),
                    "note": _text(info.get("note")),
                }
            )
        return {
            "generated_at": _now(),
            "schema_version": SCHEMA_VERSION,
            "datasets": datasets,
            "assets": metrics.get("assets_detail") or {},
            "pending": self.pending(),
            "limits": {
                "text_bytes": TRANSFER_MAX_TEXT_BYTES,
                "archive_bytes": TRANSFER_MAX_ARCHIVE_BYTES,
                "volume_bytes": ASSET_VOLUME_BYTES,
                "single_asset_bytes": MAX_SINGLE_ASSET_BYTES,
                "catalog_changes": MAX_CATALOG_CHANGES,
                "stage_ttl": STAGE_TTL_SECONDS,
            },
        }

    def _metrics(self) -> Dict[str, Any]:
        """统计结果缓存 15 秒：算一次要序列化 4 MB 文本 + stat 上千个文件。"""
        with self._lock:
            cached = self._metrics_cache
        if cached is not None and time.time() - cached[0] < 15.0:
            return cached[1]
        metrics = self._collect_metrics()
        with self._lock:
            self._metrics_cache = (time.time(), metrics)
        return metrics

    def _collect_metrics(self) -> Dict[str, Any]:
        metrics: Dict[str, Any] = {}
        for name in ("catalog", "terminology", "wiki-index", "groups", "audit"):
            try:
                rows = self._rows(name, "merged")
                overrides: List[Dict[str, Any]] = []
                if "overrides" in DATASET_SPECS[name]["scopes"]:
                    overrides = self._rows(name, "overrides")
                metrics[name] = {
                    "count": len(rows),
                    "overrides": len(overrides),
                    "bytes": len(self._json_bytes(name, "merged", rows)),
                    "ready": True,
                    "note": "",
                }
            except Exception as exc:  # noqa: BLE001 - 统计失败不该拖垮整个页面
                metrics[name] = {
                    "count": 0,
                    "overrides": 0,
                    "bytes": 0,
                    "ready": False,
                    "note": f"读取失败：{exc}",
                }
        bundle_files = self._bundle_files()
        metrics["bundle"] = {
            "count": len(bundle_files),
            "overrides": 0,
            "bytes": sum(size for _, _, size in bundle_files),
            "ready": bool(bundle_files),
            "note": "" if bundle_files else "还没有可备份的配置文件",
        }
        stats = self._asset_stats()
        metrics["assets"] = {
            "count": stats["count"],
            "overrides": 0,
            "bytes": stats["bytes"],
            "volumes": stats["volumes"],
            "ready": stats["count"] > 0,
            "note": "" if stats["count"] else "素材目录是空的",
        }
        metrics["assets_detail"] = stats
        return metrics

    # ------------------------------------------------------------------ 导出

    def export(
        self,
        dataset: Any,
        format_name: Any = "",
        *,
        scope: Any = "",
        group_id: Any = "",
        volume: Any = 1,
    ) -> TransferExport:
        """导出一份数据；压缩包会先落到 ``webui/exports/`` 再流式吐出。"""
        name = self._dataset_name(dataset)
        fmt = self._format_name(name, format_name)
        scope_name = self._scope_name(name, scope)
        self.cleanup()
        if name == "assets":
            return self._export_assets(_int(volume, 1))
        if name == "bundle":
            return self._export_bundle()
        rows = self._rows(name, scope_name, group_id=_text(group_id))
        suffix = _slug(group_id) or (scope_name if scope_name != "merged" else "")
        parts = ["kirby", name]
        if suffix:
            parts.append(suffix)
        parts.append(_stamp())
        filename = "-".join(parts) + "." + fmt
        if fmt == "csv":
            payload = self._csv_bytes(name, rows)
            mime = "text/csv; charset=utf-8"
        else:
            payload = self._json_bytes(name, scope_name, rows)
            mime = "application/json; charset=utf-8"
        return TransferExport(
            filename=filename,
            mime_type=mime,
            total_bytes=len(payload),
            data=payload,
        )

    def _rows(
        self,
        dataset: str,
        scope: str,
        *,
        group_id: str = "",
    ) -> List[Dict[str, Any]]:
        if dataset == "catalog":
            return self._catalog_rows(scope)
        if dataset == "terminology":
            return self._terminology_rows(scope)
        if dataset == "wiki-index":
            return self._wiki_index_rows(scope)
        if dataset == "groups":
            return self._group_rows(group_id)
        if dataset == "audit":
            return self._audit_rows()
        raise ValueError("该数据不支持按行导出")

    def _json_bytes(
        self,
        dataset: str,
        scope: str,
        rows: Sequence[Mapping[str, Any]],
    ) -> bytes:
        """写成自描述的 JSON：带上 dataset 头，导入时能识别传错文件。"""
        payload = {
            "plugin": PLUGIN_NAME,
            "dataset": dataset,
            "scope": scope,
            "schema_version": SCHEMA_VERSION,
            "generated_at": _now(),
            "count": len(rows),
            "items": [dict(row) for row in rows],
        }
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        return text.encode("utf-8")

    def _csv_bytes(self, dataset: str, rows: Sequence[Mapping[str, Any]]) -> bytes:
        """写成带 BOM 的 CSV，Excel 直接双击不会乱码。"""
        fields = CSV_FIELD_MAP.get(dataset)
        if not fields:
            raise ValueError("该数据不支持 CSV 导出")
        buffer = StringIO()
        writer = csv.DictWriter(
            buffer,
            fieldnames=list(fields),
            extrasaction="ignore",
            lineterminator="\r\n",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _csv_value(row.get(key)) for key in fields})
        return b"\xef\xbb\xbf" + buffer.getvalue().encode("utf-8")

    def _catalog_rows(self, scope: str) -> List[Dict[str, Any]]:
        store = self.store
        rows: List[Dict[str, Any]] = []
        overrides_only = scope == "overrides"
        for entry in store.entries():
            profile = store.profile_for(entry)
            origin = _text(profile.get("description_origin")) or "missing"
            kind = _text(entry.get("kind"))
            if overrides_only and origin != "override" and kind != "manual":
                continue
            rows.append(
                {
                    "id": _int(entry.get("id"), 0),
                    "name": _text(entry.get("name")),
                    "source": _text(entry.get("source")),
                    "filename": _text(entry.get("filename")),
                    "entry_key": _text(entry.get("entry_key")),
                    "page_title": _text(entry.get("page_title")),
                    "variant_key": _text(entry.get("variant_key")),
                    "catalog_kind": _text(entry.get("catalog_kind")),
                    "asset_set": _text(entry.get("asset_set")),
                    "debut_work": _text(entry.get("debut_work")),
                    "debut_year": _text(entry.get("debut_year")),
                    "kind": kind,
                    "pageid": _text(entry.get("pageid")),
                    "aliases": _list(entry.get("aliases")),
                    "name_zh": _text(profile.get("name_zh")),
                    "name_en": _text(profile.get("name_en")),
                    "display_name": _text(profile.get("display_name")),
                    "display_work": _text(profile.get("display_work")),
                    "description": _text(profile.get("description_zh")),
                    "description_origin": origin,
                    "description_updated_at": _text(
                        profile.get("description_updated_at")
                    ),
                    "description_updated_by": _text(
                        profile.get("description_updated_by")
                    ),
                    "has_asset": store.asset_path(entry) is not None,
                }
            )
        return rows

    def _terminology_rows(self, scope: str) -> List[Dict[str, Any]]:
        store = self._require_terminology()
        overrides_only = scope == "overrides"
        rows: List[Dict[str, Any]] = []
        for entry in store.entries():
            origin = store.origin(entry.term_id)
            if overrides_only and origin not in {"override", "custom"}:
                continue
            row = dict(entry.to_mapping())
            row["origin"] = origin
            rows.append(row)
        return rows

    def _wiki_index_rows(self, scope: str) -> List[Dict[str, Any]]:
        store = self._require_wiki_index()
        rows: List[Dict[str, Any]] = []
        for row in store.export_rows(overrides_only=scope == "overrides"):
            rows.append(
                {
                    "site": _text(row.get("site")),
                    "key": _text(row.get("key")),
                    "number": _int(row.get("number"), 0),
                    "target": _text(row.get("target")),
                    "enabled": bool(row.get("enabled")),
                    "label_zh": _text(row.get("label_zh")),
                    "label_en": _text(row.get("label_en")),
                    "label_ja": _text(row.get("label_ja")),
                    "context": _text(row.get("context")),
                    "catalog_id": _text(row.get("catalog_id")),
                    "default_number": _int(row.get("default_number"), 0),
                    "default_target": _text(row.get("default_target")),
                    "default_enabled": bool(row.get("default_enabled")),
                    "origin": _text(row.get("origin")) or "bundled",
                    "updated_at": _text(row.get("updated_at")),
                    "updated_by": _text(row.get("updated_by")),
                }
            )
        return rows

    def _group_rows(self, group_id: str = "") -> List[Dict[str, Any]]:
        store = self.store
        wanted = _text(group_id)
        group_ids = [wanted] if wanted else list(store.group_ids())
        rows: List[Dict[str, Any]] = []
        for gid in group_ids:
            config = store.load_group(gid)
            for user_id, payload in sorted(config.items()):
                current = payload.get("current") or {}
                unlocked = payload.get("unlocked") or []
                rows.append(
                    {
                        "group_id": gid,
                        "user_id": user_id,
                        "nickname": _text(payload.get("nickname")) or "用户",
                        "current_ally": _text(current.get("ally_filename")),
                        "current_date": _text(current.get("date")),
                        "no_new_count": _int(payload.get("no_new_count"), 0),
                        "unlocked_count": len(unlocked),
                        "unlocked": [
                            "{0}@{1}".format(
                                _text(item.get("ally_filename")),
                                _text(item.get("unlock_date")),
                            )
                            for item in unlocked
                            if _text(item.get("ally_filename"))
                        ],
                    }
                )
        return rows

    def _audit_rows(self) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for item in self.store.audit_entries(500):
            rows.append({key: _text(item.get(key)) for key in AUDIT_FIELDS})
        return rows

    # ------------------------------------------------------------- 素材与整包

    def _asset_files(self) -> List[Tuple[Path, int]]:
        directory = self.store.assets_dir
        if not directory.is_dir():
            return []
        files: List[Tuple[Path, int]] = []
        for item in directory.iterdir():
            if not item.is_file() or item.suffix.lower() not in IMAGE_SUFFIXES:
                continue
            try:
                files.append((item, item.stat().st_size))
            except OSError:
                continue
        files.sort(key=lambda pair: pair[0].name.lower())
        return files

    @staticmethod
    def _plan_asset_volumes(
        files: Sequence[Tuple[Path, int]]
    ) -> List[Dict[str, int]]:
        """按 64 MB 一卷做贪心切分，保证每卷至少有一张图（超大图单独成卷）。"""
        volumes: List[Dict[str, int]] = []
        start = 0
        used = 0
        count = 0
        for index, (_, size) in enumerate(files):
            if count and used + size > ASSET_VOLUME_BYTES:
                volumes.append(
                    {
                        "volume": len(volumes) + 1,
                        "start": start,
                        "end": index,
                        "count": count,
                        "bytes": used,
                    }
                )
                start = index
                used = 0
                count = 0
            used += size
            count += 1
        volumes.append(
            {
                "volume": len(volumes) + 1,
                "start": start,
                "end": len(files),
                "count": count,
                "bytes": used,
            }
        )
        return volumes

    def _asset_stats(self) -> Dict[str, Any]:
        files = self._asset_files()
        volumes = self._plan_asset_volumes(files)
        return {
            "count": len(files),
            "bytes": sum(size for _, size in files),
            "volumes": len(volumes),
            "volume_bytes": ASSET_VOLUME_BYTES,
        }

    def _bundle_files(self) -> List[Tuple[str, Path, int]]:
        """整包成员：``catalog.json`` + ``config/*`` + ``webui/`` 里的两份运行记录。

        故意不收 ``webui/imports`` 与 ``webui/exports``，那是本模块自己的中转目录。
        """
        store = self.store
        members: List[Tuple[str, Path, int]] = []

        def add(arcname: str, path: Path) -> None:
            try:
                if path.is_file():
                    members.append((arcname, path, path.stat().st_size))
            except OSError:
                return

        add("catalog.json", store.catalog_path)
        if store.config_dir.is_dir():
            for item in sorted(store.config_dir.iterdir(), key=lambda p: p.name):
                if item.is_file() and item.suffix.lower() == ".json":
                    add("config/" + item.name, item)
        for path in (store.audit_path, store.tombstones_path):
            add("webui/" + path.name, path)
        return members

    def _write_archive(
        self,
        filename: str,
        manifest: Mapping[str, Any],
        members: Sequence[Tuple[str, Path]],
        *,
        compress: bool,
    ) -> TransferExport:
        """先把压缩包写到磁盘，再交给 :meth:`TransferExport.chunks` 流式发送。

        素材包可能有几百 MB，在内存里拼完整个 zip 会直接把进程打爆。
        """
        self.export_dir.mkdir(parents=True, exist_ok=True)
        target = self.export_dir / (uuid.uuid4().hex + ".zip")
        mode = zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED
        try:
            with zipfile.ZipFile(target, "w", mode) as archive:
                archive.writestr(
                    "manifest.json",
                    json.dumps(dict(manifest), ensure_ascii=False, indent=2) + "\n",
                )
                for arcname, path in members:
                    archive.write(path, arcname)
        except OSError as exc:
            target.unlink(missing_ok=True)
            raise ValueError(f"生成压缩包失败：{exc}") from exc
        return TransferExport(
            filename=filename,
            mime_type="application/zip",
            total_bytes=target.stat().st_size,
            path=target,
        )

    def _export_assets(self, volume: int) -> TransferExport:
        files = self._asset_files()
        if not files:
            raise ValueError("素材目录里还没有图片")
        volumes = self._plan_asset_volumes(files)
        index = min(max(1, volume), len(volumes))
        plan = volumes[index - 1]
        selected = files[plan["start"] : plan["end"]]
        manifest = {
            "plugin": PLUGIN_NAME,
            "dataset": "assets",
            "schema_version": SCHEMA_VERSION,
            "generated_at": _now(),
            "volume": index,
            "volume_total": len(volumes),
            "count": len(selected),
            "bytes": plan["bytes"],
            "files": [path.name for path, _ in selected],
        }
        suffix = f"-part{index}of{len(volumes)}" if len(volumes) > 1 else ""
        filename = f"kirby-assets{suffix}-{_stamp()}.zip"
        # 图片本身已经是压缩格式，再 deflate 只会白烧 CPU。
        return self._write_archive(
            filename,
            manifest,
            [("assets/" + path.name, path) for path, _ in selected],
            compress=False,
        )

    def _export_bundle(self) -> TransferExport:
        members = self._bundle_files()
        if not members:
            raise ValueError("还没有可备份的配置文件")
        manifest = {
            "plugin": PLUGIN_NAME,
            "dataset": "bundle",
            "schema_version": SCHEMA_VERSION,
            "generated_at": _now(),
            "count": len(members),
            "bytes": sum(size for _, _, size in members),
            "files": [arcname for arcname, _, _ in members],
        }
        return self._write_archive(
            f"kirby-bundle-{_stamp()}.zip",
            manifest,
            [(arcname, path) for arcname, path, _ in members],
            compress=True,
        )

    # ------------------------------------------------------------ 暂存区管理

    def _token_dir(self, token: Any) -> Path:
        value = _text(token).lower()
        if not TOKEN_RE.match(value):
            raise ValueError("暂存编号无效")
        return self.stage_dir / value

    def _stage_bytes(self) -> int:
        total = 0
        if not self.stage_dir.is_dir():
            return 0
        for item in self.stage_dir.rglob("*"):
            try:
                if item.is_file():
                    total += item.stat().st_size
            except OSError:
                continue
        return total

    def begin_upload(self, dataset: Any, filename: Any) -> Dict[str, Any]:
        """开一个暂存槽位，返回给上层「往哪写、最多写多少」。

        先给路径再落盘，是为了让 WebUI 能把大文件按块直接写进磁盘，
        而不是先在内存里拼出一个 96 MB 的 bytes。
        """
        original = Path(_text(filename)).name
        if not original:
            raise ValueError("请选择要导入的文件")
        # 宿主 bridge 的 upload() 不允许 endpoint 带 query，dataset 走路径参数；
        # 万一旧版本 Dashboard 把路径参数吃掉了，就退回用导出文件名反推。
        name = _text(dataset).lower() or self._guess_dataset(original)
        name = self._dataset_name(name)
        fmt = self._suffix_format(original)
        if fmt not in DATASET_SPECS[name]["formats"]:
            expected = "、".join(DATASET_SPECS[name]["formats"])
            raise ValueError(f"「{DATASET_SPECS[name]['label']}」只接受 {expected} 文件")
        if not DATASET_SPECS[name]["modes"]:
            raise ValueError(f"「{DATASET_SPECS[name]['label']}」只支持导出")
        self.cleanup()
        if self._stage_bytes() >= STAGE_CAPACITY_BYTES:
            raise ValueError("暂存区已满，请先应用或丢弃待处理的导入任务")
        archive = fmt == "zip"
        limit = TRANSFER_MAX_ARCHIVE_BYTES if archive else TRANSFER_MAX_TEXT_BYTES
        token = uuid.uuid4().hex
        directory = self.stage_dir / token
        directory.mkdir(parents=True, exist_ok=True)
        payload = directory / ("payload." + fmt)
        _atomic_write_json(
            directory / "upload.json",
            {
                "token": token,
                "dataset": name,
                "format": fmt,
                "filename": original,
                "created_at": _now(),
            },
        )
        return {
            "token": token,
            "dataset": name,
            "format": fmt,
            "archive": archive,
            "limit": limit,
            "path": payload,
        }

    def stage(
        self,
        token: Any,
        filename: Any = "",
        *,
        data: Optional[bytes] = None,
    ) -> Dict[str, Any]:
        """把上传内容固化下来并跑一遍预检，只读不写业务数据。"""
        directory = self._token_dir(token)
        upload = _read_json(directory / "upload.json", None)
        if not isinstance(upload, Mapping):
            raise ValueError("暂存任务已过期，请重新上传")
        dataset = self._dataset_name(upload.get("dataset"))
        fmt = _text(upload.get("format")) or "json"
        payload = directory / ("payload." + fmt)
        if data is not None:
            limit = (
                TRANSFER_MAX_ARCHIVE_BYTES if fmt == "zip" else TRANSFER_MAX_TEXT_BYTES
            )
            if len(data) > limit:
                raise ValueError(f"文件超过 {limit // (1024 * 1024)} MB 上限")
            _atomic_write_bytes(payload, data)
        if not payload.is_file():
            raise ValueError("没有收到文件内容")
        size = payload.stat().st_size
        if size <= 0:
            raise ValueError("上传的文件是空的")
        spec = DATASET_SPECS[dataset]
        preview = self._preview(dataset, fmt, payload)
        modes = list(spec["modes"])
        record = {
            "token": _text(token).lower(),
            "dataset": dataset,
            "label": spec["label"],
            "icon": spec["icon"],
            "format": fmt,
            "filename": Path(_text(filename)).name
            or _text(upload.get("filename"))
            or ("payload." + fmt),
            "size": size,
            "created_at": _now(),
            "created_ts": time.time(),
            "modes": modes,
            "mode_labels": [MODE_LABELS[mode] for mode in modes],
            "summary": preview["summary"],
            "summaries": preview["summaries"],
            "warnings": preview["warnings"],
            "samples": preview["samples"],
            "notes": preview["notes"],
        }
        _atomic_write_json(directory / "record.json", record)
        return self._decorate(record)

    @staticmethod
    def _decorate(record: Mapping[str, Any]) -> Dict[str, Any]:
        result = {key: value for key, value in record.items() if key != "created_ts"}
        created = float(record.get("created_ts") or 0.0)
        remaining = STAGE_TTL_SECONDS - int(max(0.0, time.time() - created))
        result["expires_in"] = max(0, remaining)
        return result

    def pending(self) -> List[Dict[str, Any]]:
        """列出所有等待确认的导入任务，新的排前面。"""
        if not self.stage_dir.is_dir():
            return []
        records: List[Dict[str, Any]] = []
        for directory in self.stage_dir.iterdir():
            if not directory.is_dir():
                continue
            record = _read_json(directory / "record.json", None)
            if isinstance(record, Mapping) and record.get("token"):
                records.append(self._decorate(record))
        records.sort(key=lambda item: _text(item.get("created_at")), reverse=True)
        return records

    def _record(self, token: Any) -> Tuple[Dict[str, Any], Path]:
        directory = self._token_dir(token)
        record = _read_json(directory / "record.json", None)
        if not isinstance(record, Mapping):
            raise ValueError("暂存任务不存在或已过期")
        fmt = _text(record.get("format")) or "json"
        payload = directory / ("payload." + fmt)
        if not payload.is_file():
            raise ValueError("暂存文件已被清理，请重新上传")
        return dict(record), payload

    def discard(self, token: Any) -> Dict[str, Any]:
        """丢弃一个暂存任务，把整个目录删掉。"""
        directory = self._token_dir(token)
        shutil.rmtree(directory, ignore_errors=True)
        return {"token": _text(token).lower(), "pending": self.pending()}

    def cleanup(self) -> None:
        """清掉过期的暂存目录与残留的导出临时包。"""
        now = time.time()
        if self.stage_dir.is_dir():
            for directory in self.stage_dir.iterdir():
                if not directory.is_dir():
                    continue
                try:
                    if now - directory.stat().st_mtime > STAGE_TTL_SECONDS:
                        shutil.rmtree(directory, ignore_errors=True)
                except OSError:
                    continue
        if self.export_dir.is_dir():
            for item in self.export_dir.iterdir():
                try:
                    if item.is_file() and now - item.stat().st_mtime > EXPORT_TTL_SECONDS:
                        item.unlink(missing_ok=True)
                except OSError:
                    continue

    # ------------------------------------------------------------ 解析与预检

    @staticmethod
    def _decode(raw: bytes) -> str:
        try:
            return raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("文件必须使用 UTF-8 编码") from exc

    def _parse_rows(self, dataset: str, fmt: str, path: Path) -> List[Dict[str, Any]]:
        raw = path.read_bytes()
        if dataset == "terminology":
            store = self._require_terminology()
            try:
                if fmt == "csv":
                    return store.parse_csv_rows(raw)
                return store.parse_json_rows(raw)
            except TerminologyError as exc:
                raise ValueError(str(exc)) from exc
        text = self._decode(raw)
        if fmt == "csv":
            return self._parse_csv(dataset, text)
        return self._parse_json(dataset, text)

    def _parse_json(self, dataset: str, text: str) -> List[Dict[str, Any]]:
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON 解析失败：{exc}") from exc
        if isinstance(raw, Mapping):
            declared = _text(raw.get("dataset"))
            if declared and declared != dataset and declared in DATASET_SPECS:
                label = DATASET_SPECS[declared]["label"]
                raise ValueError(f"这是「{label}」的备份文件，请切换到对应的数据类型")
            items: Any = None
            for key in ("items", "rows", "entries"):
                if key in raw:
                    items = raw[key]
                    break
            if items is None:
                raise ValueError("JSON 里找不到 items 数组")
        else:
            items = raw
        if isinstance(items, Mapping):
            rows: List[Dict[str, Any]] = []
            for key, value in items.items():
                if not isinstance(value, Mapping):
                    continue
                row = dict(value)
                row.setdefault("key", key)
                rows.append(row)
            return rows
        if isinstance(items, list):
            return [dict(row) for row in items if isinstance(row, Mapping)]
        raise ValueError("JSON 结构无法识别")

    @staticmethod
    def _parse_csv(dataset: str, text: str) -> List[Dict[str, Any]]:
        reader = csv.DictReader(StringIO(text))
        list_fields = CSV_LIST_FIELDS.get(dataset, ())
        rows: List[Dict[str, Any]] = []
        for raw in reader:
            row = {key: value for key, value in raw.items() if key}
            if not any(_text(value) for value in row.values()):
                continue
            for field in list_fields:
                row[field] = _list(row.get(field))
            rows.append(row)
        return rows

    def _preview(self, dataset: str, fmt: str, path: Path) -> Dict[str, Any]:
        """跑一遍导入流程但不落盘，把每种导入方式的差异都算出来。"""
        if dataset in ("assets", "bundle"):
            return self._preview_archive(dataset, path)
        rows = self._parse_rows(dataset, fmt, path)
        if not rows:
            raise ValueError("文件里没有可导入的数据行")
        modes = list(DATASET_SPECS[dataset]["modes"])
        summaries: Dict[str, Dict[str, int]] = {}
        primary: Dict[str, Any] = {}
        for mode in modes:
            plan = self._plan_rows(dataset, rows, mode)
            summaries[mode] = plan["summary"]
            if not primary:
                primary = plan
        return {
            "summary": summaries[modes[0]],
            "summaries": summaries,
            "warnings": primary.get("warnings") or [],
            "samples": primary.get("samples") or [],
            "notes": primary.get("notes") or [],
        }

    def _plan_rows(
        self,
        dataset: str,
        rows: Sequence[Mapping[str, Any]],
        mode: str,
    ) -> Dict[str, Any]:
        if dataset == "catalog":
            return self._walk_catalog(rows)
        if dataset == "groups":
            return self._walk_groups(rows, mode=mode)
        if dataset == "terminology":
            store = self._require_terminology()
            try:
                raw = store.preview_rows(rows, replace_overrides=mode == "replace")
            except TerminologyError as exc:
                raise ValueError(str(exc)) from exc
            return {
                "summary": _summary(raw),
                "warnings": [],
                "samples": list(raw.get("samples") or []),
                "notes": [],
            }
        if dataset == "wiki-index":
            store_index = self._require_wiki_index()
            raw = store_index.preview_rows(rows, replace_overrides=mode == "replace")
            return {
                "summary": _summary(raw),
                "warnings": list(raw.get("warnings") or []),
                "samples": list(raw.get("samples") or []),
                "notes": [],
            }
        raise ValueError("该数据不支持导入")

    def _catalog_index(self) -> Dict[str, Dict[str, Any]]:
        """建三套索引，让导入文件可以用 entry_key / 文件名 / 编号任意一种对齐。"""
        index: Dict[str, Dict[str, Any]] = {}
        for entry in self.store.entries():
            entry_key = _text(entry.get("entry_key")).casefold()
            filename = Path(_text(entry.get("filename"))).name.casefold()
            catalog_id = _int(entry.get("id"), 0)
            if entry_key:
                index.setdefault("entry_key:" + entry_key, entry)
            if filename:
                index.setdefault("filename:" + filename, entry)
            if catalog_id:
                index.setdefault("id:" + str(catalog_id), entry)
        return index

    @staticmethod
    def _match_entry(
        index: Mapping[str, Dict[str, Any]], row: Mapping[str, Any]
    ) -> Optional[Dict[str, Any]]:
        entry_key = _text(row.get("entry_key")).casefold()
        if entry_key and "entry_key:" + entry_key in index:
            return index["entry_key:" + entry_key]
        filename = Path(_text(row.get("filename"))).name.casefold()
        if filename and "filename:" + filename in index:
            return index["filename:" + filename]
        catalog_id = _int(row.get("id"), 0)
        if catalog_id and "id:" + str(catalog_id) in index:
            return index["id:" + str(catalog_id)]
        return None

    # ------------------------------------------------------------ 图鉴与群组

    def _catalog_change(
        self, entry: Mapping[str, Any], row: Mapping[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """算出一行导入数据会对某个图鉴条目造成什么改动，没改动就返回 ``None``。"""
        profile = self.store.profile_for(entry)
        current_name = _text(entry.get("name"))
        current_source = _text(entry.get("source"))
        name = _text(row.get("name")) or current_name
        source = _text(row.get("source")) if "source" in row else current_source
        if not name:
            raise ValueError("角色名称不能为空")
        if len(name) > 60:
            raise ValueError("角色名称不能超过 60 个字")
        if len(source) > 60:
            raise ValueError("所属作品不能超过 60 个字")
        current_description = _text(profile.get("description_zh"))
        current_origin = _text(profile.get("description_origin")) or "missing"
        row_origin = _text(row.get("description_origin")).lower()
        description = _text(row.get("description"))
        truncated = False
        if len(description) > MAX_DESCRIPTION_CHARS:
            description = description[:MAX_DESCRIPTION_CHARS]
            truncated = True
        action = "keep"
        if description and description != current_description:
            action = "set"
        elif row_origin in {"bundled", "missing"} and current_origin == "override":
            # 文件里说这条用的是内置简介，说明备份来源没有改过它，把本地覆盖撤掉
            action = "restore"
        if action == "keep" and name == current_name and source == current_source:
            return None
        return {
            "name": name,
            "source": source,
            "description_action": action,
            "description": description,
            "truncated": truncated,
        }

    def _walk_catalog(
        self,
        rows: Sequence[Mapping[str, Any]],
        *,
        apply: bool = False,
        username: str = "",
    ) -> Dict[str, Any]:
        index = self._catalog_index()
        summary = _empty_summary()
        warnings: List[str] = []
        samples: List[Dict[str, str]] = []
        changes: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
        truncated = 0
        for position, row in enumerate(rows, start=1):
            summary["total"] += 1
            entry = self._match_entry(index, row)
            if entry is None:
                summary["skipped"] += 1
                if len(warnings) < 12:
                    label = (
                        _text(row.get("name"))
                        or _text(row.get("filename"))
                        or f"第 {position} 行"
                    )
                    warnings.append(f"第 {position} 行「{label}」找不到对应素材，已跳过")
                continue
            try:
                change = self._catalog_change(entry, row)
            except ValueError as exc:
                summary["skipped"] += 1
                if len(warnings) < 12:
                    warnings.append(f"第 {position} 行：{exc}")
                continue
            if change is None:
                summary["unchanged"] += 1
                continue
            if change["truncated"]:
                truncated += 1
            summary["updated"] += 1
            changes.append((entry, change))
            if len(samples) < 8:
                samples.append(
                    {
                        "id": str(entry.get("id") or ""),
                        "label": change["name"],
                        "state": "updated",
                    }
                )
        if len(changes) > MAX_CATALOG_CHANGES:
            raise ValueError(
                f"一次最多更新 {MAX_CATALOG_CHANGES} 条图鉴资料，当前文件有 {len(changes)} 条改动"
            )
        notes = ["图鉴导入只更新已有素材；要新增角色请先导入素材图片包，再回来导入资料。"]
        if truncated:
            notes.append(f"有 {truncated} 条简介超过 {MAX_DESCRIPTION_CHARS} 字，会被截断。")
        if apply:
            applied = 0
            for entry, change in changes:
                try:
                    self.store.update_entry_details(
                        entry,
                        change["name"],
                        change["source"],
                        description_action=change["description_action"],
                        description=change["description"],
                        updated_by=username,
                    )
                    applied += 1
                except (ValueError, FileExistsError, OSError) as exc:
                    # 改名可能和别的条目撞车，单条失败不该让整批回滚
                    summary["skipped"] += 1
                    if len(warnings) < 12:
                        warnings.append(f"「{change['name']}」更新失败：{exc}")
            summary["updated"] = applied
        return {
            "summary": summary,
            "warnings": warnings,
            "samples": samples,
            "notes": notes,
        }

    def _walk_groups(
        self,
        rows: Sequence[Mapping[str, Any]],
        *,
        mode: str = "merge",
        apply: bool = False,
        username: str = "",
    ) -> Dict[str, Any]:
        store = self.store
        summary = _empty_summary()
        warnings: List[str] = []
        samples: List[Dict[str, str]] = []
        incoming: Dict[str, Dict[str, Any]] = {}
        for position, row in enumerate(rows, start=1):
            summary["total"] += 1
            group_id = _text(row.get("group_id"))
            user_id = _text(row.get("user_id"))
            if not DATA_ID_RE.match(group_id) or not DATA_ID_RE.match(user_id):
                summary["skipped"] += 1
                if len(warnings) < 12:
                    warnings.append(f"第 {position} 行的群号或用户号不合法，已跳过")
                continue
            bucket = incoming.setdefault(group_id, {})
            if user_id in bucket:
                summary["skipped"] += 1
                if len(warnings) < 12:
                    warnings.append(f"第 {position} 行是重复的成员记录，已跳过")
                continue
            bucket[user_id] = {
                "nickname": _text(row.get("nickname")) or "用户",
                "no_new_count": max(0, _int(row.get("no_new_count"), 0)),
                "current": {
                    "ally_filename": Path(_text(row.get("current_ally"))).name,
                    "date": _text(row.get("current_date")),
                },
                "unlocked": _unlocked_from_row(row.get("unlocked")),
            }
        for group_id, bucket in sorted(incoming.items()):
            existing = normalise_group_config(store.load_group(group_id))
            normalised = normalise_group_config(bucket)
            merged: Dict[str, Any] = {} if mode == "replace" else dict(existing)
            for user_id, payload in normalised.items():
                before = existing.get(user_id)
                if before is None:
                    summary["added"] += 1
                    state = "added"
                elif before == payload:
                    summary["unchanged"] += 1
                    state = "unchanged"
                else:
                    summary["updated"] += 1
                    state = "updated"
                merged[user_id] = payload
                if state != "unchanged" and len(samples) < 8:
                    samples.append(
                        {
                            "id": f"{group_id}:{user_id}",
                            "label": f"{payload['nickname']}（{group_id}）",
                            "state": state,
                        }
                    )
            if mode == "replace":
                summary["removed"] += len(set(existing) - set(normalised))
            if apply:
                store.save_group(group_id, merged)
        notes = [
            "解锁记录用「文件名@日期」表示；日期留空表示未知。",
            "整体替换只会重写文件里出现过的群，其它群不受影响。",
        ]
        if username:
            notes.append(f"本次操作会记到 {username} 名下。")
        return {
            "summary": summary,
            "warnings": warnings,
            "samples": samples,
            "notes": notes,
        }

    # ------------------------------------------------------------ 压缩包预检

    @staticmethod
    def _archive_members(path: Path) -> List[Tuple[str, str, int]]:
        """列出压缩包成员，同时挡掉 zip-slip、超量成员和解压炸弹。"""
        try:
            with zipfile.ZipFile(path) as archive:
                infos = archive.infolist()
        except (zipfile.BadZipFile, OSError) as exc:
            raise ValueError(f"压缩包无法读取：{exc}") from exc
        if len(infos) > MAX_ARCHIVE_MEMBERS:
            raise ValueError(f"压缩包里的文件超过 {MAX_ARCHIVE_MEMBERS} 个，请拆分后再导入")
        members: List[Tuple[str, str, int]] = []
        total = 0
        for info in infos:
            if info.is_dir():
                continue
            safe = _safe_member(info.filename)
            if not safe:
                continue
            total += max(0, int(info.file_size))
            if total > MAX_EXTRACTED_BYTES:
                raise ValueError("压缩包解压后体积过大，请按分卷导入")
            members.append((info.filename, safe, max(0, int(info.file_size))))
        if not members:
            raise ValueError("压缩包是空的")
        return members

    def _bundle_kind(self, member: str) -> str:
        """判断整包成员属于哪一类；不认识的一律当作 ``unknown`` 跳过。"""
        parts = member.split("/")
        if len(parts) == 1:
            return "catalog" if parts[0] == "catalog.json" else "unknown"
        if len(parts) != 2:
            return "unknown"
        folder, name = parts
        if folder == "config":
            if name in NON_GROUP_CONFIG_FILENAMES:
                return "config"
            stem = Path(name).stem
            if name.lower().endswith(".json") and DATA_ID_RE.match(stem):
                return "group"
            return "unknown"
        if folder == "webui":
            allowed = {self.store.audit_path.name, self.store.tombstones_path.name}
            return "webui" if name in allowed else "unknown"
        return "unknown"

    def _bundle_target(self, member: str, kind: str) -> Optional[Path]:
        store = self.store
        name = Path(member).name
        if kind == "catalog":
            return store.catalog_path
        if kind in ("config", "group"):
            return store.config_dir / name
        if kind == "webui":
            return store.webui_dir / name
        return None

    def _preview_archive(self, dataset: str, path: Path) -> Dict[str, Any]:
        members = self._archive_members(path)
        warnings: List[str] = []
        samples: List[Dict[str, str]] = []
        notes: List[str] = []
        if dataset == "assets":
            summary = _empty_summary()
            seen: Dict[str, bool] = {}
            limit_mb = MAX_SINGLE_ASSET_BYTES // (1024 * 1024)
            for _raw, safe, size in members:
                name = Path(safe).name
                if name in ARCHIVE_METADATA_NAMES:
                    continue
                summary["total"] += 1
                lowered = name.lower()
                if Path(name).suffix.lower() not in IMAGE_SUFFIXES:
                    summary["skipped"] += 1
                    if len(warnings) < 12:
                        warnings.append(f"{name} 不是支持的图片格式，已跳过")
                    continue
                if size > MAX_SINGLE_ASSET_BYTES:
                    summary["skipped"] += 1
                    if len(warnings) < 12:
                        warnings.append(f"{name} 超过 {limit_mb} MB，已跳过")
                    continue
                if lowered in seen:
                    summary["skipped"] += 1
                    if len(warnings) < 12:
                        warnings.append(f"{name} 在包里重复出现，只保留第一份")
                    continue
                seen[lowered] = True
                if (self.store.assets_dir / name).is_file():
                    summary["updated"] += 1
                    state = "updated"
                else:
                    summary["added"] += 1
                    state = "added"
                if len(samples) < 8:
                    samples.append({"id": name, "label": name, "state": state})
            summaries = {"merge": dict(summary)}
            notes.append("素材导入不会删除现有图片；同名文件会被覆盖。")
            notes.append("恢复整套备份时请先导入素材图片包，再导入配置整包。")
        else:
            merge_summary = _empty_summary()
            replace_summary = _empty_summary()
            counts: Dict[str, int] = {}
            for _raw, safe, _size in members:
                if safe in ARCHIVE_METADATA_NAMES:
                    continue
                merge_summary["total"] += 1
                replace_summary["total"] += 1
                kind = self._bundle_kind(safe)
                if kind == "unknown":
                    merge_summary["skipped"] += 1
                    replace_summary["skipped"] += 1
                    if len(warnings) < 12:
                        warnings.append(f"{safe} 不属于配置整包的内容，已跳过")
                    continue
                counts[kind] = counts.get(kind, 0) + 1
                target = self._bundle_target(safe, kind)
                exists = target is not None and target.is_file()
                state = "updated" if exists else "added"
                if kind in ("catalog", "webui"):
                    # 合并导入不动图鉴索引与运行记录，否则会把本机新增的素材抹掉
                    merge_summary["skipped"] += 1
                    replace_summary[state] += 1
                else:
                    merge_summary[state] += 1
                    replace_summary[state] += 1
                if len(samples) < 8:
                    samples.append({"id": safe, "label": safe, "state": state})
            summary = merge_summary
            summaries = {"merge": merge_summary, "replace": replace_summary}
            if counts:
                detail = "、".join(
                    f"{BUNDLE_KIND_LABELS[key]} {value} 个"
                    for key, value in sorted(counts.items())
                )
                notes.append("包含：" + detail)
            notes.append("合并导入只补齐自定义配置与群组进度，图鉴索引和运行记录保持原样。")
            notes.append("整体替换会连图鉴索引与运行记录一起覆盖，请确认这是同一套素材的备份。")
            notes.append("恢复整套备份时请先导入素材图片包，再导入配置整包。")
        if summary["total"] <= 0:
            raise ValueError("压缩包里没有可导入的内容")
        return {
            "summary": summary,
            "summaries": summaries,
            "warnings": warnings,
            "samples": samples,
            "notes": notes,
        }

    # ------------------------------------------------------------------ 应用

    def apply(self, token: Any, mode: Any = "", username: Any = "") -> Dict[str, Any]:
        """真正落盘。只有这一处会改业务数据，而且一定写一条审计记录。"""
        record, payload = self._record(token)
        dataset = self._dataset_name(record.get("dataset"))
        mode_name = self._mode_name(dataset, mode)
        fmt = _text(record.get("format")) or "json"
        who = _text(username) or "dashboard"
        spec = DATASET_SPECS[dataset]
        with self._lock:
            if dataset == "assets":
                result = self._apply_assets(payload)
            elif dataset == "bundle":
                result = self._apply_bundle(payload, mode_name)
            elif dataset == "catalog":
                result = self._walk_catalog(
                    self._parse_rows(dataset, fmt, payload),
                    apply=True,
                    username=who,
                )
            elif dataset == "groups":
                result = self._walk_groups(
                    self._parse_rows(dataset, fmt, payload),
                    mode=mode_name,
                    apply=True,
                    username=who,
                )
            elif dataset == "terminology":
                terms = self._require_terminology()
                rows = self._parse_rows(dataset, fmt, payload)
                replace = mode_name == "replace"
                try:
                    preview = terms.preview_rows(rows, replace_overrides=replace)
                    terms.import_rows(rows, replace_overrides=replace)
                except TerminologyError as exc:
                    raise ValueError(str(exc)) from exc
                result = {
                    "summary": _summary(preview),
                    "warnings": [],
                    "samples": list(preview.get("samples") or []),
                    "notes": [],
                }
            elif dataset == "wiki-index":
                index_store = self._require_wiki_index()
                raw = index_store.import_rows(
                    self._parse_rows(dataset, fmt, payload),
                    replace_overrides=mode_name == "replace",
                    updated_by=who,
                )
                result = {
                    "summary": _summary(raw),
                    "warnings": list(raw.get("warnings") or []),
                    "samples": [],
                    "notes": [],
                }
            else:
                raise ValueError("该数据不支持导入")
        summary = result["summary"]
        self.store.append_audit(
            "transfer.import",
            spec["label"],
            "{0}：新增 {1}、更新 {2}、跳过 {3}".format(
                MODE_LABELS[mode_name],
                summary.get("added", 0),
                summary.get("updated", 0),
                summary.get("skipped", 0),
            ),
            username=who,
        )
        self.discard(token)
        self.invalidate()
        return {
            "dataset": dataset,
            "label": spec["label"],
            "mode": mode_name,
            "mode_label": MODE_LABELS[mode_name],
            "summary": summary,
            "warnings": result.get("warnings") or [],
            "notes": result.get("notes") or [],
            "pending": self.pending(),
        }

    def _apply_assets(self, path: Path) -> Dict[str, Any]:
        store = self.store
        store.assets_dir.mkdir(parents=True, exist_ok=True)
        members = self._archive_members(path)
        summary = _empty_summary()
        warnings: List[str] = []
        seen: Dict[str, bool] = {}
        limit_mb = MAX_SINGLE_ASSET_BYTES // (1024 * 1024)
        written = 0
        with zipfile.ZipFile(path) as archive:
            for raw_name, safe, size in members:
                name = Path(safe).name
                if name in ARCHIVE_METADATA_NAMES:
                    continue
                summary["total"] += 1
                lowered = name.lower()
                if (
                    Path(name).suffix.lower() not in IMAGE_SUFFIXES
                    or lowered in seen
                    or size > MAX_SINGLE_ASSET_BYTES
                ):
                    summary["skipped"] += 1
                    continue
                seen[lowered] = True
                exists = (store.assets_dir / name).is_file()
                try:
                    with archive.open(raw_name) as handle:
                        data = handle.read(MAX_SINGLE_ASSET_BYTES + 1)
                    if len(data) > MAX_SINGLE_ASSET_BYTES:
                        raise ValueError(f"超过 {limit_mb} MB 上限")
                    Image.open(BytesIO(data)).verify()
                    _atomic_write_bytes(store.assets_dir / name, data)
                except Exception as exc:  # noqa: BLE001 - 单张坏图不该中断整批
                    summary["skipped"] += 1
                    if len(warnings) < 12:
                        warnings.append(f"{name} 导入失败：{exc}")
                    continue
                written += 1
                summary["updated" if exists else "added"] += 1
        if written:
            # 逐张 refresh 是 O(n²)：一千多张图会把目录重扫一千多次，所以只扫一次
            store.refresh()
        notes = [
            "素材写入 img/allies/ 后只重扫一次图鉴索引，导入千张图也只扫一次。"
        ]
        if written:
            notes.append(f"本次写入 {written} 张图片。")
        return {
            "summary": summary,
            "warnings": warnings,
            "samples": [],
            "notes": notes,
        }

    @staticmethod
    def _merge_config(name: str, target: Path, payload: Any) -> Any:
        """合并导入时按键合并配置文件，而不是整份覆盖。"""
        current = _read_json(target, None)
        if name in {"draw_limits.json", "draw_bonuses.json"}:
            merged = dict(current) if isinstance(current, Mapping) else {}
            if isinstance(payload, Mapping):
                merged.update(payload)
            return merged
        if isinstance(payload, Mapping) and isinstance(payload.get("items"), Mapping):
            items: Dict[str, Any] = {}
            if isinstance(current, Mapping) and isinstance(
                current.get("items"), Mapping
            ):
                items.update(current["items"])
            items.update(payload["items"])
            result = dict(current) if isinstance(current, Mapping) else {}
            result.update(
                {key: value for key, value in payload.items() if key != "items"}
            )
            result["items"] = items
            result.setdefault("version", 1)
            return result
        return payload

    def _apply_bundle(self, path: Path, mode: str) -> Dict[str, Any]:
        store = self.store
        members = self._archive_members(path)
        summary = _empty_summary()
        warnings: List[str] = []
        counts: Dict[str, int] = {}
        replace = mode == "replace"
        touched = False
        with zipfile.ZipFile(path) as archive:
            for raw_name, safe, _size in members:
                if safe in ARCHIVE_METADATA_NAMES:
                    continue
                summary["total"] += 1
                kind = self._bundle_kind(safe)
                target = self._bundle_target(safe, kind)
                if kind == "unknown" or target is None:
                    summary["skipped"] += 1
                    if len(warnings) < 12:
                        warnings.append(f"{safe} 不属于配置整包的内容，已跳过")
                    continue
                if kind in ("catalog", "webui") and not replace:
                    summary["skipped"] += 1
                    continue
                try:
                    payload = json.loads(archive.read(raw_name).decode("utf-8-sig"))
                except (
                    KeyError,
                    OSError,
                    UnicodeDecodeError,
                    json.JSONDecodeError,
                ) as exc:
                    summary["skipped"] += 1
                    if len(warnings) < 12:
                        warnings.append(f"{safe} 解析失败：{exc}")
                    continue
                exists = target.is_file()
                try:
                    if kind == "group":
                        group_id = Path(safe).stem
                        final = normalise_group_config(payload)
                        if not replace:
                            merged_group = normalise_group_config(
                                store.load_group(group_id)
                            )
                            merged_group.update(final)
                            final = merged_group
                        store.save_group(group_id, final)
                    elif kind == "config" and not replace:
                        _atomic_write_json(
                            target,
                            self._merge_config(Path(safe).name, target, payload),
                        )
                    else:
                        _atomic_write_json(target, payload)
                except (OSError, ValueError) as exc:
                    summary["skipped"] += 1
                    if len(warnings) < 12:
                        warnings.append(f"{safe} 写入失败：{exc}")
                    continue
                touched = True
                counts[kind] = counts.get(kind, 0) + 1
                summary["updated" if exists else "added"] += 1
        if touched:
            # 配置文件是被绕过内存直接改掉的，必须让各个 store 重新读盘
            store.reload()
            if self.terminology is not None and self.terminology.loaded:
                self.terminology.reload()
            if self.wiki_index is not None:
                self.wiki_index.reload()
        notes: List[str] = []
        if counts:
            detail = "、".join(
                f"{BUNDLE_KIND_LABELS[key]} {value} 个"
                for key, value in sorted(counts.items())
            )
            notes.append("已恢复：" + detail)
        notes.append("图鉴里若出现「素材缺失」，说明对应图片还没导入，补一份素材图片包即可。")
        return {
            "summary": summary,
            "warnings": warnings,
            "samples": [],
            "notes": notes,
        }


__all__ = ["CatalogTransferService", "TransferExport", "DATASET_SPECS"]
