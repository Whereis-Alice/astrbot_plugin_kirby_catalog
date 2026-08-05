# 星之卡比图鉴

[![AstrBot](https://img.shields.io/badge/AstrBot-%3E%3D4.16%2C%3C5-4c8bf5)](https://github.com/AstrBotDevs/AstrBot) [![Platform](https://img.shields.io/badge/platform-aiocqhttp-f59e0b)](https://github.com/AstrBotDevs/AstrBot) [![License](https://img.shields.io/badge/license-MIT-2ea44f)](LICENSE)

面向 AstrBot 群聊的星之卡比盟友抽取、收藏图鉴和百科查询插件。

插件以“年代编号 + 规范名称 + 本地素材”为核心管理盟友，适合数百张以上、仍会持续扩充的素材库。普通角色、卡比能力、特殊形态、EX 版本和其他经过核验的变体都可以作为独立盟友计入图鉴、个人进度与排行榜。1353 项规范图鉴均附有中英文名称、首次登场作品和简体中文简介；插件同时支持每日抽取、猜盟友、引用修改资料，以及 WiKirby 和 Kirby Wiki | Fandom 双百科查询。

项目仓库：[Whereis-Alice/astrbot_plugin_kirby_catalog](https://github.com/Whereis-Alice/astrbot_plugin_kirby_catalog)

## 目录

- [功能概览](#功能概览)
- [快速开始](#快速开始)
- [WebUI 图鉴管理台](#webui-图鉴管理台)
- [数据目录与迁移](#数据目录与迁移)
- [普通用户命令](#普通用户命令)
- [百科查询](#百科查询)
- [管理员命令](#管理员命令)
- [配置说明](#配置说明)
- [数据规则](#数据规则)
- [常见问题](#常见问题)
- [开发与反馈](#开发与反馈)
- [致谢与许可](#致谢与许可)

## 功能概览

| 模块 | 能力 |
| --- | --- |
| 盟友抽取 | 每日限次抽取、Bot 独立抽取、简体中文简介、可配置长度与回复形式、冷却时间、连续未出新保底、纯文本 `今日盟友` 触发 |
| 收藏图鉴 | 按首次登场作品排序的固定编号、独立能力与形态、个人与群图鉴、按需分页、生成缓存、有效进度和群内排行榜 |
| 素材管理 | 管理员可引用 Bot 消息改名、换图或编辑简介，也可手动添加素材；历史记录同步更新 |
| WebUI 管理台 | 在 AstrBot Dashboard 集中管理素材资料、全部群成员图鉴、今日次数、回收站和操作记录 |
| 互动玩法 | 中英文猜盟友、引用图片直接作答、超时或猜错公布答案、随机盟友 |
| WiKirby | 页面简介、资料栏目、多语言名称、首图、LLM 翻译和百科卡片 |
| Kirby Fandom | 简介、信息框、分类、正文栏目、社区页面名称、相关语录、网页式招式表和首图 |
| 发送稳定性 | 超长百科语义分页、图片尺寸预检、JPEG 标准化，以及可回退的 NapCat 本地文件直发 |
| 数据兼容 | v3 规范素材迁移、迁移报告、原子替换、自动备份，以及上游 AW 数据增量导入 |

> [!NOTE]
> 猜盟友和随机盟友只用于互动或查看，不会增加抽取次数，也不会把盟友写入个人图鉴。

## 快速开始

### 环境要求

- AstrBot `>=4.16,<5`
- 完整 WebUI 管理台需要 AstrBot `v4.26.8+`
- `aiocqhttp` 平台适配器
- Python 依赖：Pillow、Beautiful Soup 4

### 安装

可以在 AstrBot 插件管理中使用本仓库地址安装，也可以手动放入 AstrBot 的插件目录。

手动安装示例：

```bash
cd /path/to/AstrBot/data/plugins
git clone https://github.com/Whereis-Alice/astrbot_plugin_kirby_catalog.git
python -m pip install -r astrbot_plugin_kirby_catalog/requirements.txt
```

安装依赖时应使用 AstrBot 实际运行所用的 Python 环境。完成后在管理面板重载插件；通常不需要重启整个 AstrBot。

### 更新

推荐直接在 AstrBot WebUI 的插件管理页面更新。仅在手动维护插件目录时，才需要进入插件目录执行：

```bash
git pull
python -m pip install -r requirements.txt
```

更新完成后重载插件即可。普通代码更新不会清空图鉴数据；v3 规范素材迁移属于需要管理员主动执行的离线操作，插件不会静默重排现有图鉴。

## WebUI 图鉴管理台

从 `v3.4.0` 起，插件提供 AstrBot Dashboard 原生管理 Page。更新并重载插件后，在 Dashboard 的插件详情中打开“星之卡比图鉴管理台”即可使用，不需要另开端口或部署独立前端。

> [!IMPORTANT]
> 完整管理台依赖 AstrBot `v4.26.8+` 的插件 Page 和 Web API。插件仍声明兼容 `>=4.16,<5`，因为旧版 AstrBot 上抽取、图鉴和百科等群聊功能可以继续运行；旧版只是不显示这套管理页面。

### 管理范围

| 页面 | 可执行操作 |
| --- | --- |
| 概览 | 查看图鉴总数、缺图/缺简介、群组与成员规模、今日抽取、作品分布和最近操作 |
| 素材库 | 按编号、名称、英文名或作品搜索；按作品、类型和资料状态筛选；新增素材、换图、改名、修改首次登场作品和简介 |
| 群数据 | 查看全部群和成员；编辑昵称、当前盟友、当前日期、连续未解锁次数、今日已用次数和额外次数；增删个人解锁、重置群今日次数或删除成员记录 |
| 回收站 | 查看已删除条目，并恢复固定编号、素材、简介覆盖和仍可恢复的群成员引用 |
| 操作记录 | 查看 Dashboard 中的素材和群数据管理记录，包含操作者、时间、对象和变更摘要 |

名称或首次登场作品发生变化时，管理台会同步重命名素材文件，并更新所有群的当前盟友与历史解锁引用。换图只替换图片内容，不改变固定编号和用户解锁。新增条目使用新的固定编号；回收站中的旧编号不会被后续新增素材占用。

### 主题与响应式布局

右上角可以选择：

- 跟随 AstrBot：根据 Dashboard 当前明暗外观自动切换；
- 卡比配色：浅粉、青绿、金色和紫色共同构成的浅色管理主题；
- 暗色模式：适合长时间整理大量素材的高对比暗色主题。

桌面端使用高密度表格与右侧编辑抽屉，便于连续处理数百或上千项素材；手机端会改为纵向列表和全屏编辑面板。图标已随插件本地打包，不依赖 CDN。

### 权限与数据安全

- 管理 API 继承 AstrBot Dashboard 的登录鉴权，不提供匿名公开入口；不要在反向代理中绕过 Dashboard 身份验证单独暴露 `/api/plug/astrbot_plugin_kirby_catalog/`。
- WebUI 写操作与群内抽取共用同一把锁，避免管理员修改资料时恰好发生抽取而互相覆盖。
- 素材名称、首次登场作品和简介作为一次事务保存；成员资料与今日两类计数也会一起提交。任一磁盘写入失败时会自动恢复修改前状态。
- 删除素材只会移入回收站，不提供永久删除按钮。恢复会尽可能还原删除时受影响的当前盟友和解锁记录。
- 大批量整理前仍建议备份整个插件数据目录；WebUI 回收站不能代替服务器备份。

## 数据目录与迁移

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
├── preferences.json           # Dashboard 用户主题偏好
├── trash/                     # 已删除素材、资料和用户引用快照
└── uploads/                   # 尚未提交的临时上传，24 小时后自动清理
```

这些文件和 `catalog.json`、`config/*.json`、`img/allies/*` 共同组成完整图鉴数据。迁移、备份或服务器搬迁时应复制整个 `astrbot_plugin_kirby_catalog` 数据目录，而不是只复制图片。

### v3.1 规范素材与独立形态迁移

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

### 从上游 AW 增量导入

首次启动或执行下面的管理员命令时，插件仍可扫描 `astrbot_plugin_AnimeWife` 等兼容旧目录并补拷可识别数据：

```text
星之卡比图鉴迁移
```

该群聊命令只用于旧目录增量导入，不会执行 v3 素材替换和编号重排。旧数据位于自定义路径时，可在插件配置中填写 `legacy_data_dir`。增量导入不会重复增加同一解锁记录，也不会删除来源目录。

## 普通用户命令

大多数命令可以带 `/` 使用。`今日盟友` 额外支持直接发送纯文本，不要求斜杠或命令前缀。

| 命令 | 作用 | 常用别名 |
| --- | --- | --- |
| `今日盟友` | 抽取今天的盟友，显示简介，并在抽到新条目时写入个人图鉴 | `抽盟友`、`抽取盟友` |
| `查盟友 [成员]` | 查看自己或指定成员今天的盟友及简介，支持成员编号、昵称和 `@` | `我的盟友` |
| `我的盟友图鉴` | 生成个人已解锁图鉴 | `盟友图鉴` |
| `我的图鉴进度` | 查看有效解锁数、总数、完成率、进度条和剩余数量 | `图鉴进度`、`我的盟友图鉴进度` |
| `星之卡比图鉴` | 生成本群完整图鉴，显示编号、名字和群内解锁状态 | `群盟友图鉴` |
| `随机盟友` | 随机查看一位盟友及简介，不写入任何用户数据 | `随机查看盟友` |
| `Bot今日盟友` | 让 Bot 使用独立身份抽取当天盟友；重复调用只展示当天结果 | `机器人今日盟友`、`Bot抽盟友` |
| `查看简介 [编号或完整名称]` | 单独查看盟友简介；可引用 Bot 的盟友消息后直接回复 | `查看盟友简介` |
| `猜盟友` | 发起猜名；中文名、英文名均可，也可引用题目图片直接回答 | 无 |
| `盟友排行榜` | 查看本群有效解锁数量前十名 | `星之卡比排行榜`、`图鉴排行榜` |
| `盟友名单 [关键词]` | 按编号、名字或素材文件名检索盟友 | `星之卡比图鉴名单` |
| `星之卡比图鉴帮助` | 查看群内命令速查 | `盟友帮助` |

### 盟友简介

只有 `今日盟友`、`随机盟友` 和 `查盟友` 会自动附带简介。猜盟友、图鉴长图、名单和排行榜不会附带简介，避免无关消息变长。

默认编排如下，素材图片始终位于文字之后：

```text
爱丽丝的尼酱，你今天的盟友是 Papi，图鉴编号 #1202，首次登场于《Kirby: Meta Knight and the Knight of Yomi》。
今日剩余次数：2
简介：
（匹配到的简体中文简介）
详细信息引用本条消息并回复卡比百科即可查看（查百科会比较慢）
[盟友图片]
```

简介来自 WiKirby 页面开头的引语和导语，经简体中文翻译与术语规范化处理。角色、作品、能力等已核验专名采用“官方中文（官方英文）”；没有可靠官方中文时保留英文。每条内置资料保留来源页面、修订号和抓取时间，详见 [`resources/catalog_profiles.json`](resources/catalog_profiles.json)。

需要单独查看时，可以引用 Bot 发出的 `今日盟友`、`随机盟友` 或 `查盟友` 消息，然后直接回复：

```text
查看简介
```

也可以不引用消息，直接使用图鉴编号或完整名称：

```text
查看简介 1202
查看简介 Papi
```

`查看简介` 不受 `ally_description_enabled` 开关影响，即使关闭自动附带简介仍然可用。回复形式可配置为普通消息、合并转发或单张简介卡片；卡片只排版名称、编号、首次登场、简介和资料来源，不会重复发送盟友素材图。卡片渲染失败时自动回退到普通消息。

简介默认最多显示 600 个字符，超过限制的内容会以 `... ...` 结尾。该限制同时作用于自动附带简介和 `查看简介`；将 `ally_description_max_chars` 设为 `0` 可显示完整简介。

### Bot 今日盟友

`Bot今日盟友` 使用 `bot_<QQ号>` 形式的独立持久化身份；无法读取 Bot QQ 号时使用稳定的 `bot_astrbot` 身份。它不会占用命令发送者的次数，也不会写入任何群友的当前盟友或个人图鉴。

Bot 每个群每天只实际抽取一次。当天再次发送命令，或 LLM 重复调用工具，只会返回已经抽到的同一盟友，避免 Agent 重试造成连续抽取。Bot 解锁会计入本群汇总图鉴；默认不出现在个人排行榜，可通过 `bot_show_in_leaderboard` 开启。

### 猜盟友规则

- 同一群同时只会存在一轮猜盟友；其他用户不能覆盖正在进行的题目。
- 可以回复 `猜盟友 名字`，也可以引用 Bot 发出的题目图片后直接发送名字。
- 规范中文名或对应英文名都算正确；英文不区分大小写和多余空格。
- 答对、猜错或超时都会公布正确答案并结束本轮。
- 猜盟友不会修改当前盟友、抽取次数或个人图鉴。

## 百科查询

插件提供两个彼此独立的英文卡比百科来源。查询失败只会影响当前百科回复，不会影响抽取和图鉴数据。

### 来源区别

| 命令 | 来源 | 适合查询 |
| --- | --- | --- |
| `卡比百科` | [WiKirby](https://wikirby.com/wiki/Kirby_Wiki) | 结构化资料、简介、出现信息、趣闻和页面记录的多语言名称 |
| `卡比F` | [Kirby Wiki \| Fandom](https://kirby.fandom.com/wiki/Kirby_Wiki) | 作品经历、外观、性格、信息框、相关语录、招式与按章节读取的长篇正文 |

> [!CAUTION]
> `卡比F名称` 返回的是不同语言 Fandom 社区的页面标题，只能作为检索线索，不等同于任天堂官方译名。需要核对官方译名时，请优先使用 `卡比百科名称`，并以任天堂正式发布内容为最终依据。

### WiKirby 命令

| 命令 | 作用 |
| --- | --- |
| `卡比百科 [查询词]` | 查询页面简介、资料、多语言名称、首图和来源链接 |
| `卡比百科名称 [查询词]` | 只返回页面记录的多语言名称；别名：`卡比百科名`、`卡比百科译名` |

也可以引用 Bot 发出的盟友消息执行 `卡比百科` 或 `卡比百科名称`。插件会优先使用图鉴记录中的英文 `page_title`；没有编号时再从角色名的英文括号或素材名中提取查询词，不会把开头的作品名称当成角色。

### Kirby Fandom 命令

| 命令 | 作用 |
| --- | --- |
| `卡比F [页面名]` | 查询简介、信息框、分类、正文栏目、相关语录、招式表、首图和来源 |
| `卡比F章节 [页面名]` | 列出当前页面可查询的栏目 |
| `卡比F [页面名] \| [栏目名]` | 只读取指定栏目；父栏目会自动汇总其子栏目 |
| `卡比F名称 [页面名]` | 查询日文名和各语言 Fandom 社区页面标题 |

兼容别名：`卡比Fandom`、`卡比Fandom章节`、`卡比Fandom名称` 和 `卡比社区百科`。

`卡比F` 同样支持引用盟友消息查询，并与 WiKirby 命令共用“优先英文角色名、忽略作品前缀”的解析规则。

### 查询示例

```text
卡比百科 Driblee
卡比百科名称 Meta Knight

卡比F Spinni
卡比F章节 Spinni
卡比F Spinni | Games
卡比F Artist | Techniques
卡比F Blade Knight | Related Quotes
卡比F名称 Spinni
```

查询词对应多个页面时，插件会列出候选项，避免把角色、关卡、作品或续作页面混在一起。

### 回复形式和卡片

WiKirby 与 Kirby Fandom 都支持以下回复形式：

- 普通消息；
- 合并转发；
- 仅百科卡片；
- 百科文字 + 卡片；
- 文字 + 卡片合并转发。

百科卡片由 AstrBot T2I 服务渲染。默认使用 `1600px` 逻辑视口、`高清` 设备像素截图和质量 `92` 的 JPEG，在文字清晰度、文件大小和 QQ 上传稳定性之间取平衡；也可切换为 PNG、标准或超清。

普通页面仍只生成一张卡片。只有内容预计过长，或渲染后的宽高、总像素、文件大小超过安全阈值时，插件才会自动分页。分页按简介续篇、资料栏目、语录条目和招式表行拆分；每页重复页码与来源，招式续页保留表头，正文和表格行不会被截断。默认安全阈值为宽 `2160px`、高 `8000px`、`18MP` 和 `8MiB`，这些是针对 NapCat/QQNT 的保守兜底，不是 QQ 官方公布的硬限制。

使用合并转发时，插件会按段落把长文字拆成多个节点，图片或百科卡片放在独立节点。单个文字节点默认最多 3000 个字符，单条合并转发默认最多 20 个节点；超出后会继续发送下一条合并转发，正文不会被截断。这两个限制用于规避 NapCat/QQ 对超大转发载荷的不稳定处理，不是百科内容长度限制。

Kirby Fandom 的特殊栏目会保留网页结构：`Related Quotes` 中每条语录独立显示正文、出处和作品；`Techniques` 按作品及 Type A / Type B 分组，以“招式、操作、说明、伤害”四列表格显示。网页里的手柄按钮图片会转换为可读操作文字，例如 `Pro 手柄：冲刺 + B`、`Joy-Con：下方向键`。所有语录、招式分组、操作说明和表格行都会完整保留。

内置模板：

| 模板 | 配色与版式 |
| --- | --- |
| `梦之泉` | 水蓝、薄荷绿、梦境紫与星光金；WiKirby 默认模板 |
| `卡比粉彩` | 卡比粉、天蓝、淡紫与奶油黄；Fandom 默认模板 |
| `瓦豆鲁迪` | 浅杏橙、奶油黄、草绿与青色；观察笔记式左侧首图 |
| `星际档案` | 银蓝、浅靛蓝、青色与淡金；克制的资料档案排版 |

卡片渲染失败时会自动回退到文字回复，不会丢失百科内容。关闭 `wiki_card_auto_paginate` 可以恢复单张长图，但超长页面更容易触发 QQNT 的 `rich media transfer failed`。

### LLM 工具

插件注册以下工具，AstrBot 的 LLM 可以主动调用：

| 工具名 | 能力 |
| --- | --- |
| `kirby_catalog_lookup_wikirby` | 查询 WiKirby 页面资料 |
| `kirby_catalog_lookup_official_names` | 查询 WiKirby 多语言名称 |
| `kirby_catalog_lookup_fandom` | 查询 Kirby Fandom 页面资料或指定章节 |
| `kirby_catalog_lookup_fandom_names` | 查询 Fandom 跨语言社区页面名称 |
| `kirby_catalog_draw_bot_ally` | 使用 Bot 独立身份抽取当前群当天盟友；当天重复调用保持幂等 |

前四个百科工具只读，不会发送群消息或修改图鉴。`kirby_catalog_draw_bot_ally` 会写入 Bot 自己在当前群的当天盟友和解锁记录，但不会占用提问者的次数或修改群友数据；工具只返回文字，群内需要直接查看素材图时使用 `Bot今日盟友`。

## 管理员命令

管理员权限由 AstrBot 的管理员配置控制。

| 命令 | 作用 |
| --- | --- |
| `星之卡比图鉴换图 [编号]` | 使用当前消息或引用消息中的图片替换素材 |
| `星之卡比图鉴添加 名字 [\| 来源]` | 添加新盟友并自动分配固定编号 |
| `星之卡比图鉴改名 编号 新名字 [\| 新来源]` | 修改名称或来源，并同步所有用户记录 |
| `星之卡比图鉴简介 编号 [新简介]` | 查看简介；带新内容时新增或覆盖人工简介 |
| `星之卡比图鉴恢复简介 编号` | 删除人工覆盖并恢复随插件发布的内置简介 |
| `重置今日群抽取次数` | 清空当前群当天的已用次数和额外次数；不影响其他群或历史日期 |
| `增加今日抽取次数 @群友 [次数]` | 为指定群友增加当天可用的抽取机会；默认增加 1 次，也支持用户 ID |
| `星之卡比图鉴迁移` | 从上游 AW 或自定义兼容目录增量补拷；不执行 v3 全量重排 |
| `星之卡比图鉴清理旧名 旧前缀 新前缀 [保留名]` | 合并批量改名前遗留的旧名称 |
| `星之卡比图鉴删除重复 重复编号 正确编号 [...]` | 按编号将重复条目合并到正确条目 |

### 管理今日抽取次数

重置当前群所有成员当天的计数：

```text
重置今日群抽取次数
```

给一位群友增加当天可用机会，省略次数时默认增加 1 次：

```text
增加今日抽取次数 @群友
增加今日抽取次数 @群友 3
增加今日抽取次数 2127074778 3
```

额外机会按“群号、用户、日期”分别保存在 `config/draw_bonuses.json`。增加机会不会伪造或减少已经使用的次数；执行重置时会同时清除当前群当天的 `draw_limits.json` 已用记录和 `draw_bonuses.json` 额外记录。

### 引用消息换图

引用 Bot 发出的盟友图片消息：

```text
星之卡比图鉴换图
```

插件会从引用消息中的 `#编号` 或 `编号：` 自动识别目标。也可以明确填写编号：

```text
星之卡比图鉴换图 12
```

换图只替换素材文件。已经发送出去的旧聊天消息不会变化，但后续抽取、查询和图鉴会使用新图片。

### 引用消息改名

按编号修改：

```text
星之卡比图鉴改名 12 结晶化天鹅罗利那
星之卡比图鉴改名 12 结晶化天鹅罗利那 | 星之卡比 探索发现
```

也可以引用 Bot 发出的盟友消息后省略编号：

```text
星之卡比图鉴改名 结晶化天鹅罗利那
```

改名会同步：

- 图鉴目录中的名称和别名；
- 素材文件名；
- 所有群的当前盟友记录；
- 所有用户的历史解锁记录；
- 后续生成的群图鉴和个人图鉴。

素材文件名中的作品前缀不会被改动。例如：

```text
星之卡比 探索发现.旧名字.png
```

会变为：

```text
星之卡比 探索发现.新名字.png
```

固定图鉴编号保持不变。

### 查看和修改简介

只填写编号时查看当前生效的简介及来源：

```text
星之卡比图鉴简介 1202
```

在编号后填写内容即可新增或修改人工简介，支持多行文本：

```text
星之卡比图鉴简介 1202 Papi 是《Kirby: Meta Knight and the Knight of Yomi》中登场的角色。
```

也可以引用 Bot 发出的 `今日盟友`、`随机盟友` 或 `查盟友` 消息，省略编号：

```text
星之卡比图鉴简介 新的简介内容
```

需要撤销人工修改时，按编号执行或引用盟友消息执行：

```text
星之卡比图鉴恢复简介 1202
```

修改立即生效，不需要重载插件。人工简介不会改写内置资料文件，也不会影响用户的解锁记录。

### 素材文件命名

需要让插件自动识别作品来源时，推荐使用：

```text
最早登场作品.盟友名称.png
```

插件只把文件名中的第一个半角点号 `.` 作为“作品来源 / 盟友名称”的分隔符，角色名中的后续点号会完整保留。例如：

```text
Kirby's Dream Land.瓦豆鲁迪（Waddle Dee）.png
Kirby Air Riders.J.J.png
```

以上文件会分别解析出作品 `Kirby's Dream Land`、`Kirby Air Riders`，以及角色名 `瓦豆鲁迪（Waddle Dee）`、`J.J`。

Windows 文件名不能包含半角 `:`、`?` 等字符。作品标题含这些标点时可改用全角形式，例如：

```text
Kirby： Squeak Squad.怪侠洛切团（Squeaks）.jpg
```

没有可靠作品来源的素材可以继续只使用 `盟友名称.png`，插件不会强制要求作品前缀。

### 添加新盟友

在当前命令中附带图片，或引用一张图片：

```text
星之卡比图鉴添加 新盟友
星之卡比图鉴添加 新盟友 | 作品来源
```

添加后盟友会进入抽取池和猜名池。添加操作本身不会直接解锁给任何用户。

### 清理旧名称和重复条目

批量前缀改名后，可合并旧名称并保留例外：

```text
星之卡比图鉴清理旧名 水晶 结晶化 水晶针卡比
```

当重复条目已经使用不同名称时，请明确指定“重复编号 正确编号”：

```text
星之卡比图鉴删除重复 421 406 413 134
```

上例会把 `#421` 合并到 `#406`，把 `#413` 合并到 `#134`。合并时会同步用户当前记录和历史解锁记录。

> [!WARNING]
> 不建议直接在文件管理器中批量改名或删除素材。请优先使用管理员命令，确保素材文件、固定编号和用户历史记录保持一致。

## 配置说明

所有配置均可在 AstrBot 插件配置页面修改，不需要直接编辑源码。

| 配置组 | 常用选项 |
| --- | --- |
| 抽取与图鉴 | 每日抽取次数、今日/随机/查询文案、Bot 独立抽取、简介长度与回复形式、百科提示开关、详情编排、冷却时间、猜盟友超时、图鉴列数 |
| 图片发送与长卡片 | NapCat 本地文件直发、共享目录、失败重试、JPEG 标准化、群图鉴高度、百科分页与图片安全阈值 |
| WiKirby | 启用状态、首图、详细资料、回复形式、卡片模板、LLM 翻译、缓存和 Worker 中转 |
| Kirby Fandom | 启用状态、首图、详细栏目、回复形式、卡片模板、LLM 翻译和缓存 |
| 数据兼容 | 旧素材图床地址、上游 AW 或自定义兼容目录 `legacy_data_dir` |

### 盟友消息编排

| 配置项 | 说明 |
| --- | --- |
| `draw_message_template` | `今日盟友` 的基础文案 |
| `random_message_template` | `随机盟友` 的基础文案 |
| `query_message_template` | `查盟友` 的基础文案 |
| `ally_description_enabled` | 是否显示简介，默认开启 |
| `ally_description_max_chars` | 简介最大字符数，默认 600；`0` 表示不截断 |
| `ally_description_view_mode` | `查看简介` 使用普通消息、合并转发或简介卡片 |
| `ally_description_card_template` | 简介卡片模板：梦之泉、卡比粉彩、瓦豆鲁迪或星际档案 |
| `ally_wiki_hint_enabled` | 是否显示引用查询百科的提示，默认开启 |
| `ally_wiki_hint_text` | 自定义百科提示内容 |
| `ally_detail_template` | 编排基础文案、简介和百科提示 |
| `bot_draw_enabled` | 启用 `Bot今日盟友` 和 Bot 抽取 LLM 工具 |
| `bot_draw_nickname` | Bot 独立身份的显示名称 |
| `bot_draw_message_template` | Bot 今日盟友基础文案 |
| `bot_show_in_leaderboard` | 是否让 Bot 出现在群排行榜，默认关闭 |

三个基础文案都支持 `{name}`、`{id}`、`{source}` 和 `{source_text}`。今日抽取另支持 `{nickname}`、`{flags}`、`{remaining}`；查询文案另支持 `{nickname}`、`{unlock_date}`、`{unlock_text}`。

`ally_detail_template` 支持 `{base}`、`{description}`、`{description_block}`、`{wiki_hint}` 和 `{wiki_hint_block}`。`description_block` 会自动生成换行、`简介：` 标题和正文；`wiki_hint_block` 会自动在提示前换行。模板字段错误时插件会记录警告并回退到默认编排。

### 图片发送与长图保护

AstrBot v4.26.8 的 aiocqhttp 适配器会把 `Image` 组件统一转换为 base64 后再交给 NapCat。base64 会增加约三分之一的传输体积和额外的编码内存；NapCat WebSocket 本身还有 `50 MiB` 的 `maxPayload`。即使文件体积很小，极端宽高或总像素过大的图片仍可能被 QQNT 拒绝并返回 `rich media transfer failed`。NapCat 对超大图片问题的处理建议也是由调用方自行兜底：[NapCatQQ #1443](https://github.com/NapNeko/NapCatQQ/issues/1443)。

插件默认使用“自动（推荐）”发送方式：

1. 在 aiocqhttp/NapCat 上优先把本地图片路径直接作为 OneBot `file://` 发送，避免 base64；
2. 失败后按配置重试；
3. 仍失败则回退 AstrBot 标准发送，不会因为直发不可用而吞掉消息；
4. 非 aiocqhttp 平台直接使用标准发送。

常用配置：

| 配置项 | 默认值 | 说明 |
| --- | ---: | --- |
| `media_send_mode` | `自动（推荐）` | 自动、AstrBot 标准发送或强制 NapCat 本地文件直发 |
| `media_shared_directory` | 留空 | AstrBot 可写、NapCat 可读的共享挂载目录 |
| `media_napcat_directory` | 留空 | 同一共享卷在 NapCat 容器内使用不同路径时填写 |
| `media_direct_retry_count` | `1` | 本地文件直发失败后的重试次数 |
| `media_normalize_jpeg` | 开启 | 将 JPEG 统一为 RGB/JFIF，排除非标准编码问题 |
| `media_max_width_px` / `media_max_height_px` | `2160` / `8000` | 所有本地发送图片的最终尺寸保护 |
| `media_max_megapixels` / `media_max_bytes_mb` | `18` / `8` | 所有本地发送图片的总像素与文件大小保护 |
| `gallery_max_height_px` | `7600` | 群或个人图鉴超过此高度才分页；`0` 表示关闭 |
| `wiki_card_auto_paginate` | 开启 | 只分页真正过长的百科卡片 |
| `wiki_card_page_line_budget` | `110` | 每页内容预算，可配置范围 `60-3000`；复杂页面可从 `500` 或 `600` 开始测试 |
| `wiki_card_resolution` | `高清（推荐）` | 标准、高清或超清 |
| `wiki_card_image_format` | `JPEG` | JPEG 体积更小；PNG 保留无损文字边缘 |

AstrBot 与 NapCat 运行在同一系统、NapCat 能读取 AstrBot 文件路径时，共享目录可以留空。容器部署时应给两边挂载同一个目录：若容器内路径相同，只填写 `media_shared_directory`；若路径不同，再填写 NapCat 侧的 `media_napcat_directory`。插件只会在共享目录下使用自己的 `astrbot_plugin_kirby_catalog` 子目录，并定期清理过期暂存图片；同一源文件未变化时会复用暂存副本，重复查询不再反复复制或转码。

群总图鉴会根据 `gallery_max_height_px` 自动分成少量图片；状态、素材和版式未变化时会直接复用上次生成结果。个人图鉴较短时仍只发送一张。百科分页和图鉴分页都不删除内容，只改变图片分组。若今日盟友等原始素材自身仍超过通用阈值，插件只创建等比缩放的发送缓存副本，原素材文件、图鉴编号和用户数据都不会被修改。百科卡片宽度超过安全值时会自动降低一级渲染清晰度后重试。

`wiki_card_page_line_budget` 从 v3.5.2 起不再限制为最高 `300`，现在最高可设为 `3000`。它是分页目标，不是绕过图片安全检查的开关：如果单页渲染后仍超过 `wiki_card_max_height_px`、`wiki_card_max_megapixels` 或 `wiki_card_max_bytes_mb`，插件会自动降低预算重新分页。大幅提高这些图片阈值可能重新触发 NapCat/QQNT 上传失败。

### LLM 翻译

百科翻译默认关闭。开启后，插件使用 AstrBot 原生文本模型将英文简介和页面资料翻译为简体中文：

- 可以指定 Provider；
- Provider 留空时使用当前聊天会话的模型；
- 翻译失败会自动保留原文；
- 相同来源、相同 Provider 和相同原文的译文会在插件进程内缓存，缓存时间复用对应百科的 TTL 配置；重载插件后缓存会清空；
- Fandom 语录和招式使用结构化 JSON 翻译，LLM 可以翻译操作中的自然语言，但按键、方向、加号、平台对应关系和换行由插件校验；布局和伤害数字不交给 LLM 决定；
- LLM 工具调用本身不会再次触发嵌套翻译。

### 合并转发稳定性

| 配置项 | 默认值 | 说明 |
| --- | ---: | --- |
| `forward_node_max_chars` | `3000` | 单个合并转发文字节点的最大字符数，按段落优先拆分 |
| `forward_max_nodes_per_message` | `20` | 单条合并转发的最大节点数，超过后自动分成下一条合并转发 |
| `forward_max_images_per_message` | `2` | 单条合并转发最多包含的图片数；超长百科的文字与图片会分开打包 |
| `forward_direct_send_enabled` | `true` | aiocqhttp/NapCat 上由插件直接发送，以便捕获异常并执行兜底 |
| `forward_retry_count` | `1` | 已无法继续拆分的单节点转发失败后重试次数 |
| `forward_retry_delay_seconds` | `0.5` | 单节点转发重试间隔 |
| `forward_batch_delay_seconds` | `0.2` | 多条转发之间的发送间隔；设为 `0` 更快，但连续上传稳定性可能降低 |

这些设置由 WiKirby、Kirby Fandom 和 `查看简介` 的合并转发共用。普通短正文和一张图片仍会保持为一条转发；只有长正文、多卡片或节点超限时才拆分。通常应保持默认值：把字符数调得过大会重新形成超大节点，调得过小则会制造过多节点，同样可能触发 QQ 或 NapCat 的限制。

### WiKirby Cloudflare Worker

部分云服务器出口 IP 访问 WiKirby 时会收到 HTTP 403。插件会先尝试官方 API、备用域名、REST API 和静态页面回退。

如果所有官方入口都不可用，可以部署可选中转：

- Worker 代码：[`cloudflare_worker/wikirby_proxy.js`](cloudflare_worker/wikirby_proxy.js)
- 部署说明：[`cloudflare_worker/README.md`](cloudflare_worker/README.md)

Worker 必须设置 `WIKIRBY_PROXY_TOKEN`。请勿把真实密钥提交到公开仓库、Issue 或日志截图中。

Kirby Fandom 使用独立 API，不读取 WiKirby Worker 配置。

## 数据规则

- 对群友而言，只有 `今日盟友` 会更新自己的当前盟友、抽取次数和个人解锁图鉴；`Bot今日盟友` 只更新 Bot 的独立记录。
- `今日盟友`、`随机盟友` 和 `查盟友` 默认显示内置或人工简介；简介与百科提示均可单独关闭。关闭自动简介后仍可引用消息发送 `查看简介`。
- 默认抽取文案显示盟友名、图鉴编号、首次登场作品和剩余次数；三个显示简介的命令均可在 WebUI 自定义基础文案和详情编排。无效模板会自动回退到默认文案。
- `随机盟友` 和 `猜盟友` 不写入用户图鉴。
- Bot 每群每天只实际抽取一次，重复命令或 LLM 工具重试只返回当天结果；默认不显示在排行榜。
- 改名会同步素材文件名和所有用户记录；固定编号保持不变。
- 换图不会删除用户解锁记录。
- 人工简介按稳定 `entry_key` 保存；改名不会丢失，恢复简介只删除人工覆盖。
- 个人进度只统计当前仍存在的有效条目；旧名称别名不会重复计数。
- 不同能力、形态、EX 版本和阶段使用不同 `entry_key`，在个人进度与排行榜中分别计数。
- 群图鉴和排行榜按群分别保存，不会跨群合并。
- v3 规范图鉴按最早登场年份、同年作品顺序、主角优先、条目类型和页面标题生成编号；正式迁移后编号固定，日常改名不会改变编号。
- 插件不包含与收藏图鉴无关的夺取或 NTR 机制，也不会读取对应旧状态文件。

## 常见问题

### 为什么更新后看不到图鉴管理台？

先确认 AstrBot 已升级到 `v4.26.8+`，然后在插件管理中更新并重载“星之卡比图鉴”。如果日志出现 `WebUI 注册失败`，群聊功能仍会运行，但需要根据同一条日志修复 AstrBot Page/Web API 环境。管理台只会出现在已登录的 Dashboard 插件页面，不存在可直接访问的独立公网地址。

### 更新后需要重启 AstrBot 吗？

通常使用 AstrBot 的“重载插件”即可。只有依赖环境未刷新、适配器状态异常或重载失败时，才需要重启 AstrBot。

### 为什么换图后旧消息里的图片没有变化？

聊天平台已经发送的历史消息无法被插件修改。换图会影响之后的抽取、查询和图鉴生成。

### 为什么图鉴中出现灰色问号？

这表示用户历史记录仍在，但对应素材暂时找不到。请补回同名素材，或使用管理员换图、迁移和重复条目合并命令修复。

### 为什么迁移后的有效解锁数仍可能和旧记录数不同？

能力、形态和 EX 版本会分别计数，但旧别名、同一条目的重复图片和重复解锁记录仍只算一次；一张包含多个角色或职业的旧图片也可能展开成多个解锁。迁移工具会保留每个可识别记录的去向和最早解锁日期，具体变化可查看 `用户迁移影响.csv` 与 `漏迁用户记录.csv`。

### 为什么无法读取引用消息中的图片？

不同平台适配器对引用消息的支持程度不同。可以把图片直接附在当前命令中；如果当前消息和引用消息都无法提供图片字节，插件无法执行换图或添加。

### 为什么合并转发发送失败？

如果日志同时出现 `send_group_forward_msg`、`UploadForwardMsgV2`、`retcode=1200` 和 `Cannot read properties of undefined (reading 'resId')`，失败发生在 NapCat/QQ 上传合并转发的阶段，不是 WiKirby 抓取或 LLM 翻译阶段。NapCat 项目中已有相同错误的报告：[NapCatQQ #885](https://github.com/NapNeko/NapCatQQ/issues/885)；另一个转发超时案例在更新 QQNT 后恢复：[NapCatQQ #1147](https://github.com/NapNeko/NapCatQQ/issues/1147)。

插件从 v3.5.1 起会直接捕获 aiocqhttp/NapCat 的转发异常：长文字与卡片图片分开发送，图片按独立上限分批；失败批次会继续缩小，单节点仍失败时自动改发普通消息或普通图片。这样即使 NapCat 无法生成合并转发 `resId`，也不会再由 AstrBot 响应阶段原样抛出整条失败消息。

如果仍然失败，请依次检查：

1. 将 NapCat 和 QQNT 更新到彼此兼容的当前版本；
2. 保持 `forward_direct_send_enabled=true`、`forward_node_max_chars=3000`、`forward_max_nodes_per_message=20` 和 `forward_max_images_per_message=2` 先测试；
3. 若图片转发仍不稳定，将 `forward_max_images_per_message` 调为 `1`；
4. 将百科回复形式改为“仅百科卡片”，绕开合并转发；
5. 若普通短合并转发和普通图片也失败，检查 NapCat 网络、QQ 风控和账号发送状态。

百科全文不会因为这些兼容处理而被截断；极长页面可能拆成多条合并转发。

### 为什么普通卡片或图鉴图片提示 rich media transfer failed？

这个错误发生在 NapCat 调用 QQNT 上传图片的阶段。它不等于百科抓取失败，也不能只看文件大小判断：分辨率、长宽、总像素、JPEG 编码、网络和账号风控都可能影响结果。

建议按下面顺序排查：

1. 保持 `wiki_card_auto_paginate=true`、`wiki_card_resolution=高清（推荐）`、`wiki_card_image_format=JPEG`；
2. 保持默认安全阈值和 `gallery_max_height_px=7600`，先确认超长卡片与群总图鉴能够分页；
3. 使用 `media_send_mode=自动（推荐）`；同机部署可直接测试，容器部署需正确配置共享目录；
4. 日志出现“NapCat 本地文件直发成功”表示已绕过 base64；出现“回退 AstrBot 标准发送”则检查共享路径在 NapCat 侧是否真实可读；
5. 如果尺寸正常的短图在两种发送方式下都失败，继续检查 NapCat、QQNT 版本、网络和账号风控。

本地文件直发只减少 AstrBot 到 NapCat 的编码和传输成本，不能绕过 QQNT 最终的图片限制。因此插件仍会先做长卡片与图鉴分页，再选择发送方式。

### 为什么百科查询有时比较慢？

完整查询可能依次包含百科请求、LLM 翻译、首图下载和 HTML 卡片渲染。v3.3.2 会记录类似下面的日志：

```text
[astrbot_plugin_kirby_catalog] WiKirby 查询内容已生成: query='Kirby', mode=forward, chars=34864, forward_nodes=13, elapsed=12.34s
```

如果这条“查询内容已生成”日志本身很晚才出现，耗时在抓取、翻译或卡片渲染；同一页面的重复翻译会使用进程内缓存。如果该日志很快出现，但随后长时间没有消息，或 `respond.stage` 才报告错误，耗时在 AstrBot 到 NapCat/QQ 的发送阶段。只追求稳定和较少消息时优先使用“仅百科卡片”；只追求首次响应速度时，关闭 LLM 翻译和卡片渲染会更快。

### 为什么 WiKirby 查询返回 403？

这通常是 WiKirby 或 Cloudflare 对云服务器出口 IP 的限制。先查看日志确认所有官方回退是否都失败，再按 [Worker 部署说明](cloudflare_worker/README.md) 配置中转。

### Fandom 名称可以当作官方中文名吗？

不可以。Fandom 名称来自社区页面标题。请使用 `卡比百科名称` 辅助核对，并以任天堂正式发布内容为准。

## 开发与反馈

运行测试：

```bash
python -m unittest discover -s tests -t .. -q
```

发现问题时，请在 [GitHub Issues](https://github.com/Whereis-Alice/astrbot_plugin_kirby_catalog/issues) 提交，并尽量提供：

- AstrBot 版本和平台适配器；
- 插件版本；
- 触发问题的完整命令；
- 相关日志和错误码；
- 是否启用了百科翻译、卡片、合并转发或 Worker。

公开日志前请删除 Token、Cookie、群号、用户隐私和服务器敏感路径。

## 致谢与许可

本项目在数据格式兼容、抽取流程和基础图鉴思路上参考并改造自：

1. [zgojin/astrbot_plugin_AW](https://github.com/zgojin/astrbot_plugin_AW)：原始 AstrBot 群老婆插件，上游代码与数据格式参考来源。
2. [Rinco304/AnimeWife](https://github.com/Rinco304/AnimeWife)：早期功能灵感来源。
3. [WiKirby](https://wikirby.com/wiki/Kirby_Wiki)：`卡比百科` 及内置图鉴简介的资料来源和 MediaWiki API 服务。
4. [Kirby Wiki | Fandom](https://kirby.fandom.com/wiki/Kirby_Wiki)：`卡比F` 的资料来源和 MediaWiki API 服务。
5. [Lucide](https://lucide.dev/)：图鉴管理台使用的本地图标库。

感谢上游作者、百科编辑者及所有贡献者的工作。

- 本项目代码采用 [MIT License](LICENSE)。
- 内置简介是 WiKirby 页面引语和导语的简体中文翻译及术语规范化派生内容，按 GNU Free Documentation License 1.3 或更高版本提供。每条记录保留来源页面与修订号，完整说明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
- Kirby Wiki | Fandom 的站点 API 标注内容许可为 CC BY-SA。
- 管理台内置 Lucide `v1.28.0`，按 ISC License 提供；其中源自 Feather 的图标保留 MIT License，完整声明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
- 百科页面图片、盟友素材和星之卡比相关角色版权归各自权利人所有。

在线百科查询会返回摘要、资料、名称和原页面链接；内置简介库仅打包图鉴所需的页面开头短介绍，不包含完整百科正文。
