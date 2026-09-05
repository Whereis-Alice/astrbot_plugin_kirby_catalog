# 数据目录与迁移

[← 返回 README](../README.md)

新插件使用独立标识符 `astrbot_plugin_kirby_catalog`，不会与上游插件共用写入目录。

默认数据目录：

```text
data/plugin_data/astrbot_plugin_kirby_catalog
```

例如 AstrBot 安装在 `/root/AstrBot` 时，完整路径通常为：

```text
/root/AstrBot/data/plugin_data/astrbot_plugin_kirby_catalog
```

盟友图片会保存到：

```text
data/plugin_data/astrbot_plugin_kirby_catalog/img/allies
```

管理员修改的简介独立保存在：

```text
data/plugin_data/astrbot_plugin_kirby_catalog/config/description_overrides.json
```

该文件只保存人工覆盖，不复制内置简介；插件更新、素材改名和规范迁移都会保留它。

管理台自身数据位于：

```text
data/plugin_data/astrbot_plugin_kirby_catalog/webui/
├── audit.json                 # Dashboard 操作记录
├── catalog_tombstones.json    # 回收站索引和保留编号
├── preferences.json           # Dashboard 用户皮肤偏好（旧主题名自动迁移）
├── trash/                     # 已删除素材、资料和用户引用快照
└── uploads/                   # 尚未提交的临时上传，24 小时后自动清理
```

名称库的内置版本随插件发布，管理员修改只保存为覆盖层：

```text
data/plugin_data/astrbot_plugin_kirby_catalog/config/terminology_overrides.json
```

覆盖层不会改写 `resources/kirby_terminology.json`，也不会被误识别为群数据。升级插件时内置名称库可以继续补充；管理台的“恢复内置”只删除指定条目的覆盖版本。

这些文件和 `catalog.json`、`config/*.json`、`img/allies/*` 共同组成完整图鉴数据。迁移、备份或服务器搬迁时应复制整个 `astrbot_plugin_kirby_catalog` 数据目录，而不是只复制图片。

## 规范素材与独立形态迁移

`migrate_catalog.py` 用于把已经运行过的旧图鉴整体迁移到 WiKirby 规范角色素材，并合并独立的能力与形态素材。它不会从网络下载图片；基础素材和补充素材必须分别准备，两个目录都要保留自己的 `_收集记录`：

```text
/Kirby/
├── _收集记录/候选清单.json
├── _收集记录/收集清单.csv
└── 1078 张规范角色图片

/Kirby_Forms/
├── _收集记录/候选清单.json
├── _收集记录/收集清单.csv
└── 275 张能力、形态与 EX 图片
```

本项目的基础清单包含 1092 个来源页面。迁移规则会排除物件、机制、地点、组织、种族或类型汇总等 14 个非角色页面，建立 1078 个基础角色条目。补充清单再加入 275 个独立条目，包括 75 种普通复制能力、进化能力、超级能力、塞满嘴、机器人装甲模式、64 组合能力、毛线变身、盟友能力、卡比猎人职业、EX、结晶化与其他形态，最终总图鉴为 1353 项。

每个补充条目都使用独立 `entry_key`。即使多个条目来自同一个 WiKirby 页面，普通版、EX、元素形态、服装形态和阶段形态也不会互相覆盖或合并；抽到其中一个只会解锁该条目，个人进度和排行榜分别增加 `1`。

迁移后的编号首先按角色或形态最早登场作品的年份从早到晚排列。同一年内按发行映射文件声明的作品顺序排列；同一作品内优先列出主角卡比，再按条目类型、页面标题和变体标识稳定排序。没有可靠首作年份的条目排在最后，并在 `新图鉴编号.csv` 中标记为“待确认”。

> [!WARNING]
> v3 会重新生成全部图鉴编号，旧消息中的 `#编号` 不再代表原来的角色。用户当前盟友和解锁记录会改写为新素材文件名。正式迁移前必须停用插件，避免 dry-run 后又产生新的抽取记录。

> [!IMPORTANT]
> 如果服务器已经执行过 v3.0 或 v3.0.1，活动目录中的能力、形态和 EX 历史可能已经被合并。此时不能只迁移当前活动目录，必须用 `--history-source` 同时读取迁移前自动生成的 `.before-v3-*` 备份。工具会读取活动目录中的 `migration_state.json` 和对应迁移报告，先移除上次迁移产生的旧映射基线，再保留迁移后真正新增的抽取，最后按 v3.1 规则恢复历史形态。这样不会把旧版错误归并的普通角色和正确形态同时计数。当前昵称、累计次数、连续未出新计数等字段仍以活动目录为准，历史源不会被修改。

正式执行会在活动数据目录旁构建完整暂存副本，请确保该文件系统的可用空间大于新素材目录体积。下面使用 AstrBot 自己的虚拟环境 Python；安装路径不同时请相应调整。

以 AstrBot 安装在 `/root/AstrBot`、基础素材位于 `/Kirby`、补充素材位于 `/Kirby_Forms` 为例，先通过 WebUI 停用并更新插件，再进入插件目录执行预演。尚未执行过任何 v3 规范迁移时使用：

```bash
cd /root/AstrBot/data/plugins/astrbot_plugin_kirby_catalog
/root/AstrBot/.venv/bin/python migrate_catalog.py \
  --plugin-data /root/AstrBot/data/plugin_data/astrbot_plugin_kirby_catalog \
  --new-assets /Kirby \
  --supplemental-assets /Kirby_Forms \
  --report-dir /root/kirby_migration_report
```

已经执行过 v3.0.x 时，先找到迁移前备份：

```bash
find /root/AstrBot/data/plugin_data -maxdepth 1 -type d -name 'astrbot_plugin_kirby_catalog.before-v3-*' -print
```

然后在预演命令中加入实际备份路径，例如：

```bash
/root/AstrBot/.venv/bin/python migrate_catalog.py \
  --plugin-data /root/AstrBot/data/plugin_data/astrbot_plugin_kirby_catalog \
  --history-source /root/AstrBot/data/plugin_data/astrbot_plugin_kirby_catalog.before-v3-20260804-120000 \
  --new-assets /Kirby \
  --supplemental-assets /Kirby_Forms \
  --report-dir /root/kirby_migration_report
```

`--history-source` 可以重复传入。存在多个有效的迁移前备份时可以全部加入，工具会按规范文件名取并集，不会重复增加进度。不要把当前活动目录填作历史源。

已经执行过 v3.0.x 的活动目录必须保留 `migration_reports`。若迁移器找不到与 `migration_state.json` 对应的 `migration_plan.json`，会主动停止，而不是用简单并集制造虚高进度。

不传 `--apply` 时只生成报告，不会修改旧数据。至少检查以下文件：

| 报告 | 用途 |
| --- | --- |
| `migration_summary.json` | 新旧条目、基线校正、解锁恢复率、展开和漏迁总数 |
| `旧素材匹配报告.csv` | 每个旧条目的匹配方法、目标和候选项 |
| `漏迁用户记录.csv` | 无法恢复的群、用户、素材和解锁日期 |
| `用户迁移影响.csv` | 每个数据源中每位用户的映射、展开与去重结果 |
| `历史数据合并.csv` | 活动解锁、剥离的旧基线、迁移后新增、历史恢复和最终有效数 |
| `新图鉴编号.csv` | 新编号、首作年份、作品、名称和文件名 |
| `已排除非角色页.csv` | 未进入新图鉴的来源页面、文件名和排除原因 |
| `迁移复核.html` | 低置信度、一对多和未匹配条目的图片对照页 |

匹配顺序包含人工覆盖、图片 SHA-256、规范名称、感知哈希和保守的角色匹配。旧能力、EX 和形态会优先迁移到各自的独立 `entry_key`，不会因共用页面标题而落回普通版。组合图片可以人工展开到多个已核验条目；多个旧记录确实指向同一条目时，只保留该用户最早的解锁日期。无法可靠对应的旧条目只写入漏迁报告，不会随意分配给相似图片。

> [!NOTE]
> “成功映射的历史记录数”和“迁移后的有效解锁数”不是同一个指标。能力、形态和 EX 版本现在分别计数；只有同一条目的旧别名、重复图片以及重复解锁记录会去重。组合图片还可能展开为多个条目。请检查 `用户迁移影响.csv` 的来源字段与映射列；使用历史源时，再核对 `历史数据合并.csv` 的 `active_unique_unlocks`、`active_baseline_unlocks_removed`、`active_post_migration_unlocks_preserved`、`history_unlocks_added` 和 `final_unique_unlocks`。

确认报告后，使用同一组路径执行正式迁移：

```bash
/root/AstrBot/.venv/bin/python migrate_catalog.py \
  --plugin-data /root/AstrBot/data/plugin_data/astrbot_plugin_kirby_catalog \
  --new-assets /Kirby \
  --supplemental-assets /Kirby_Forms \
  --report-dir /root/kirby_migration_report \
  --apply \
  --confirm REPLACE_OLD_KIRBY_DATA
```

如果 dry-run 使用了 `--history-source`，正式迁移必须传入完全相同的历史源：

```bash
/root/AstrBot/.venv/bin/python migrate_catalog.py \
  --plugin-data /root/AstrBot/data/plugin_data/astrbot_plugin_kirby_catalog \
  --history-source /root/AstrBot/data/plugin_data/astrbot_plugin_kirby_catalog.before-v3-20260804-120000 \
  --new-assets /Kirby \
  --supplemental-assets /Kirby_Forms \
  --report-dir /root/kirby_migration_report \
  --apply \
  --confirm REPLACE_OLD_KIRBY_DATA
```

正式迁移会先在同一文件系统完整构建并校验暂存目录，然后原子替换活动数据目录。它会保留：

- 当前盟友和抽取日期；
- 用户昵称、每日抽取次数、累计次数、连续未出新计数和其他未知用户字段；
- 每位用户的全部解锁记录及首次解锁日期；
- `draw_limits.json`、`draw_bonuses.json` 等非群配置；
- 百科缓存及其他不属于旧图鉴素材的插件数据。

> [!IMPORTANT]
> 活动目录中的 `img/allies` 只包含新素材。完整活动目录会自动重命名为同级的 `astrbot_plugin_kirby_catalog.before-v3-时间戳`，传入的历史源则保持原样。不要在确认迁移结果前删除任何备份。

迁移完成后在 AstrBot 管理面板重载插件，并实际检查 `查盟友`、`我的图鉴进度`、`我的盟友图鉴`、`星之卡比图鉴` 和 `盟友排行榜`。如需回滚，请先停用插件，再把当前活动目录移走，并把 `.before-v3-时间戳` 备份改回原目录名。

## 从上游 AW 增量导入

需要从 `astrbot_plugin_AnimeWife` 等兼容旧目录增量补拷数据时，由管理员明确执行下面的命令：

```text
星之卡比图鉴迁移
```

该群聊命令只用于旧目录增量导入，不会执行 v3 素材替换和编号重排。旧数据位于自定义路径时，可在插件配置中填写 `legacy_data_dir`。增量导入不会重复增加同一解锁记录，也不会删除来源目录。
