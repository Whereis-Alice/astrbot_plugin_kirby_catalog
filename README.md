# 星之卡比图鉴

[![AstrBot](https://img.shields.io/badge/AstrBot-%3E%3D4.16%2C%3C5-4c8bf5)](https://github.com/AstrBotDevs/AstrBot) [![Platform](https://img.shields.io/badge/platform-aiocqhttp-f59e0b)](https://github.com/AstrBotDevs/AstrBot) [![License](https://img.shields.io/badge/license-MIT-2ea44f)](LICENSE)

面向 AstrBot 群聊的星之卡比盟友抽取、收藏图鉴和百科查询插件。

插件以“年代编号 + 规范名称 + 本地素材”为核心管理盟友，适合数百张以上、仍会持续扩充的素材库。普通角色、卡比能力、特殊形态、EX 版本和其他经过核验的变体都可以作为独立盟友计入图鉴、个人进度与排行榜。插件同时支持每日抽取、猜盟友、引用改名或换图，以及 WiKirby 和 Kirby Wiki | Fandom 双百科查询。

项目仓库：[Whereis-Alice/astrbot_plugin_kirby_catalog](https://github.com/Whereis-Alice/astrbot_plugin_kirby_catalog)

## 目录

- [功能概览](#功能概览)
- [快速开始](#快速开始)
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
| 盟友抽取 | 每日限次抽取、冷却时间、连续未出新保底、纯文本 `今日盟友` 触发 |
| 收藏图鉴 | 按首次登场作品排序的固定编号、独立能力与形态、个人与群图鉴、有效进度和群内排行榜 |
| 素材管理 | 管理员可引用 Bot 消息改名、换图或手动添加素材；历史记录同步更新 |
| 互动玩法 | 猜盟友、引用图片直接作答、超时或猜错公布答案、随机盟友 |
| WiKirby | 页面简介、资料栏目、多语言名称、首图、LLM 翻译和百科卡片 |
| Kirby Fandom | 简介、信息框、分类、正文栏目、社区页面名称、相关语录、网页式招式表和首图 |
| 数据兼容 | v3 规范素材迁移、迁移报告、原子替换、自动备份，以及上游 AW 数据增量导入 |

> [!NOTE]
> 猜盟友和随机盟友只用于互动或查看，不会增加抽取次数，也不会把盟友写入个人图鉴。

## 快速开始

### 环境要求

- AstrBot `>=4.16,<5`
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
- `draw_limits.json` 等非群配置；
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
| `今日盟友` | 抽取今天的盟友，并在抽到新角色时写入个人图鉴 | `抽盟友`、`抽取盟友` |
| `查盟友 [成员]` | 查看自己或指定成员今天的盟友，支持成员编号、昵称和 `@` | `我的盟友` |
| `我的盟友图鉴` | 生成个人已解锁图鉴 | `盟友图鉴` |
| `我的图鉴进度` | 查看有效解锁数、总数、完成率、进度条和剩余数量 | `图鉴进度`、`我的盟友图鉴进度` |
| `星之卡比图鉴` | 生成本群完整图鉴，显示编号、名字和群内解锁状态 | `群盟友图鉴` |
| `随机盟友` | 随机查看一位盟友，不写入任何用户数据 | `随机查看盟友` |
| `猜盟友` | 发起猜名；可发送 `猜盟友 名字`，也可引用题目图片直接回答 | 无 |
| `盟友排行榜` | 查看本群有效解锁数量前十名 | `星之卡比排行榜`、`图鉴排行榜` |
| `盟友名单 [关键词]` | 按编号、名字或素材文件名检索盟友 | `星之卡比图鉴名单` |
| `星之卡比图鉴帮助` | 查看群内命令速查 | `盟友帮助` |

### 猜盟友规则

- 同一群同时只会存在一轮猜盟友；其他用户不能覆盖正在进行的题目。
- 可以回复 `猜盟友 名字`，也可以引用 Bot 发出的题目图片后直接发送名字。
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

也可以引用 Bot 发出的盟友消息执行 `卡比百科`，插件会尝试使用图鉴中的盟友名称查询。

### Kirby Fandom 命令

| 命令 | 作用 |
| --- | --- |
| `卡比F [页面名]` | 查询简介、信息框、分类、正文栏目、相关语录、招式表、首图和来源 |
| `卡比F章节 [页面名]` | 列出当前页面可查询的栏目 |
| `卡比F [页面名] \| [栏目名]` | 只读取指定栏目；父栏目会自动汇总其子栏目 |
| `卡比F名称 [页面名]` | 查询日文名和各语言 Fandom 社区页面标题 |

兼容别名：`卡比Fandom`、`卡比Fandom章节`、`卡比Fandom名称` 和 `卡比社区百科`。

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

百科卡片由 AstrBot T2I 服务渲染为一张完整 PNG。卡片使用 `1600px` 逻辑视口、设备像素截图和 `ultra` 清晰度，实测输出宽度约为 `2880px`。简介和首图位于顶部，资料与名称表横向排列，长正文使用双栏布局。WiKirby 与 Kirby Fandom 均不设置正文字符数或条目数上限，卡片会随完整内容自动增长。

Kirby Fandom 的特殊栏目会保留网页结构：`Related Quotes` 中每条语录独立显示正文、出处和作品；`Techniques` 按作品及 Type A / Type B 分组，以“招式、操作、说明、伤害”四列表格显示。网页里的手柄按钮图片会转换为可读操作文字，例如 `Pro 手柄：冲刺 + B`、`Joy-Con：下方向键`。所有语录、招式分组、操作说明和表格行都会完整保留。

内置模板：

| 模板 | 配色与版式 |
| --- | --- |
| `梦之泉` | 水蓝、薄荷绿、梦境紫与星光金；WiKirby 默认模板 |
| `卡比粉彩` | 卡比粉、天蓝、淡紫与奶油黄；Fandom 默认模板 |
| `瓦豆鲁迪` | 浅杏橙、奶油黄、草绿与青色；观察笔记式左侧首图 |
| `星际档案` | 银蓝、浅靛蓝、青色与淡金；克制的资料档案排版 |

卡片渲染失败时会自动回退到文字回复，不会丢失百科内容。

### LLM 工具

插件注册以下只读工具，AstrBot 的 LLM 可以主动调用：

| 工具名 | 能力 |
| --- | --- |
| `kirby_catalog_lookup_wikirby` | 查询 WiKirby 页面资料 |
| `kirby_catalog_lookup_official_names` | 查询 WiKirby 多语言名称 |
| `kirby_catalog_lookup_fandom` | 查询 Kirby Fandom 页面资料或指定章节 |
| `kirby_catalog_lookup_fandom_names` | 查询 Fandom 跨语言社区页面名称 |

这些工具不会发送群消息，也不会修改抽取或图鉴数据。

## 管理员命令

管理员权限由 AstrBot 的管理员配置控制。

| 命令 | 作用 |
| --- | --- |
| `星之卡比图鉴换图 [编号]` | 使用当前消息或引用消息中的图片替换素材 |
| `星之卡比图鉴添加 名字 [\| 来源]` | 添加新盟友并自动分配固定编号 |
| `星之卡比图鉴改名 编号 新名字 [\| 新来源]` | 修改名称或来源，并同步所有用户记录 |
| `星之卡比图鉴迁移` | 从上游 AW 或自定义兼容目录增量补拷；不执行 v3 全量重排 |
| `星之卡比图鉴清理旧名 旧前缀 新前缀 [保留名]` | 合并批量改名前遗留的旧名称 |
| `星之卡比图鉴删除重复 重复编号 正确编号 [...]` | 按编号将重复条目合并到正确条目 |

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
| 抽取与图鉴 | 每日抽取次数、冷却时间、猜盟友超时、图鉴列数 |
| WiKirby | 启用状态、首图、详细资料、回复形式、卡片模板、LLM 翻译、缓存和 Worker 中转 |
| Kirby Fandom | 启用状态、首图、详细栏目、回复形式、卡片模板、LLM 翻译和缓存 |
| 数据兼容 | 旧素材图床地址、上游 AW 或自定义兼容目录 `legacy_data_dir` |

### LLM 翻译

百科翻译默认关闭。开启后，插件使用 AstrBot 原生文本模型将英文简介和页面资料翻译为简体中文：

- 可以指定 Provider；
- Provider 留空时使用当前聊天会话的模型；
- 翻译失败会自动保留原文；
- Fandom 语录和招式使用结构化 JSON 翻译，LLM 可以翻译操作中的自然语言，但按键、方向、加号、平台对应关系和换行由插件校验；布局和伤害数字不交给 LLM 决定；
- LLM 工具调用本身不会再次触发嵌套翻译。

### WiKirby Cloudflare Worker

部分云服务器出口 IP 访问 WiKirby 时会收到 HTTP 403。插件会先尝试官方 API、备用域名、REST API 和静态页面回退。

如果所有官方入口都不可用，可以部署可选中转：

- Worker 代码：[`cloudflare_worker/wikirby_proxy.js`](cloudflare_worker/wikirby_proxy.js)
- 部署说明：[`cloudflare_worker/README.md`](cloudflare_worker/README.md)

Worker 必须设置 `WIKIRBY_PROXY_TOKEN`。请勿把真实密钥提交到公开仓库、Issue 或日志截图中。

Kirby Fandom 使用独立 API，不读取 WiKirby Worker 配置。

## 数据规则

- 只有 `今日盟友` 抽取会更新当前盟友、抽取次数和个人解锁图鉴。
- `随机盟友` 和 `猜盟友` 不写入用户图鉴。
- 改名会同步素材文件名和所有用户记录；固定编号保持不变。
- 换图不会删除用户解锁记录。
- 个人进度只统计当前仍存在的有效条目；旧名称别名不会重复计数。
- 不同能力、形态、EX 版本和阶段使用不同 `entry_key`，在个人进度与排行榜中分别计数。
- 群图鉴和排行榜按群分别保存，不会跨群合并。
- v3 规范图鉴按最早登场年份、同年作品顺序、主角优先、条目类型和页面标题生成编号；正式迁移后编号固定，日常改名不会改变编号。
- 插件不包含与收藏图鉴无关的夺取或 NTR 机制，也不会读取对应旧状态文件。

## 常见问题

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

QQ 或适配器可能拒绝过长的文字或合并转发消息。插件不会为适配平台限制而截断百科内容；这类情况下建议将百科回复形式切换为“仅百科卡片”。

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
3. [WiKirby](https://wikirby.com/wiki/Kirby_Wiki)：`卡比百科` 的资料来源和 MediaWiki API 服务。
4. [Kirby Wiki | Fandom](https://kirby.fandom.com/wiki/Kirby_Wiki)：`卡比F` 的资料来源和 MediaWiki API 服务。

感谢上游作者、百科编辑者及所有贡献者的工作。

- 本项目代码采用 [MIT License](LICENSE)。
- WiKirby 页面内容除另有说明外采用 GNU Free Documentation License 1.3 或更高版本。
- Kirby Wiki | Fandom 的站点 API 标注内容许可为 CC BY-SA。
- 百科页面图片、盟友素材和星之卡比相关角色版权归各自权利人所有。

插件只按查询结果返回摘要、资料、名称和原页面链接，不重新打包百科站点内容。
