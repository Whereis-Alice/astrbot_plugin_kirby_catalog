from __future__ import annotations

import asyncio
import base64
import inspect
import threading
import time
import uuid
from collections import Counter, OrderedDict
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional

from PIL import Image, ImageOps

from astrbot.api import logger

from .catalog_core import (
    SHANGHAI,
    CatalogStore,
    _atomic_write_bytes,
    _atomic_write_json,
    _read_json,
    get_today,
)

PLUGIN_ID = "astrbot_plugin_kirby_catalog"
MAX_UPLOAD_BYTES = 16 * 1024 * 1024
MAX_DESCRIPTION_CHARS = 30000
THUMBNAIL_SIZE = 192

try:
    from astrbot.api.web import (
        PluginUploadFile,
        error_response,
        json_response,
        request,
    )

    MODERN_WEB_API = True
except ImportError:  # pragma: no cover - compatibility for AstrBot < 4.26
    from quart import jsonify, request

    PluginUploadFile = Any  # type: ignore[misc,assignment]
    MODERN_WEB_API = False

    def json_response(
        data: Any = None,
        *,
        status_code: int = 200,
        headers: Optional[Dict[str, str]] = None,
    ):
        response = jsonify({} if data is None else data)
        response.status_code = status_code
        if headers:
            response.headers.update(headers)
        return response

    def error_response(
        message: str,
        *,
        status_code: int = 400,
        data: Any = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        return json_response(
            {"status": "error", "message": message, "data": data},
            status_code=status_code,
            headers=headers,
        )


def _query_value(key: str, default: Any = None, converter: Any = None) -> Any:
    source = request.query if MODERN_WEB_API else request.args
    if converter is None:
        return source.get(key, default)
    return source.get(key, default, type=converter)


async def _request_json() -> Dict[str, Any]:
    if MODERN_WEB_API:
        payload = await request.json(default={})
    else:  # pragma: no cover - compatibility for AstrBot < 4.26
        payload = await request.get_json(silent=True)
    return payload if isinstance(payload, dict) else {}


async def _request_upload() -> Any | None:
    if MODERN_WEB_API:
        files = await request.files()
    else:  # pragma: no cover - compatibility for AstrBot < 4.26
        files = await request.files
    return files.get("file")


async def _upload_bytes(upload: Any) -> bytes:
    result = upload.read(MAX_UPLOAD_BYTES + 1)
    if inspect.isawaitable(result):
        result = await result
    return bytes(result or b"")


def _request_username() -> str:
    username = getattr(request, "username", None) if MODERN_WEB_API else None
    return str(username or "dashboard").strip() or "dashboard"


def _bounded_text(value: Any, label: str, maximum: int, *, required: bool) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise ValueError(f"{label}不能为空")
    if len(text) > maximum:
        raise ValueError(f"{label}不能超过 {maximum} 个字符")
    return text


def _page_values(payload: Mapping[str, Any] | None = None) -> tuple[int, int]:
    payload = payload or {}
    try:
        page = max(1, int(payload.get("page", 1) or 1))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = max(10, min(60, int(payload.get("page_size", 30) or 30)))
    except (TypeError, ValueError):
        page_size = 30
    return page, page_size


class CatalogAdminService:
    """Read and mutate catalogue data for the authenticated plugin Page."""

    def __init__(self, store: CatalogStore):
        self.store = store
        self.upload_dir = store.webui_dir / "uploads"
        self.preferences_path = store.webui_dir / "preferences.json"
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self._thumbnail_cache: OrderedDict[tuple[Any, ...], str] = OrderedDict()
        self._thumbnail_lock = threading.RLock()
        self._cleanup_uploads()

    def _audit(
        self,
        action: str,
        target: str,
        summary: str,
        username: str,
    ) -> None:
        try:
            self.store.append_audit(action, target, summary, username)
        except Exception as exc:
            logger.exception("[%s] WebUI 操作记录写入失败: %s", PLUGIN_ID, exc)

    def _cleanup_uploads(self) -> None:
        cutoff = time.time() - 24 * 60 * 60
        for path in self.upload_dir.glob("*"):
            try:
                if path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                continue

    def _entry(self, entry_id: Any) -> Dict[str, Any]:
        entry = self.store.resolve_entry(str(entry_id or "").strip())
        if entry is None:
            raise ValueError("图鉴编号不存在")
        return entry

    @staticmethod
    def _group_id(value: Any) -> str:
        group_id = str(value or "").strip()
        if not group_id:
            raise ValueError("请先选择群组")
        return group_id

    @staticmethod
    def _user_id(value: Any) -> str:
        user_id = str(value or "").strip()
        if not user_id:
            raise ValueError("用户 ID 不能为空")
        return user_id

    def _thumbnail_data_uri(self, entry: Mapping[str, Any]) -> str:
        asset = self.store.asset_path(dict(entry))
        if asset is None:
            return ""
        try:
            stat = asset.stat()
            cache_key = (
                str(asset),
                stat.st_mtime_ns,
                stat.st_size,
                THUMBNAIL_SIZE,
            )
        except OSError:
            return ""
        with self._thumbnail_lock:
            cached = self._thumbnail_cache.get(cache_key)
            if cached is not None:
                self._thumbnail_cache.move_to_end(cache_key)
                return cached
        try:
            with Image.open(asset) as source:
                source.seek(0)
                image = source.convert("RGBA")
                image = ImageOps.contain(
                    image,
                    (THUMBNAIL_SIZE, THUMBNAIL_SIZE),
                    method=Image.Resampling.LANCZOS,
                )
                canvas = Image.new(
                    "RGBA",
                    (THUMBNAIL_SIZE, THUMBNAIL_SIZE),
                    (0, 0, 0, 0),
                )
                position = (
                    (THUMBNAIL_SIZE - image.width) // 2,
                    (THUMBNAIL_SIZE - image.height) // 2,
                )
                canvas.alpha_composite(image, position)
                output = BytesIO()
                canvas.save(output, format="WEBP", quality=82, method=4)
            uri = "data:image/webp;base64," + base64.b64encode(
                output.getvalue()
            ).decode("ascii")
        except Exception:
            return ""
        with self._thumbnail_lock:
            self._thumbnail_cache[cache_key] = uri
            while len(self._thumbnail_cache) > 256:
                self._thumbnail_cache.popitem(last=False)
        return uri

    def _entry_payload(
        self,
        entry: Mapping[str, Any],
        *,
        detail: bool = False,
        thumbnail: bool = False,
    ) -> Dict[str, Any]:
        profile = self.store.profile_for(entry)
        description = str(profile.get("description_zh") or "").strip()
        payload = {
            "id": int(entry.get("id", 0) or 0),
            "name": str(entry.get("name") or "未命名盟友"),
            "source": str(entry.get("source") or ""),
            "filename": str(entry.get("filename") or ""),
            "entry_key": str(entry.get("entry_key") or ""),
            "catalog_kind": str(entry.get("catalog_kind") or "legacy"),
            "variant_key": str(entry.get("variant_key") or ""),
            "page_title": str(entry.get("page_title") or ""),
            "debut_year": entry.get("debut_year"),
            "name_en": str(profile.get("name_en") or entry.get("page_title") or ""),
            "display_work": str(
                profile.get("display_work") or entry.get("source") or ""
            ),
            "source_url": str(profile.get("source_url") or ""),
            "description_origin": str(profile.get("description_origin") or "missing"),
            "description_missing": not bool(description),
            "description_excerpt": (
                description[:140] + ("..." if len(description) > 140 else "")
            ),
            "has_asset": self.store.asset_path(dict(entry)) is not None,
        }
        if detail:
            payload["description"] = description
            payload["aliases"] = list(entry.get("aliases", []))
            payload["metadata"] = {
                key: entry.get(key)
                for key in (
                    "asset_set",
                    "kind",
                    "debut_work",
                    "pageid",
                )
                if entry.get(key) not in {None, ""}
            }
        if thumbnail:
            payload["thumbnail"] = self._thumbnail_data_uri(entry)
        return payload

    @staticmethod
    def _catalog_reference_index(
        entries: List[Dict[str, Any]],
    ) -> Dict[str, str]:
        references: Dict[str, str] = {}
        for entry in entries:
            canonical = Path(str(entry.get("filename") or "")).name
            if not canonical:
                continue
            for value in (canonical, *entry.get("aliases", [])):
                filename = Path(str(value or "")).name
                if filename:
                    references[filename.casefold()] = canonical
        return references

    @staticmethod
    def _progress_count(
        user: Mapping[str, Any], reference_index: Mapping[str, str]
    ) -> int:
        unlocked = {
            reference_index[Path(str(item.get("ally_filename") or "")).name.casefold()]
            for item in user.get("unlocked", [])
            if Path(str(item.get("ally_filename") or "")).name.casefold()
            in reference_index
        }
        return len(unlocked)

    def summary(self, username: str) -> Dict[str, Any]:
        entries = self.store.entries()
        missing_assets = sum(self.store.asset_path(entry) is None for entry in entries)
        missing_descriptions = sum(
            not self.store.description_for(entry) for entry in entries
        )
        manual_entries = sum(
            str(entry.get("catalog_kind") or "") == "manual" for entry in entries
        )
        reference_index = self._catalog_reference_index(entries)
        groups = [
            self._group_summary(group_id, reference_index, len(entries))
            for group_id in self.store.group_ids()
        ]
        source_counts = Counter(
            str(entry.get("source") or "未标注") for entry in entries
        )
        kind_counts = Counter(
            str(entry.get("catalog_kind") or "legacy") for entry in entries
        )
        return {
            "catalog": {
                "entries": len(entries),
                "missing_assets": missing_assets,
                "missing_descriptions": missing_descriptions,
                "manual_entries": manual_entries,
                "sources": [
                    {"name": name, "count": count}
                    for name, count in source_counts.most_common()
                ],
                "kinds": [
                    {"name": name, "count": count}
                    for name, count in kind_counts.most_common()
                ],
            },
            "groups": {
                "count": len(groups),
                "users": sum(item["users"] for item in groups),
                "unlock_records": sum(item["unlock_records"] for item in groups),
                "draws_today": sum(item["draws_today"] for item in groups),
            },
            "trash": len(self.store.deleted_entries()),
            "recent_audit": self.store.audit_entries(8),
            "preferences": self.preferences(username),
            "today": get_today(),
        }

    def list_entries(self, params: Mapping[str, Any]) -> Dict[str, Any]:
        query = str(params.get("query") or "").strip().casefold()[:200]
        source = str(params.get("source") or "").strip()
        kind = str(params.get("kind") or "").strip()
        status = str(params.get("status") or "all").strip()
        sort = str(params.get("sort") or "id_asc").strip()
        page, page_size = _page_values(params)
        rows: List[tuple[Dict[str, Any], Dict[str, Any]]] = []
        for entry in self.store.entries():
            profile = self.store.profile_for(entry)
            description = str(profile.get("description_zh") or "").strip()
            has_asset = self.store.asset_path(entry) is not None
            if source and str(entry.get("source") or "") != source:
                continue
            if kind and str(entry.get("catalog_kind") or "legacy") != kind:
                continue
            if status == "missing_asset" and has_asset:
                continue
            if status == "missing_description" and description:
                continue
            if (
                status == "description_override"
                and profile.get("description_origin") != "override"
            ):
                continue
            if status == "manual" and str(entry.get("catalog_kind")) != "manual":
                continue
            if query:
                haystack = "\n".join(
                    str(value or "")
                    for value in (
                        entry.get("id"),
                        entry.get("name"),
                        entry.get("source"),
                        entry.get("filename"),
                        entry.get("page_title"),
                        profile.get("name_en"),
                        profile.get("display_work"),
                    )
                ).casefold()
                if query not in haystack:
                    continue
            rows.append((entry, profile))

        if sort == "id_desc":
            rows.sort(key=lambda item: int(item[0].get("id", 0)), reverse=True)
        elif sort == "name_asc":
            rows.sort(key=lambda item: str(item[0].get("name") or "").casefold())
        elif sort == "source_asc":
            rows.sort(
                key=lambda item: (
                    str(item[0].get("source") or "").casefold(),
                    int(item[0].get("id", 0)),
                )
            )
        else:
            rows.sort(key=lambda item: int(item[0].get("id", 0)))
        total = len(rows)
        start = (page - 1) * page_size
        selected = rows[start : start + page_size]
        return {
            "items": [
                self._entry_payload(entry, thumbnail=True) for entry, _ in selected
            ],
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": max(1, (total + page_size - 1) // page_size),
        }

    def entry_detail(self, entry_id: Any) -> Dict[str, Any]:
        return self._entry_payload(self._entry(entry_id), detail=True, thumbnail=True)

    def save_entry(self, payload: Mapping[str, Any], username: str) -> Dict[str, Any]:
        entry = self._entry(payload.get("id"))
        name = _bounded_text(payload.get("name"), "盟友名称", 160, required=True)
        source = _bounded_text(
            payload.get("source"), "首次登场作品", 240, required=False
        )
        description_action = str(payload.get("description_action") or "keep")
        description = ""
        if description_action == "set":
            description = _bounded_text(
                payload.get("description"),
                "简介",
                MAX_DESCRIPTION_CHARS,
                required=True,
            )
        elif description_action not in {"keep", "restore"}:
            raise ValueError("简介操作无效")

        changed_fields: List[str] = []
        if name != str(entry.get("name") or ""):
            changed_fields.append("名称")
        if source != str(entry.get("source") or ""):
            changed_fields.append("首次登场")
        if description_action == "set":
            changed_fields.append("简介")
        elif description_action == "restore":
            changed_fields.append("简介恢复为内置版本")

        entry = self.store.update_entry_details(
            entry,
            name,
            source,
            description_action=description_action,
            description=description,
            updated_by=username,
        )

        if not changed_fields:
            changed_fields.append("资料未变化")
        self._audit(
            "entry.update",
            f"#{entry['id']} {entry['name']}",
            "、".join(changed_fields),
            username,
        )
        return self.entry_detail(entry["id"])

    @staticmethod
    def _validated_image(data: bytes) -> tuple[bytes, str]:
        if not data:
            raise ValueError("没有收到图片文件")
        if len(data) > MAX_UPLOAD_BYTES:
            raise ValueError("图片不能超过 16 MB")
        try:
            with Image.open(BytesIO(data)) as image:
                image.verify()
            with Image.open(BytesIO(data)) as image:
                image_format = str(image.format or "PNG").lower()
        except Exception as exc:
            raise ValueError("图片格式无法识别") from exc
        extension = ".jpg" if image_format in {"jpeg", "jpg"} else f".{image_format}"
        if extension not in {".png", ".jpg", ".gif", ".bmp", ".webp"}:
            raise ValueError("仅支持 PNG、JPG、GIF、BMP 或 WebP")
        return data, extension

    def stage_upload(self, data: bytes) -> Dict[str, Any]:
        data, extension = self._validated_image(data)
        self._cleanup_uploads()
        token = uuid.uuid4().hex
        target = self.upload_dir / f"{token}{extension}"
        _atomic_write_bytes(target, data)
        try:
            with Image.open(BytesIO(data)) as image:
                size = {"width": image.width, "height": image.height}
        except Exception:
            size = {"width": 0, "height": 0}
        return {
            "token": token,
            "size": size,
            "bytes": len(data),
            "preview": self._thumbnail_from_bytes(data),
        }

    @staticmethod
    def _thumbnail_from_bytes(data: bytes) -> str:
        try:
            with Image.open(BytesIO(data)) as source:
                source.seek(0)
                image = ImageOps.contain(
                    source.convert("RGBA"),
                    (THUMBNAIL_SIZE, THUMBNAIL_SIZE),
                    method=Image.Resampling.LANCZOS,
                )
                output = BytesIO()
                image.save(output, format="WEBP", quality=82, method=4)
            return "data:image/webp;base64," + base64.b64encode(
                output.getvalue()
            ).decode("ascii")
        except Exception:
            return ""

    def _pending_upload(self, token: Any) -> Path:
        token = str(token or "").strip()
        if len(token) != 32 or any(
            character not in "0123456789abcdef" for character in token
        ):
            raise ValueError("上传凭据无效或已经过期")
        matches = list(self.upload_dir.glob(f"{token}.*"))
        if len(matches) != 1 or not matches[0].is_file():
            raise ValueError("上传凭据无效或已经过期")
        return matches[0]

    def add_entry(self, payload: Mapping[str, Any], username: str) -> Dict[str, Any]:
        name = _bounded_text(payload.get("name"), "盟友名称", 160, required=True)
        source = _bounded_text(
            payload.get("source"), "首次登场作品", 240, required=False
        )
        upload = self._pending_upload(payload.get("upload_token"))
        data = upload.read_bytes()
        self._validated_image(data)
        description = str(payload.get("description") or "").strip()
        if description:
            description = _bounded_text(
                description, "简介", MAX_DESCRIPTION_CHARS, required=True
            )
        entry = self.store.add_asset(
            name,
            data,
            source,
            description=description,
            updated_by=username,
        )
        try:
            upload.unlink()
        except OSError:
            pass
        self._audit(
            "entry.add",
            f"#{entry['id']} {entry['name']}",
            f"首次登场：{source or '未标注'}",
            username,
        )
        return self.entry_detail(entry["id"])

    def replace_image(
        self, entry_id: Any, data: bytes, username: str
    ) -> Dict[str, Any]:
        entry = self._entry(entry_id)
        data, _ = self._validated_image(data)
        self.store.replace_asset(entry, data)
        with self._thumbnail_lock:
            self._thumbnail_cache.clear()
        self._audit(
            "entry.image.replace",
            f"#{entry['id']} {entry['name']}",
            f"替换素材图片，{len(data)} bytes",
            username,
        )
        return self.entry_detail(entry["id"])

    def delete_entry(self, entry_id: Any, username: str) -> Dict[str, Any]:
        entry = self._entry(entry_id)
        result = self.store.delete_entry(entry, deleted_by=username)
        with self._thumbnail_lock:
            self._thumbnail_cache.clear()
        self._audit(
            "entry.delete",
            f"#{entry['id']} {entry['name']}",
            f"移入回收站，清理 {result['affected_users']} 位用户的引用",
            username,
        )
        return result

    def restore_entry(self, token: Any, username: str) -> Dict[str, Any]:
        entry = self.store.restore_deleted_entry(str(token or ""), restored_by=username)
        self._audit(
            "entry.restore",
            f"#{entry['id']} {entry['name']}",
            "从回收站恢复素材与可恢复的用户引用",
            username,
        )
        return self.entry_detail(entry["id"])

    def trash(self) -> Dict[str, Any]:
        return {"items": self.store.deleted_entries()}

    def _group_summary(
        self,
        group_id: str,
        reference_index: Optional[Mapping[str, str]] = None,
        total: Optional[int] = None,
    ) -> Dict[str, Any]:
        users = self.store.load_group(group_id)
        unique_unlocks: set[str] = set()
        unlock_records = 0
        last_activity = ""
        draws_today = 0
        bonuses_today = 0
        if reference_index is None or total is None:
            entries = self.store.entries()
            reference_index = self._catalog_reference_index(entries)
            total = len(entries)
        for user_id, user in users.items():
            unique_unlocks.update(
                reference_index[Path(filename).name.casefold()]
                for filename in self.store.unlocked_filenames(user)
                if Path(filename).name.casefold() in reference_index
            )
            unlock_records += len(user.get("unlocked", []))
            draws_today += self.store.draw_count(group_id, user_id)
            bonuses_today += self.store.draw_bonus(group_id, user_id)
            dates = [
                str(user.get("current", {}).get("date") or ""),
                *[
                    str(item.get("unlock_date") or "")
                    for item in user.get("unlocked", [])
                ],
            ]
            last_activity = max([last_activity, *dates])
        return {
            "group_id": group_id,
            "users": len(users),
            "unlock_records": unlock_records,
            "unique_unlocks": len(unique_unlocks),
            "completion": round(len(unique_unlocks) * 100 / total, 2) if total else 0,
            "draws_today": draws_today,
            "bonuses_today": bonuses_today,
            "last_activity": last_activity,
        }

    def list_groups(self, params: Mapping[str, Any]) -> Dict[str, Any]:
        query = str(params.get("query") or "").strip().casefold()[:200]
        page, page_size = _page_values(params)
        entries = self.store.entries()
        reference_index = self._catalog_reference_index(entries)
        rows = [
            self._group_summary(group_id, reference_index, len(entries))
            for group_id in self.store.group_ids()
            if not query or query in group_id.casefold()
        ]
        rows.sort(key=lambda item: (item["last_activity"], item["users"]), reverse=True)
        total = len(rows)
        start = (page - 1) * page_size
        return {
            "items": rows[start : start + page_size],
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": max(1, (total + page_size - 1) // page_size),
        }

    def _user_payload(
        self,
        group_id: str,
        user_id: str,
        user: Mapping[str, Any],
        *,
        detail: bool,
        reference_index: Optional[Mapping[str, str]] = None,
        total: Optional[int] = None,
    ) -> Dict[str, Any]:
        if reference_index is None or total is None:
            entries = self.store.entries()
            reference_index = self._catalog_reference_index(entries)
            total = len(entries)
        unlocked_count = self._progress_count(user, reference_index)
        current_filename = str(user.get("current", {}).get("ally_filename") or "")
        current = (
            self.store.resolve_entry(current_filename) if current_filename else None
        )
        payload = {
            "group_id": group_id,
            "user_id": user_id,
            "nickname": str(user.get("nickname") or "用户"),
            "no_new_count": max(0, int(user.get("no_new_count", 0) or 0)),
            "current": (self._entry_payload(current) if current is not None else None),
            "current_date": str(user.get("current", {}).get("date") or ""),
            "unlocked": unlocked_count,
            "unlock_records": len(user.get("unlocked", [])),
            "total": total,
            "completion": round(unlocked_count * 100 / total, 2) if total else 0,
            "draw_count": self.store.draw_count(group_id, user_id),
            "draw_bonus": self.store.draw_bonus(group_id, user_id),
        }
        if detail:
            unlocks = []
            for item in user.get("unlocked", []):
                entry = self.store.resolve_entry(str(item.get("ally_filename") or ""))
                if entry is None:
                    unlocks.append(
                        {
                            "id": 0,
                            "name": str(item.get("ally_filename") or "未知素材"),
                            "filename": str(item.get("ally_filename") or ""),
                            "unlock_date": str(item.get("unlock_date") or ""),
                            "missing": True,
                        }
                    )
                    continue
                row = self._entry_payload(entry)
                row["unlock_date"] = str(item.get("unlock_date") or "")
                row["missing"] = False
                unlocks.append(row)
            unlocks.sort(key=lambda item: (int(item.get("id", 0)), item["name"]))
            payload["unlocks"] = unlocks
        return payload

    def list_users(self, params: Mapping[str, Any]) -> Dict[str, Any]:
        group_id = self._group_id(params.get("group_id"))
        users = self.store.load_group(group_id)
        query = str(params.get("query") or "").strip().casefold()[:200]
        page, page_size = _page_values(params)
        entries = self.store.entries()
        reference_index = self._catalog_reference_index(entries)
        rows = [
            self._user_payload(
                group_id,
                user_id,
                user,
                detail=False,
                reference_index=reference_index,
                total=len(entries),
            )
            for user_id, user in users.items()
            if not query
            or query in user_id.casefold()
            or query in str(user.get("nickname") or "").casefold()
        ]
        rows.sort(key=lambda item: (item["unlocked"], item["nickname"]), reverse=True)
        total = len(rows)
        start = (page - 1) * page_size
        return {
            "items": rows[start : start + page_size],
            "group": self._group_summary(group_id),
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": max(1, (total + page_size - 1) // page_size),
        }

    def user_detail(self, group_id: Any, user_id: Any) -> Dict[str, Any]:
        group_id = self._group_id(group_id)
        user_id = self._user_id(user_id)
        users = self.store.load_group(group_id)
        user = users.get(user_id)
        if user is None:
            raise ValueError("群成员数据不存在")
        entries = self.store.entries()
        return self._user_payload(
            group_id,
            user_id,
            user,
            detail=True,
            reference_index=self._catalog_reference_index(entries),
            total=len(entries),
        )

    def save_user(self, payload: Mapping[str, Any], username: str) -> Dict[str, Any]:
        group_id = self._group_id(payload.get("group_id"))
        user_id = self._user_id(payload.get("user_id"))
        kwargs: Dict[str, Any] = {
            "nickname": _bounded_text(
                payload.get("nickname"), "群昵称", 160, required=True
            ),
            "no_new_count": max(0, int(payload.get("no_new_count", 0) or 0)),
        }
        if "current_id" in payload:
            current_id = str(payload.get("current_id") or "").strip()
            kwargs["current_filename"] = (
                self._entry(current_id)["filename"] if current_id else ""
            )
            kwargs["current_date"] = str(payload.get("current_date") or get_today())
        if "draw_count" in payload:
            kwargs["draw_count"] = max(0, int(payload.get("draw_count", 0) or 0))
        if "draw_bonus" in payload:
            kwargs["draw_bonus"] = max(0, int(payload.get("draw_bonus", 0) or 0))
        self.store.update_group_user_state(group_id, user_id, **kwargs)
        self._audit(
            "group.user.update",
            f"群 {group_id} / 用户 {user_id}",
            "更新昵称、当前盟友与今日计数",
            username,
        )
        return self.user_detail(group_id, user_id)

    def change_unlock(
        self, payload: Mapping[str, Any], username: str
    ) -> Dict[str, Any]:
        group_id = self._group_id(payload.get("group_id"))
        user_id = self._user_id(payload.get("user_id"))
        entry = self._entry(payload.get("entry_id"))
        action = str(payload.get("action") or "").strip()
        if action == "add":
            self.store.update_group_user(
                group_id,
                user_id,
                add_unlock_filename=str(entry["filename"]),
                unlock_date=str(payload.get("unlock_date") or get_today()),
            )
        elif action == "remove":
            self.store.update_group_user(
                group_id,
                user_id,
                remove_unlock_filename=str(entry["filename"]),
            )
        else:
            raise ValueError("解锁操作无效")
        self._audit(
            f"group.unlock.{action}",
            f"群 {group_id} / 用户 {user_id}",
            f"#{entry['id']} {entry['name']}",
            username,
        )
        return self.user_detail(group_id, user_id)

    def delete_user(self, payload: Mapping[str, Any], username: str) -> Dict[str, Any]:
        group_id = self._group_id(payload.get("group_id"))
        user_id = self._user_id(payload.get("user_id"))
        removed = self.store.delete_group_user(group_id, user_id)
        if not removed:
            raise ValueError("群成员数据不存在")
        self._audit(
            "group.user.delete",
            f"群 {group_id} / 用户 {user_id}",
            "删除用户图鉴、当前盟友和抽取计数",
            username,
        )
        return {"deleted": True, "group_id": group_id, "user_id": user_id}

    def reset_group_draws(
        self, payload: Mapping[str, Any], username: str
    ) -> Dict[str, Any]:
        group_id = self._group_id(payload.get("group_id"))
        result = self.store.reset_group_draws(group_id)
        self._audit(
            "group.draws.reset",
            f"群 {group_id}",
            f"清理 {result['users']} 位用户的今日计数",
            username,
        )
        return result

    def preferences(self, username: str) -> Dict[str, Any]:
        raw = _read_json(self.preferences_path, {})
        users = raw.get("users", {}) if isinstance(raw, dict) else {}
        value = users.get(username, {}) if isinstance(users, dict) else {}
        theme = str(value.get("theme") or "auto") if isinstance(value, dict) else "auto"
        if theme not in {"auto", "kirby", "dark"}:
            theme = "auto"
        return {"theme": theme}

    def save_preferences(
        self, payload: Mapping[str, Any], username: str
    ) -> Dict[str, Any]:
        theme = str(payload.get("theme") or "auto")
        if theme not in {"auto", "kirby", "dark"}:
            raise ValueError("主题选项无效")
        raw = _read_json(self.preferences_path, {})
        if not isinstance(raw, dict):
            raw = {}
        users = raw.setdefault("users", {})
        if not isinstance(users, dict):
            users = {}
            raw["users"] = users
        users[username] = {"theme": theme}
        _atomic_write_json(self.preferences_path, {"version": 1, "users": users})
        return {"theme": theme}


class KirbyCatalogWebUI:
    def __init__(
        self,
        context: Any,
        store: CatalogStore,
        write_lock: Optional[asyncio.Lock] = None,
    ) -> None:
        self.context = context
        self.service = CatalogAdminService(store)
        self.write_lock = write_lock or asyncio.Lock()

    def register(self) -> None:
        routes = [
            ("admin/summary", self.summary, ["GET"], "Kirby catalog summary"),
            ("admin/preferences", self.preferences, ["POST"], "Save WebUI preferences"),
            ("admin/entries", self.entries, ["GET"], "List catalog entries"),
            ("admin/entries/<entry_id>", self.entry, ["GET"], "Get catalog entry"),
            ("admin/entries/save", self.save_entry, ["POST"], "Save catalog entry"),
            ("admin/uploads/image", self.upload_image, ["POST"], "Stage catalog image"),
            ("admin/entries/add", self.add_entry, ["POST"], "Add catalog entry"),
            (
                "admin/entries/<entry_id>/image",
                self.replace_image,
                ["POST"],
                "Replace catalog image",
            ),
            (
                "admin/entries/delete",
                self.delete_entry,
                ["POST"],
                "Archive catalog entry",
            ),
            ("admin/trash", self.trash, ["GET"], "List archived entries"),
            (
                "admin/trash/restore",
                self.restore_entry,
                ["POST"],
                "Restore catalog entry",
            ),
            ("admin/groups", self.groups, ["GET"], "List catalog groups"),
            ("admin/groups/users", self.users, ["GET"], "List group users"),
            ("admin/groups/user", self.user, ["GET"], "Get group user"),
            ("admin/groups/user/save", self.save_user, ["POST"], "Save group user"),
            (
                "admin/groups/user/unlock",
                self.change_unlock,
                ["POST"],
                "Edit user unlock",
            ),
            (
                "admin/groups/user/delete",
                self.delete_user,
                ["POST"],
                "Delete group user",
            ),
            (
                "admin/groups/reset-draws",
                self.reset_group_draws,
                ["POST"],
                "Reset group draws",
            ),
            ("admin/audit", self.audit, ["GET"], "List WebUI audit records"),
        ]
        for path, handler, methods, description in routes:
            self.context.register_web_api(
                f"/{PLUGIN_ID}/{path}", handler, methods, description
            )

    async def _read(self, operation: Callable[..., Any], *args: Any) -> Any:
        try:
            return json_response(await asyncio.to_thread(operation, *args))
        except ValueError as exc:
            return error_response(str(exc), status_code=400)
        except Exception as exc:
            logger.exception("[%s] WebUI 读取失败: %s", PLUGIN_ID, exc)
            return error_response("读取图鉴管理数据失败", status_code=500)

    async def _write(self, operation: Callable[..., Any], *args: Any) -> Any:
        try:
            async with self.write_lock:
                result = await asyncio.to_thread(operation, *args)
            return json_response(result)
        except FileExistsError as exc:
            return error_response(str(exc), status_code=409)
        except ValueError as exc:
            return error_response(str(exc), status_code=400)
        except Exception as exc:
            logger.exception("[%s] WebUI 写入失败: %s", PLUGIN_ID, exc)
            return error_response("写入图鉴管理数据失败", status_code=500)

    async def summary(self):
        return await self._read(self.service.summary, _request_username())

    async def preferences(self):
        return await self._write(
            self.service.save_preferences,
            await _request_json(),
            _request_username(),
        )

    async def entries(self):
        params = {
            key: _query_value(key, default)
            for key, default in {
                "query": "",
                "source": "",
                "kind": "",
                "status": "all",
                "sort": "id_asc",
                "page": 1,
                "page_size": 30,
            }.items()
        }
        return await self._read(self.service.list_entries, params)

    async def entry(self, entry_id: str):
        return await self._read(self.service.entry_detail, entry_id)

    async def save_entry(self):
        return await self._write(
            self.service.save_entry,
            await _request_json(),
            _request_username(),
        )

    async def upload_image(self):
        upload = await _request_upload()
        if upload is None:
            return error_response("没有收到图片文件", status_code=400)
        data = await _upload_bytes(upload)
        return await self._write(self.service.stage_upload, data)

    async def add_entry(self):
        return await self._write(
            self.service.add_entry,
            await _request_json(),
            _request_username(),
        )

    async def replace_image(self, entry_id: str):
        upload = await _request_upload()
        if upload is None:
            return error_response("没有收到图片文件", status_code=400)
        data = await _upload_bytes(upload)
        return await self._write(
            self.service.replace_image, entry_id, data, _request_username()
        )

    async def delete_entry(self):
        payload = await _request_json()
        return await self._write(
            self.service.delete_entry, payload.get("id"), _request_username()
        )

    async def trash(self):
        return await self._read(self.service.trash)

    async def restore_entry(self):
        payload = await _request_json()
        return await self._write(
            self.service.restore_entry,
            payload.get("token"),
            _request_username(),
        )

    async def groups(self):
        params = {
            "query": _query_value("query", ""),
            "page": _query_value("page", 1),
            "page_size": _query_value("page_size", 30),
        }
        return await self._read(self.service.list_groups, params)

    async def users(self):
        params = {
            "group_id": _query_value("group_id", ""),
            "query": _query_value("query", ""),
            "page": _query_value("page", 1),
            "page_size": _query_value("page_size", 30),
        }
        return await self._read(self.service.list_users, params)

    async def user(self):
        return await self._read(
            self.service.user_detail,
            _query_value("group_id", ""),
            _query_value("user_id", ""),
        )

    async def save_user(self):
        return await self._write(
            self.service.save_user,
            await _request_json(),
            _request_username(),
        )

    async def change_unlock(self):
        return await self._write(
            self.service.change_unlock,
            await _request_json(),
            _request_username(),
        )

    async def delete_user(self):
        return await self._write(
            self.service.delete_user,
            await _request_json(),
            _request_username(),
        )

    async def reset_group_draws(self):
        return await self._write(
            self.service.reset_group_draws,
            await _request_json(),
            _request_username(),
        )

    async def audit(self):
        limit = _query_value("limit", 100, int)
        return await self._read(
            lambda: {"items": self.service.store.audit_entries(limit)}
        )


__all__ = ["CatalogAdminService", "KirbyCatalogWebUI"]
