"""Verified QQ identity links and recoverable collection merges.

Platform OpenIDs stay at the delivery boundary. Links resolve data addresses,
so old and new adapters share one record rather than copying it on every draw.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import time
import uuid
from copy import deepcopy
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from .catalog_core import (
    NON_GROUP_CONFIG_FILENAMES,
    _atomic_write_json,
    _normalise_unlocked,
    _restore_file,
    _safe_data_id,
    _snapshot_file,
    normalise_group_config,
)

IDENTITY_LINKS_FILENAME = "identity_links.json"
CODE_TTL = 20 * 60
MAX_PENDING_CODES = 500


@dataclass(frozen=True)
class MigrationResult:
    status: str

    @property
    def message(self) -> str:
        messages = {
            "bound": "已恢复你的旧收藏，之后会自动关联。",
            "group_unlinked": "当前群尚未关联旧群，请管理员先设置新旧群对应关系。",
            "name_missing": "官方 Bot 本次没有提供可用于匹配的资料名称，暂时无法自动恢复旧收藏。",
            "legacy_names_missing": "旧群数据尚未采集 QQ 资料昵称，请管理员在旧 OneBot 执行「采集盟友迁移资料」后迁移数据。",
            "name_not_found": "没有找到与当前资料名称一致的旧收藏记录。",
            "ambiguous": "旧群中有多个相同资料昵称，无法确定你的旧收藏。",
            "claimed": "匹配到的旧收藏已关联其他官方身份，无法自动认领。",
        }
        message = messages.get(self.status, "")
        if message and self.status != "bound":
            message += "\n当前使用新身份记录；可发送「盟友身份」按提示绑定旧账号。"
        return message


def digest(*parts: str) -> str:
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()


def scope_key(bot: str, scope: str) -> str:
    return digest(bot, scope)


def member_key(bot: str, scope: str, user: str) -> str:
    return digest(scope_key(bot, scope), user)


def unlinked_user(bot: str, user: str) -> str:
    return "qqu_" + digest(bot, user)[:32]


def empty_state() -> dict[str, Any]:
    return {"version": 1, "groups": {}, "users": {}, "codes": {}}


def validate_state(raw: Any, *, include_codes: bool = True) -> dict[str, Any]:
    """Reject malformed or path-bearing links, including imported backups."""
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise ValueError("身份绑定文件格式无效")
    result = empty_state()
    for kind in ("groups", "users", "codes"):
        values = raw.get(kind, {})
        if not isinstance(values, dict):
            raise ValueError("身份绑定文件格式无效")
        if kind == "codes" and not include_codes:
            continue
        for key, record in values.items():
            if not re.fullmatch(r"[0-9a-f]{64}", key) or not isinstance(record, dict):
                raise ValueError("身份绑定记录格式无效")
            item = deepcopy(record)
            item["group"] = _safe_data_id(item.get("group"), "旧群号")
            if item["group"].lower() + ".json" in NON_GROUP_CONFIG_FILENAMES:
                raise ValueError("旧群号不可指向配置文件")
            if kind == "users" or (kind == "codes" and item.get("kind") == "user"):
                item["user"] = _safe_data_id(item.get("user"), "旧用户 ID")
            if kind == "users" and not re.fullmatch(
                r"[0-9a-f]{64}", str(item.get("scope", ""))
            ):
                raise ValueError("身份绑定会话无效")
            if item.get("bot_user"):
                item["bot_user"] = _safe_data_id(item["bot_user"], "旧 Bot ID")
            if kind == "codes":
                if item.get("kind") not in {"user", "group"} or not isinstance(
                    item.get("expires"), (int, float)
                ):
                    raise ValueError("绑定码记录无效")
                if item["kind"] == "user" and not re.fullmatch(
                    r"[0-9a-f]{24}", str(item.get("recipient", ""))
                ):
                    raise ValueError("绑定码的目标身份无效")
            result[kind][key] = item
    for item in result["users"].values():
        group_link = result["groups"].get(item["scope"])
        if group_link and group_link["group"] != item["group"]:
            raise ValueError("个人绑定与群绑定指向不同旧群")
    return result


def merge_backup(current: Any, incoming: Any) -> dict[str, Any]:
    merged = validate_state(current or empty_state(), include_codes=False)
    incoming = validate_state(incoming, include_codes=False)
    for kind in ("groups", "users"):
        for key, item in incoming[kind].items():
            existing = merged[kind].get(key)
            if existing and any(
                existing.get(k) != item.get(k)
                for k in ("group", "user", "bot_user", "scope")
            ):
                raise ValueError("备份的身份绑定与现有绑定冲突，请核对后整体恢复")
            merged[kind][key] = item
    return validate_state(merged, include_codes=False)


def _valid_day(value: Any) -> str:
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError:
        return ""


def merge_user(target: dict, source: dict) -> dict:
    """Preserve first unlocks and the newest usable current ally."""
    merged = deepcopy(target or source)
    merged["unlocked"] = _normalise_unlocked(
        target.get("unlocked", []) + source.get("unlocked", [])
    )
    old_current, new_current = target.get("current", {}), source.get("current", {})
    if new_current.get("ally_filename") and _valid_day(new_current.get("date")):
        if not old_current.get("ally_filename") or _valid_day(
            new_current.get("date")
        ) > _valid_day(old_current.get("date")):
            merged["current"] = deepcopy(new_current)
    merged["no_new_count"] = max(
        int(target.get("no_new_count", 0)), int(source.get("no_new_count", 0))
    )
    if target.get("nickname") and target["nickname"] != "用户":
        merged["nickname"] = target["nickname"]
    else:
        merged["nickname"] = source.get("nickname") or "用户"
    return merged


class IdentityLinks:
    def __init__(self, store: Any):
        self.store = store
        self.path = store.config_dir / IDENTITY_LINKS_FILENAME
        self.journal = store.root / "identity-links-transaction.json"
        self.recover()

    def _journal_target(self, relative: str) -> Path:
        # Journal filenames are data, never arbitrary filesystem destinations.
        if not re.fullmatch(r"config/[A-Za-z0-9_-]{1,128}\.json", relative):
            raise ValueError("身份迁移事务路径无效")
        name = Path(relative).name.lower()
        if name in NON_GROUP_CONFIG_FILENAMES and name not in {
            IDENTITY_LINKS_FILENAME,
            "draw_limits.json",
            "draw_bonuses.json",
        }:
            raise ValueError("身份迁移事务目标无效")
        target = self.store.root / relative
        if target.resolve().parent != self.store.config_dir.resolve():
            raise ValueError("身份迁移事务路径越界")
        return target

    def recover(self) -> None:
        """An interrupted merge rolls back before the store loads counters."""
        with self.store._lock:
            if not self.journal.exists():
                return
            raw = json.loads(self.journal.read_text(encoding="utf-8"))
            if raw.get("version") != 1 or not isinstance(raw.get("before"), dict):
                raise ValueError("身份迁移事务损坏，请恢复数据备份")
            snapshots = []
            for relative, encoded in raw["before"].items():
                target = self._journal_target(relative)
                snapshots.append(
                    (
                        target,
                        None
                        if encoded is None
                        else base64.b64decode(encoded, validate=True),
                    )
                )
            for target, snapshot in snapshots:
                _restore_file(target, snapshot)
            self.journal.unlink()

    def ensure_ready(self) -> None:
        """Retry unfinished rollback before any adapter can access the store."""
        if self.journal.exists():
            self.recover()
            self.store._load_draw_limits()
            self.store._load_draw_bonuses()

    def _state(self) -> dict[str, Any]:
        self.ensure_ready()
        if not self.path.exists():
            return empty_state()
        return validate_state(json.loads(self.path.read_text(encoding="utf-8")))

    def group(self, bot: str, scope: str) -> str:
        with self.store._lock:
            return (
                self._state()["groups"]
                .get(scope_key(bot, scope), {})
                .get("group", scope)
            )

    def resolve(
        self, bot: str, scope: str, user: str, *, fallback_scope: str = ""
    ) -> tuple[str, str]:
        with self.store._lock:
            state = self._state()
            link = state["users"].get(member_key(bot, scope, user))
            if link:
                return link["group"], link["user"]
            group = state["groups"].get(scope_key(bot, scope))
            return (
                (group["group"], unlinked_user(bot, user))
                if group
                else (fallback_scope or scope, user)
            )

    def bot_user(self, bot: str, scope: str, fallback: str) -> str:
        with self.store._lock:
            return (
                self._state()["groups"].get(scope_key(bot, scope), {}).get("bot_user")
                or fallback
            )

    def has_user(self, bot: str, scope: str, user: str) -> bool:
        with self.store._lock:
            return member_key(bot, scope, user) in self._state()["users"]

    def issue(
        self,
        kind: str,
        group: str,
        user: str = "",
        bot_user: str = "",
        *,
        recipient: str = "",
    ) -> str:
        """Called only after entrypoint verifies private chat and ownership/admin."""
        group = _safe_data_id(group, "旧群号")
        if not group.isdigit() or kind not in {"user", "group"}:
            raise ValueError("请填写 OneBot 旧群号")
        with self.store._lock:
            config = self.store.load_group(group)
            if not config:
                raise ValueError("没有找到该旧群的数据，请先导入配置整包")
            record = {"kind": kind, "group": group, "expires": time.time() + CODE_TTL}
            if kind == "user":
                user = _safe_data_id(user, "旧 QQ 号")
                if not user.isdigit() or user not in config:
                    raise ValueError("没有找到该 QQ 号的旧收藏记录")
                record["user"] = user
                recipient = recipient.strip().lower()
                if not re.fullmatch(r"[0-9a-f]{24}", recipient):
                    raise ValueError(
                        "请先在目标官方群或单聊发送「盟友身份」，复制目标身份码"
                    )
                record["recipient"] = recipient
            if bot_user:
                if not bot_user.startswith("bot_") or bot_user not in config:
                    raise ValueError("没有找到指定的旧 Bot 收藏身份")
                record["bot_user"] = _safe_data_id(bot_user, "旧 Bot ID")
            state = self._state()
            state["codes"] = {
                key: value
                for key, value in state["codes"].items()
                if value["expires"] > time.time()
            }
            # Reissuing for one old identity revokes its previous unconsumed code.
            state["codes"] = {
                key: value
                for key, value in state["codes"].items()
                if (value["kind"], value["group"], value.get("user"))
                != (kind, group, record.get("user"))
            }
            if len(state["codes"]) >= MAX_PENDING_CODES:
                raise ValueError("待使用的绑定码过多，请稍后再试")
            code = secrets.token_hex(12).upper()
            state["codes"][digest(code)] = record
            _atomic_write_json(self.path, state)
            return code

    def _commit(self, files: dict[Path, Any]) -> None:
        before = {}
        for path in files:
            relative = path.relative_to(self.store.root).as_posix()
            self._journal_target(relative)
            snapshot = _snapshot_file(path)
            before[relative] = (
                None if snapshot is None else base64.b64encode(snapshot).decode("ascii")
            )
        snapshot = {"version": 1, "before": before}
        # Keep reviewable successful-merge snapshots as well as crash recovery.
        backups = self.store.root / "identity-backups"
        _atomic_write_json(
            backups / f"{time.time_ns()}-{uuid.uuid4().hex}.json", snapshot
        )
        _atomic_write_json(self.journal, snapshot)
        try:
            for path, value in files.items():
                _atomic_write_json(path, value)
            self.journal.unlink()
        except Exception:
            self.recover()
            raise
        finally:
            self.store._load_draw_limits()
            self.store._load_draw_bonuses()
        for previous in sorted(backups.glob("*.json"), key=lambda path: path.name)[
            :-20
        ]:
            if (
                re.fullmatch(r"[0-9]+-[0-9a-f]{32}\.json", previous.name)
                and previous.resolve().parent == backups.resolve()
            ):
                try:
                    previous.unlink()
                except OSError:
                    pass

    def auto_bind_by_name(
        self,
        bot: str,
        scope: str,
        user: str,
        profile_name: str,
        *,
        source_scope: str = "",
    ) -> bool:
        return (
            self.try_auto_bind_by_name(
                bot, scope, user, profile_name, source_scope=source_scope
            ).status
            == "bound"
        )

    def try_auto_bind_by_name(
        self,
        bot: str,
        scope: str,
        user: str,
        profile_name: str,
        *,
        source_scope: str = "",
    ) -> MigrationResult:
        """Opt-in heuristic: exact unique profile name in an admin-linked group.

        Names are not account proof. The explicit configuration is the
        administrator's decision to allow this convenience heuristic.
        """
        name = str(profile_name or "").strip()
        with self.store._lock:
            state = self._state()
            key, skey = member_key(bot, scope, user), scope_key(bot, scope)
            if key in state["users"]:
                return MigrationResult("already_bound")
            if skey not in state["groups"]:
                return MigrationResult("group_unlinked")
            if not name or name in {user, "用户", "unknown", "qq_official"}:
                return MigrationResult("name_missing")
            group = state["groups"][skey]["group"]
            config = self.store.load_group(group)
            if not any(
                uid.isdigit()
                and isinstance(record.get("identity_names"), dict)
                and record["identity_names"].get("profile")
                for uid, record in config.items()
            ):
                return MigrationResult("legacy_names_missing")
            matches = [
                uid
                for uid, record in config.items()
                if uid.isdigit()
                and isinstance(record.get("identity_names"), dict)
                and record["identity_names"].get("profile") == name
            ]
            if not matches:
                return MigrationResult("name_not_found")
            if len(matches) > 1:
                return MigrationResult("ambiguous")
            old_user = matches[0]
            subject = digest(bot, user)
            if any(
                link["group"] == group
                and link["user"] == old_user
                and link.get("subject") != subject
                for link in state["users"].values()
            ):
                return MigrationResult("claimed")
            code = secrets.token_hex(12).upper()
            state["codes"][digest(code)] = {
                "kind": "user",
                "group": group,
                "user": old_user,
                "recipient": key[:24],
                "expires": time.time() + CODE_TTL,
            }
            self._bind(
                "user",
                bot,
                scope,
                user,
                code,
                current_bot="",
                source_scope=source_scope,
                state=state,
                method="profile_name",
            )
            return MigrationResult("bound")

    def bind(
        self,
        kind: str,
        bot: str,
        scope: str,
        user: str,
        code: str,
        *,
        current_bot: str = "",
        source_scope: str = "",
    ) -> tuple[str, str]:
        with self.store._lock:
            return self._bind(
                kind,
                bot,
                scope,
                user,
                code,
                current_bot=current_bot,
                source_scope=source_scope,
            )

    def _bind(
        self,
        kind: str,
        bot: str,
        scope: str,
        user: str,
        code: str,
        *,
        current_bot: str,
        source_scope: str,
        state: dict[str, Any] | None = None,
        method: str = "verified_code",
    ) -> tuple[str, str]:
        scope = _safe_data_id(scope, "当前会话")
        if scope.lower() + ".json" in NON_GROUP_CONFIG_FILENAMES:
            raise ValueError("当前会话不可指向配置文件")
        user = _safe_data_id(user, "当前用户")
        code = code.strip().upper()
        if not re.fullmatch(r"[0-9A-F]{24}", code):
            raise ValueError("绑定码无效或已过期")
        state = state if state is not None else self._state()
        skey, ukey, ckey = (
            scope_key(bot, scope),
            member_key(bot, scope, user),
            digest(code),
        )
        bucket, key = ("groups", skey) if kind == "group" else ("users", ukey)
        existing = state[bucket].get(key)
        if existing and existing.get("code_digest") == ckey:
            return existing["group"], existing.get("user", "")
        record = state["codes"].get(ckey)
        if not record or record["kind"] != kind or record["expires"] <= time.time():
            raise ValueError("绑定码无效或已过期")
        if kind == "user" and not secrets.compare_digest(
            record["recipient"], ukey[:24]
        ):
            raise ValueError("此绑定码不属于当前官方身份，请在领取身份码的原会话使用")
        if existing:
            raise ValueError("当前身份已经绑定；不能用新码覆盖原绑定")
        target_group = record["group"]
        if scope == target_group:
            raise ValueError("新旧会话数据地址相同，请核对平台标识")
        group_link = state["groups"].get(skey)
        if kind == "user" and group_link and group_link["group"] != target_group:
            raise ValueError("绑定码的旧群与当前官方群绑定不一致")
        if kind == "group" and any(
            link["scope"] == skey and link["group"] != target_group
            for link in state["users"].values()
        ):
            raise ValueError("该会话已有成员绑定到其他旧群，请核对旧群号")
        target_user = record.get("user", "")
        files: dict[Path, Any] = {}
        groups: dict[str, dict] = {}
        counters = [
            deepcopy(self.store._draw_limits),
            deepcopy(self.store._draw_bonuses),
        ]

        def load(group: str) -> dict:
            if group not in groups:
                groups[group] = normalise_group_config(self.store.load_group(group))
            return groups[group]

        def move(source_group: str, source_user: str, dest_user: str) -> None:
            if (source_group, source_user) == (target_group, dest_user):
                return
            source, target = load(source_group), load(target_group)
            old = source.pop(source_user, None)
            if old:
                current = old.get("current", {})
                if not self.store.resolve_entry(current.get("ally_filename", "")):
                    old["current"] = {"ally_filename": "", "date": ""}
                target[dest_user] = merge_user(target.get(dest_user, {}), old)
            for index, data in enumerate(counters):
                dates = data.get(source_group, {}).pop(source_user, {})
                if dates:
                    dest = data.setdefault(target_group, {}).setdefault(dest_user, {})
                    for day, count in dates.items():
                        if not _valid_day(day):
                            continue
                        count = max(0, int(count or 0))
                        previous = max(0, int(dest.get(day, 0) or 0))
                        dest[day] = (
                            previous + count if index == 0 else max(previous, count)
                        )
                if source_group in data and not data[source_group]:
                    data.pop(source_group)

        if kind == "user":
            source_group, source_user = self.resolve(
                bot, scope, user, fallback_scope=source_scope
            )
            move(source_group, source_user, target_user)
            state["users"][ukey] = {
                "scope": skey,
                "group": target_group,
                "user": target_user,
                "code_digest": ckey,
                "subject": digest(bot, user),
                "method": method,
            }
        else:
            # Move unbound official members into a Bot namespace in the old group.
            source_users = set(load(scope))
            for data in counters:
                source_users.update(data.get(scope, {}))
            for source_user in source_users:
                link = state["users"].get(member_key(bot, scope, source_user))
                dest = link["user"] if link else unlinked_user(bot, source_user)
                if source_user.startswith("bot_"):
                    dest = (
                        record.get("bot_user")
                        if source_user == current_bot and record.get("bot_user")
                        else source_user
                    )
                move(scope, source_user, dest)
            state["groups"][skey] = {"group": target_group, "code_digest": ckey}
            if record.get("bot_user"):
                state["groups"][skey]["bot_user"] = record["bot_user"]
        state["codes"].pop(ckey)
        for group, config in groups.items():
            files[self.store.config_dir / f"{group}.json"] = config
        files[self.store.draw_limits_path] = counters[0]
        files[self.store.draw_bonuses_path] = counters[1]
        files[self.path] = validate_state(state)
        self._commit(files)
        return target_group, target_user
