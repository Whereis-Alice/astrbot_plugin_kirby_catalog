<p align="center">
  <img src="./logo.svg" alt="星之卡比图鉴" width="96">
</p>

# 星之卡比图鉴

[![AstrBot](https://img.shields.io/badge/AstrBot-%3E%3D4.22.2%2C%3C5-4c8bf5)](https://github.com/AstrBotDevs/AstrBot) [![Platform](https://img.shields.io/badge/platform-aiocqhttp-f59e0b)](https://github.com/AstrBotDevs/AstrBot) [![License](https://img.shields.io/badge/license-MIT-2ea44f)](LICENSE)

面向 AstrBot 群聊的星之卡比盟友抽取、收藏图鉴和百科查询插件。

插件以“年代编号 + 规范名称 + 本地素材”为核心管理盟友，适合数百张以上、仍会持续扩充的素材库。普通角色、卡比能力、特殊形态、EX 版本和其他经过核验的变体都可以作为独立盟友计入图鉴、个人进度与排行榜。1353 项规范图鉴均附有中英文名称、首次登场作品和简体中文简介；插件同时支持每日抽取、猜盟友、引用修改资料，以及 WiKirby、Kirby Wiki | Fandom 与真格攻略 Wiki 三种查询来源。真格 Wiki 还提供覆盖全部 301 页的中日文名称速查图和连续目录编号。

项目仓库：[Whereis-Alice/astrbot_plugin_kirby_catalog](https://github.com/Whereis-Alice/astrbot_plugin_kirby_catalog)

## 功能概览

| 模块 | 能力 |
| --- | --- |
| 盟友抽取 | 每日限次抽取、Bot 独立抽取、简体中文简介、可配置长度与回复形式、冷却时间、连续未出新保底、纯文本 `今日盟友` 触发 |
| 收藏图鉴 | 按首次登场作品排序的固定编号、独立能力与形态、个人与群图鉴、按需分页、生成缓存、有效进度和群内排行榜 |
| 素材管理 | 管理员可引用 Bot 消息改名、换图或编辑简介，也可手动添加素材；历史记录同步更新 |
| WebUI 管理台 | 在 AstrBot Dashboard 集中管理素材资料、名称库、全部群成员图鉴、今日次数、回收站和操作记录；一屏满铺仪表盘、四款皮肤、内联图标、零外部请求 |
| 备份与迁移 | 图鉴、名称库、百科序号、群组进度和操作记录导出为 JSON/CSV；配置整包与素材图片按分卷导出为 ZIP；导入先预检、确认后才写入 |
| 互动玩法 | 中英文猜盟友、引用图片直接作答、超时或猜错公布答案、随机盟友 |
| WiKirby | 页面简介、资料栏目、多语言名称、首图、LLM 翻译，以及文本、卡片和 HTML 文档三种查询形式 |
| Kirby Fandom | 简介、信息框、分类、正文栏目、社区页面名称、相关语录、网页式招式表、首图和完整 HTML 文档 |
| 真格攻略 Wiki | 全部 301 页中英日名称索引、真 Boss Battle 的能力与首领攻略、隐藏折叠资料、完整数据表、实机记录；支持可选 LLM 翻译 |
| 发送稳定性 | 超长百科语义分页、图片尺寸预检、JPEG 标准化，以及可回退的协议端本地文件直发 |
| 数据兼容 | 规范素材迁移、迁移报告、原子替换、自动备份，以及上游 AW 数据增量导入 |

> [!NOTE]
> 猜盟友和随机盟友只用于互动或查看，不会增加抽取次数，也不会把盟友写入个人图鉴。

## 快速开始

### 环境要求

- AstrBot `>=4.22.2,<5`
- 完整 WebUI 管理台需要 AstrBot `v4.26.8+`
- `aiocqhttp` 平台适配器，以及一个 OneBot v11 协议端（NapCat、LLBot 或 SnowLuma，见 [协议端兼容性](docs/protocol-clients.md)）
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

更新完成后重载插件即可。普通代码更新不会清空图鉴数据；规范素材迁移属于需要管理员主动执行的离线操作，插件不会静默重排现有图鉴。

> [!WARNING]
> AstrBot `v4.27.2`、`v4.27.3` 的 WebUI 更新流程会连续重载同一插件两次。低内存服务器建议先停用插件再更新，完成后重新启用。排查步骤见 [常见问题](docs/faq.md)。

## 常用命令

| 命令 | 作用 |
| --- | --- |
| `今日盟友` | 抽取今天的盟友，附简介与素材图，抽到新条目时写入个人图鉴 |
| `查盟友 <名称或#编号>` | 精准查询一条盟友资料，不抽取、不解锁、不计次数 |
| `我的盟友图鉴` | 生成个人已解锁图鉴 |
| `我的图鉴进度` | 查看有效解锁数、完成率和剩余数量 |
| `星之卡比图鉴` | 生成本群完整图鉴与解锁状态 |
| `猜盟友` | 发起猜名，中英文名均可，也能引用题目图片作答 |
| `Bot今日盟友` | 让 Bot 以独立身份抽取当天盟友 |
| `卡比百科 [查询词]` | 查询 WiKirby 的简介、资料与多语言名称 |
| `卡比F [页面名]` | 查询 Kirby Wiki \| Fandom 的正文、信息框与招式 |
| `卡比真格 [页面名]` | 查询真格攻略 Wiki 的 Boss 攻略与实机记录 |
| `星之卡比图鉴帮助` | 查看群内命令速查 |

大多数命令可以带 `/` 使用，`今日盟友` 还支持直接发送纯文本。别名、参数细节、管理员命令见 [命令手册](docs/commands.md)，三个百科来源的完整用法见 [百科查询](docs/wiki-lookup.md)。

## 文档

| 文档 | 内容 |
| --- | --- |
| [命令手册](docs/commands.md) | 普通用户与管理员的全部命令、别名与用法 |
| [百科查询](docs/wiki-lookup.md) | 三个资料源命令、回复形式、卡片与 LLM 工具 |
| [WebUI 管理台](docs/webui.md) | 八个页面的管理范围、数据备份、皮肤与前端结构 |
| [配置与数据规则](docs/configuration.md) | 全部配置分组、字段含义与数据规则 |
| [协议端兼容性](docs/protocol-clients.md) | NapCat、LLBot、SnowLuma 的反向 WS 接入与跨机部署 |
| [数据目录与迁移](docs/data-migration.md) | 数据目录结构、规范素材迁移、上游 AW 增量导入 |
| [常见问题](docs/faq.md) | 发送失败、百科 403、迁移计数等排查 |
| [更新日志](CHANGELOG.md) | 全部版本变更 |
| [第三方声明](THIRD_PARTY_NOTICES.md) | 数据与图标来源许可 |

## 开发与反馈

运行测试：

```bash
python -m unittest discover -s tests -t .. -q
```

审计真格 Wiki 三语名称表：

```bash
python tools/audit_shinkaku_page_names.py
```

维护时还可以用 `--source-snapshot 页面列表快照.json` 与抓取快照逐 URL 比对；任何漏页、额外页面、标题错配、重复完整名称或断裂序号都会返回非零退出码。

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
5. [星のカービィ 真 ボスバトル攻略Wiki](https://seesaawiki.jp/kirby_shinkaku/)：`卡比真格` 的日文攻略资料与日英术语对照来源。
6. [Lucide](https://lucide.dev/)：图鉴管理台内联图标集的来源。

感谢上游作者、百科编辑者及所有贡献者的工作。

- 本项目代码采用 [MIT License](LICENSE)。
- 内置简介是 WiKirby 页面引语和导语的简体中文翻译及术语规范化派生内容，按 GNU Free Documentation License 1.3 或更高版本提供。每条记录保留来源页面与修订号，完整说明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
- Kirby Wiki | Fandom 的站点 API 标注内容许可为 CC BY-SA。
- 真格攻略 Wiki 仅在用户查询时读取公开页面，不打包其正文、表格或图片；返回内容仍受原站点的使用条款与版权规则约束。
- 管理台内联 102 个取自 Lucide `v1.28.0` 的 SVG 图标，按 ISC License 提供；其中源自 Feather 的图标保留 MIT License，完整声明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
- 百科页面图片、盟友素材和星之卡比相关角色版权归各自权利人所有。

在线百科查询会返回摘要、资料、名称和原页面链接；内置简介库仅打包图鉴所需的页面开头短介绍，不包含完整百科正文。
