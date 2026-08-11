from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

from astrbot_plugin_kirby_catalog.kirby_shinkaku import KirbyShinkakuClient
from astrbot_plugin_kirby_catalog.shinkaku_reference import (
    DEFAULT_COMPACT_COLUMNS,
    REFERENCE_RENDER_VERSION,
    render_shinkaku_reference_pages,
)
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "resources" / "shinkaku_reference.png"
MANIFEST_PATH = ROOT / "resources" / "shinkaku_reference_manifest.json"


def main() -> None:
    entries = KirbyShinkakuClient(cache_ttl_seconds=0).page_name_entries
    with TemporaryDirectory() as temporary:
        rendered = render_shinkaku_reference_pages(
            Path(temporary),
            entries,
            columns=DEFAULT_COMPACT_COLUMNS,
            single_image=True,
        )
        if len(rendered) != 1:
            raise RuntimeError(f"预期生成 1 张速查图，实际为 {len(rendered)} 张")
        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(rendered[0], OUTPUT_PATH)

    with Image.open(OUTPUT_PATH) as image:
        width, height = image.size
    digest = hashlib.sha256(OUTPUT_PATH.read_bytes()).hexdigest()
    MANIFEST_PATH.write_text(
        json.dumps(
            {
                "render_version": REFERENCE_RENDER_VERSION,
                "entries": len(entries),
                "columns": DEFAULT_COMPACT_COLUMNS,
                "width": width,
                "height": height,
                "bytes": OUTPUT_PATH.stat().st_size,
                "sha256": digest,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(MANIFEST_PATH.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
