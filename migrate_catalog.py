from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

try:
    from .catalog_migration import (
        DEFAULT_OVERRIDES_FILENAME,
        DEFAULT_RELEASE_ORDER_FILENAME,
        apply_plan,
        create_plan,
        validate_plan,
        write_reports,
    )
except ImportError:  # Direct execution from the plugin directory.
    from catalog_migration import (
        DEFAULT_OVERRIDES_FILENAME,
        DEFAULT_RELEASE_ORDER_FILENAME,
        apply_plan,
        create_plan,
        validate_plan,
        write_reports,
    )


PLUGIN_DIR = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="将星之卡比图鉴旧数据迁移到按 WiKirby 规范收集的新素材。默认仅生成报告。"
    )
    parser.add_argument(
        "--plugin-data", type=Path, required=True, help="现有插件数据目录"
    )
    parser.add_argument(
        "--new-assets", type=Path, required=True, help="新素材目录，例如 /Kirby"
    )
    parser.add_argument(
        "--report-dir",
        type=Path,
        help="报告目录；默认写入当前目录下的 kirby_migration_report",
    )
    parser.add_argument(
        "--release-order",
        type=Path,
        default=PLUGIN_DIR / "migration_data" / DEFAULT_RELEASE_ORDER_FILENAME,
    )
    parser.add_argument(
        "--overrides",
        type=Path,
        default=PLUGIN_DIR / "migration_data" / DEFAULT_OVERRIDES_FILENAME,
    )
    parser.add_argument(
        "--apply", action="store_true", help="正式替换数据；不传时只预演"
    )
    parser.add_argument(
        "--confirm",
        default="",
        help="正式迁移确认文本：REPLACE_OLD_KIRBY_DATA",
    )
    return parser.parse_args()


def main() -> int:
    if hasattr(os.sys.stdout, "reconfigure"):
        os.sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
        os.sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    args = parse_args()
    report_dir = (args.report_dir or (Path.cwd() / "kirby_migration_report")).resolve()

    def progress(done: int, total: int, result) -> None:
        if done % 25 == 0 or done == total:
            matched = "matched" if result.matched else "review"
            print(
                f"[match] {done}/{total} {matched}: {result.old_filename}", flush=True
            )

    plan = create_plan(
        old_root=args.plugin_data,
        new_assets_root=args.new_assets,
        report_dir=report_dir,
        release_order_path=args.release_order,
        overrides_path=args.overrides,
        progress=progress,
    )
    validate_plan(plan)
    write_reports(plan)
    print(json.dumps(plan.summary, ensure_ascii=False, indent=2), flush=True)
    print(f"[report] {report_dir}", flush=True)
    if not args.apply:
        print("[dry-run] 旧数据未修改。请先检查 CSV 和迁移复核.html。", flush=True)
        return 0
    backup = apply_plan(plan, args.confirm)
    print(f"[apply] 迁移完成，旧数据备份：{backup}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
