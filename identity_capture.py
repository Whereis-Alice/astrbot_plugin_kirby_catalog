"""Collect OneBot profile nicknames and group cards without changing progress."""

from __future__ import annotations

from typing import Any

from .catalog_core import get_today


def parse_member_names(payload: Any) -> dict[str, dict[str, str]]:
    if isinstance(payload, dict):
        if payload.get("status") == "failed" or payload.get("retcode", 0) not in (
            0,
            None,
        ):
            raise ValueError("协议端获取群成员失败")
        payload = payload.get("data")
    if not isinstance(payload, list):
        raise ValueError("协议端未返回群成员列表")
    result: dict[str, dict[str, str]] = {}
    conflicts: set[str] = set()
    for member in payload:
        if not isinstance(member, dict):
            continue
        user = str(member.get("user_id") or "")
        profile = str(member.get("nickname") or "").strip()
        if not user.isdigit() or not profile:
            continue
        names = {
            "profile": profile,
            "group_card": str(member.get("card") or "").strip(),
        }
        if user in result and result[user] != names:
            conflicts.add(user)
        result[user] = names
    for user in conflicts:
        result.pop(user, None)
    return result


def save_member_names(
    store: Any, group: str, names: dict[str, dict[str, str]]
) -> dict[str, int]:
    """Reload under the store lock so draws during the API call aren't lost."""
    with store._lock:
        config = store.load_group(group)
        users = {user for user in config if user.isdigit()}
        stats = {"records": len(users), "captured": 0, "updated": 0, "missing": 0}
        for user in users:
            profile = names.get(user)
            if not profile:
                stats["missing"] += 1
                continue
            stats["captured"] += 1
            recorded = {**profile, "source": "onebot_member_list", "date": get_today()}
            if config[user].get("identity_names") != recorded:
                config[user]["identity_names"] = recorded
                stats["updated"] += 1
        if stats["updated"]:
            store.save_group(group, config)
        return stats
