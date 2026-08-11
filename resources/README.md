# Catalog Profile Data

`catalog_profiles.json` is the generated profile database used by `今日盟友`,
`随机盟友`, and `查盟友`. It is keyed by the catalog's stable `entry_key`, so
renaming an image or display name does not disconnect its introduction.

Each record includes:

- official Chinese and English names when available;
- official Chinese and English debut-work names when available;
- a simplified Chinese introduction and the extracted English source text;
- the WiKirby source page, revision ID, and timestamp;
- variant context for independently cataloged abilities, forms, and EX entries.

Do not edit this generated file to make a server-specific correction. Use the
administrator command `星之卡比图鉴简介` instead; overrides are stored in the
plugin data directory at `config/description_overrides.json` and survive plugin
updates.

Maintainers can rebuild the database with `tools/build_catalog_profiles.py`.
The tool reads the two collection manifests and cached WiKirby page sources,
keeps a resumable translation cache, and writes these audit files:

- `图鉴中英文名称与简介.csv`;
- `未匹配简介.csv`;
- `生成汇总.json`.

See [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) for attribution and
license details.

## 真格 Wiki 三语页面名称

`shinkaku_page_names.json` 是真格攻略 Wiki 的运行时页面名称索引，按
2026-08-11 的公开“页面一覧”逐页覆盖全部 301 个唯一页面。每条记录包含：

- 完整简体中文、英文和日文标题；
- 所属作品、原站栏目和页面分类；
- 中英文名称来源状态：官方译名、沿用系列官译、本项目自译或原文保留；
- 稳定目录序号、原始页面列表序号、查询别名和来源 URL。

同目录下同时提供便于人工核对的 `shinkaku_page_names.csv` 和
`shinkaku_page_names.md`。运行以下命令可以检查数量、唯一性、必填字段和
别名完整性：

```bash
python tools/audit_shinkaku_page_names.py
```

维护者取得新的页面列表快照后，可附加
`--source-snapshot path/to/shinkaku_pages_raw.json` 做逐 URL 覆盖比对。
