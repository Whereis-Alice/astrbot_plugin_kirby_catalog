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
