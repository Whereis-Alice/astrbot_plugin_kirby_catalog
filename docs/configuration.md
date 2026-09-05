# 配置与数据规则

[← 返回 README](../README.md)

所有配置均可在 AstrBot 插件配置页面修改，不需要直接编辑源码。

| 配置组 | 常用选项 |
| --- | --- |
| 抽取与图鉴 | 每日抽取次数、今日/随机/查询文案、Bot 独立抽取、简介长度与回复形式、百科提示开关、详情编排、冷却时间、猜盟友超时、图鉴列数 |
| 图片发送与长卡片 | 协议端本地文件直发、共享目录、失败重试、JPEG 标准化、群图鉴高度、百科分页与图片安全阈值 |
| 百科文本与文档 | 显式文本命令是否合并转发、HTML 文档翻译、首图内嵌、保留时间和翻译分块大小 |
| WiKirby | 启用状态、首图、详细资料、回复形式、卡片模板、LLM 翻译、缓存和 Worker 中转 |
| Kirby Fandom | 启用状态、首图、详细栏目、回复形式、卡片模板、LLM 翻译、缓存和 Worker 失败中转 |
| 真格攻略 Wiki | 启用状态、首图、完整攻略栏目、回复形式、卡片模板、日文 LLM 翻译、缓存和 Worker 失败中转 |
| 数据兼容 | 旧素材图床地址、上游 AW 或自定义兼容目录 `legacy_data_dir` |

## 抽取规则

| 配置项 | 默认值 | 说明 |
| --- | ---: | --- |
| `daily_draw_limit` | `3` | 每个身份每日抽取次数；普通群友与 Bot 各自独立计数 |
| `allow_duplicate_draw` | `true` | 抽取盟友时是否允许抽到已解锁过的盟友 |

`allow_duplicate_draw` 为 `true` 时沿用原有机制：抽取在全部盟友中进行，可能抽到重复盟友，重复时按原有保底逻辑处理，消息会带“（重复）”“（保底）”标记。

设为 `false` 时，每次都只从“该身份尚未解锁的盟友”中抽取，因此在全部解锁之前不会再抽到重复条目，此时也不再出现保底标记。某个身份已经解锁全部盟友后，会退回从全部盟友中随机抽取一个，并在消息中标注“（已全部解锁）”。

这个开关对成员抽取和 `Bot今日盟友` 同样生效，也不影响 `daily_draw_limit` 控制的每日抽取次数。

## 盟友消息编排

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
| `bot_draw_enabled` | 启用 `Bot今日盟友` 和 Bot 抽取 LLM 工具；工具会主动发群消息和素材图 |
| `bot_draw_nickname` | Bot 独立身份的显示名称 |
| `bot_draw_identity` | 可选的 Bot QQ 稳定身份 ID；通常留空自动识别，仅在未来任务无法识别多账号/特殊连接时填写 |
| `bot_draw_llm_vision_enabled` | 让支持视觉的 LLM 查看 Bot 抽取素材和个人图鉴页面后自然回复，默认开启 |
| `bot_draw_message_template` | Bot 今日盟友基础文案，支持剩余次数和重复/保底标记 |
| `bot_show_in_leaderboard` | 是否让 Bot 出现在群排行榜，默认关闭 |

三个基础文案都支持 `{name}`、`{id}`、`{source}` 和 `{source_text}`。`今日盟友` 与 `Bot今日盟友` 另支持 `{nickname}`、`{flags}`、`{remaining}`；查询文案另支持 `{nickname}`、`{unlock_date}`、`{unlock_text}`。

`ally_detail_template` 支持 `{base}`、`{description}`、`{description_block}`、`{wiki_hint}` 和 `{wiki_hint_block}`。`description_block` 会自动生成换行、`简介：` 标题和正文；`wiki_hint_block` 会自动在提示前换行。模板字段错误时插件会记录警告并回退到默认编排。

## 图片发送与长图保护

AstrBot v4.26.8 的 aiocqhttp 适配器会把 `Image` 组件统一转换为 base64 后再交给协议端。base64 会增加约三分之一的传输体积和额外的编码内存；NapCat WebSocket 本身还有 `50 MiB` 的 `maxPayload`。即使文件体积很小，极端宽高或总像素过大的图片仍可能被 QQNT 拒绝并返回 `rich media transfer failed`。NapCat 对超大图片问题的处理建议也是由调用方自行兜底：[NapCatQQ #1443](https://github.com/NapNeko/NapCatQQ/issues/1443)。

插件默认使用“自动（推荐）”发送方式：

1. 在 aiocqhttp 平台（NapCat / LLBot / SnowLuma 等 OneBot 协议端）上优先把本地图片路径直接作为 OneBot `file://` 发送，避免 base64；
2. 失败后按配置重试；
3. 仍失败则回退 AstrBot 标准发送，不会因为直发不可用而吞掉消息；
4. 非 aiocqhttp 平台直接使用标准发送。

`今日盟友`、`随机盟友`、查盟友和图鉴读取已加载的内存图鉴，不会在每条消息中重新扫描整个素材库或所有群 JSON。素材通过 WebUI、管理员命令增删改时会即时更新内存；直接在服务器目录手工增删文件后，执行管理员的 `星之卡比图鉴迁移` 命令进行一次受控扫描，单纯重载不会重新遍历全部素材。

普通盟友图片与百科卡片使用独立发送规格。这样可以为超长百科卡片保留较高的尺寸与像素阈值，同时让每日抽取仍使用更小、更快、更稳定的图片副本。

常用配置：

| 配置项 | 默认值 | 说明 |
| --- | ---: | --- |
| `media_send_mode` | `自动（推荐）` | 三选一：`自动（推荐）`、`AstrBot标准发送`、`NapCat本地文件直发` |
| `media_shared_directory` | 留空 | AstrBot 可写、协议端可读的共享挂载目录 |
| `media_napcat_directory` | 留空 | 同一共享卷在协议端容器内使用不同路径时填写 |
| `media_direct_retry_count` | `1` | 本地文件直发失败后的重试次数 |
| `media_normalize_jpeg` | 开启 | 将 JPEG 统一为 RGB/JFIF，排除非标准编码问题 |
| `media_cleanup_interval_minutes` | `5` | 发送缓存的扫描清理间隔；`0` 表示每条消息都清理，不建议用于日常抽取 |
| `ally_media_max_width_px` / `ally_media_max_height_px` | `2160` / `8000` | 今日盟友、随机盟友、查盟友、猜盟友和图鉴的最终尺寸保护 |
| `ally_media_max_megapixels` / `ally_media_max_bytes_mb` | `18` / `8` | 盟友素材的总像素与文件大小保护，不影响百科卡片 |
| `ally_media_jpeg_quality` | `92` | 盟友素材的 JPEG 标准化和缩放副本质量 |
| `gallery_max_height_px` | `7600` | 群或个人图鉴超过此高度才分页；`0` 表示关闭 |
| `wiki_card_auto_paginate` | 开启 | 只分页真正过长的百科卡片 |
| `wiki_card_page_line_budget` | `110` | 每页内容预算，可配置范围 `60-3000`；复杂页面可从 `500` 或 `600` 开始测试 |
| `wiki_card_max_width_px` / `wiki_card_max_height_px` | `2160` / `8000` | WiKirby、Kirby Fandom、真格攻略 Wiki 和简介卡片的独立尺寸保护 |
| `wiki_card_max_megapixels` / `wiki_card_max_bytes_mb` | `18` / `8` | 百科卡片的独立总像素与文件大小保护 |
| `wiki_card_resolution` | `高清（推荐）` | 标准、高清或超清 |
| `wiki_card_image_format` | `JPEG` | JPEG 体积更小；PNG 保留无损文字边缘 |

`media_send_mode` 的 `NapCat本地文件直发` 选项名与 `media_napcat_directory` 键名沿用早期只支持 NapCat 时的命名，对 LLBot、SnowLuma 等其它 OneBot 协议端同样生效。

AstrBot 与协议端运行在同一系统、协议端能读取 AstrBot 文件路径时，共享目录可以留空。容器部署时应给两边挂载同一个目录：若容器内路径相同，只填写 `media_shared_directory`；若路径不同，再填写协议端侧的 `media_napcat_directory`。插件只会在共享目录下使用自己的 `astrbot_plugin_kirby_catalog` 子目录，并定期清理过期暂存图片；同一源文件未变化时会复用暂存副本，重复查询不再反复复制或转码。

群总图鉴会根据 `gallery_max_height_px` 自动分成少量图片；状态、素材和版式未变化时会直接复用上次生成结果。个人图鉴较短时仍只发送一张。百科分页和图鉴分页都不删除内容，只改变图片分组。若今日盟友等原始素材自身超过 `ally_media_*` 阈值，插件只创建等比缩放的发送缓存副本，原素材文件、图鉴编号和用户数据都不会被修改。百科卡片宽度超过自己的安全值时会自动降低一级渲染清晰度后重试。

`wiki_card_page_line_budget` 最高可设为 `3000`。它是分页目标，不是绕过图片安全检查的开关：如果单页渲染后仍超过 `wiki_card_max_height_px`、`wiki_card_max_megapixels` 或 `wiki_card_max_bytes_mb`，插件会自动降低预算重新分页。大幅提高这些图片阈值可能重新触发协议端与 QQNT 的上传失败。

`media_max_width_px`、`media_max_height_px`、`media_max_megapixels` 和 `media_max_bytes_mb` 是历史遗留的通用字段，已不再影响发送行为，避免曾为百科调高的阈值拖慢今日盟友；请使用上表中的 `ally_media_*` 与 `wiki_card_*` 字段分别配置。

## 真格名称速查图

| 配置项 | 默认值 | 说明 |
| --- | ---: | --- |
| `shinkaku_reference_use_bundled_image` | 开启 | 直接发送仓库内置的五列高清 PNG，不进行运行时渲染 |
| `shinkaku_reference_single_image` | 开启 | 关闭内置高清图后，把全部 301 个页面动态渲染为一张总览图；关闭则恢复分页图 |
| `shinkaku_reference_compact_columns` | `5` | 动态单张总览图列数；范围 `4-7`，内置高清图开启时不生效 |
| `shinkaku_reference_entries_per_page` | `50` | 旧版分页模式每页条目数；范围 `20-200`，单张模式下不生效 |
| `shinkaku_reference_columns` | `2` | 旧版分页模式列数；范围 `1-3`，单张模式下不生效 |
| `shinkaku_candidate_output_mode` | `合并转发` | 短名称匹配多个页面时，选择普通消息或合并转发 |

速查图不会改变目录编号。无论使用内置高清图、动态单张还是分页模式，`#1-#301` 始终按“作品 -> 原站栏目 -> 原站菜单顺序”排列；`卡比真格文档 88` 使用的就是这套编号。动态分页模式若最后一页只剩一列条目，会自动并入前一页，避免额外发送一张近乎空白的图片。

## 百科文本与 HTML 文档

| 配置项 | 默认值 | 说明 |
| --- | ---: | --- |
| `wiki_text_command_use_forward` | 开启 | `卡比百科文本`、`卡比F文本`、`卡比真格文本` 是否按栏目使用合并转发；关闭后发送普通文字 |
| `wiki_document_translate_enabled` | 开启 | 文档模式是否调用对应百科配置的 AstrBot Provider 翻译为简体中文 |
| `wiki_document_require_complete_translation` | 开启 | 文档翻译必须全部通过完整性校验；失败时先定点纠错、再安全拆分，最终仍失败才拒绝生成文件 |
| `wiki_document_include_image` | 开启 | 是否把页面首图写入 HTML；真格攻略表格里的图标和实机记录截图仍会尽量以内嵌图片保留 |
| `wiki_document_retention_minutes` | `1440` | 服务器上已生成 HTML 的保留时间；下次生成文档时清理过期文件，`0` 表示不自动清理 |
| `wiki_translation_chunk_chars` | `6000` | 长正文和结构化表格每次交给模型的目标字符数；模型上下文较小时建议 `4000-6000` |
| `wiki_translation_retry_depth` | `2` | 分块校验失败后定点纠错和递归拆小的最大层数；范围 `0-4` |
| `wiki_translation_min_chunk_chars` | `800` | 普通正文自动拆小时的目标下限；略小于该值的失败块仍可执行残留纠错 |
| `wiki_llm_vision_enabled` | 开启 | LLM 调用三个百科查询工具时，是否把页面首图和表格里的实机截图作为视觉输入一并交给模型 |
| `wiki_llm_vision_max_images` | `4` | 单次工具调用最多附带的图片数；范围 `0-8`，`0` 等同于关闭上一项 |

文档保存在 `data/plugin_data/astrbot_plugin_kirby_catalog/wiki_documents`，随后以文件消息发送。HTML 比超长图片更轻，能够保留可复制文字、原始链接和宽表格的横向滚动，也比 PDF 更适合手机与电脑自适应。文件不依赖外部样式；启用图片内嵌后，接收者保存到本地仍能查看首图、表格图标和实机记录截图。

普通 `卡比百科`、`卡比F`、`卡比真格` 仍遵循各自配置的默认回复形式。只有显式“文档”命令或把默认回复形式设为“百科文档”时才生成文件。

`wiki_llm_vision_enabled` 影响的是 LLM 主动调用百科工具的场景。真格攻略 Wiki 的「記録集」等页面把通关成绩直接写在实机截图里，对应的表格单元格没有任何文字，模型只读文字会得出“没有数据”。开启后，工具返回结果里会按“页面首图 → 表格截图”的顺序附上压缩后的原图，模型可以自己看图作答；关闭后模型只能看到文字，但正文里仍会给出图片直链。

## LLM 翻译

百科翻译默认关闭。开启后，插件使用 AstrBot 原生文本模型将英文简介和页面资料翻译为简体中文：

- 可以指定 Provider；
- Provider 留空时使用当前聊天会话的模型；
- 普通文本和卡片查询在个别分块最终失败时只回退受影响的规范化原文；文档模式默认要求全部翻译通过校验，否则不生成半翻译文件；
- 只有全部通过校验的译文才会写入插件进程内缓存。缓存键包含翻译管线、分块、重试、严格模式和名称库 revision；修改这些配置后不会误用旧结果；
- 每个普通文本分块都带独立首尾标记，并校验长度、标题层级、项目符号、段落、数字、URL、强调标记和源语言残留。失败后会把上一次译文、校验错误和残留日文上下文交给模型定点修复；仍失败才按 `wiki_translation_retry_depth` 递归拆小；
- 严格文档中的帧数、百分比、编号和其它阿拉伯数字会在交给模型前替换为逐项唯一的 `KNUM` 占位符，译文通过占位符校验后再原位恢复；模型不会再因把 `1回` 写成“一次”而触发整块 `missing_numbers` 重译，也不能悄悄丢失数值；
- Fandom 语录、招式和真格攻略表格会按栏目、语录、招式组或完整行拆分为多批 JSON，并校验栏目、表格行列和单元格数量。失败批次会先做带反馈的完整 JSON 纠错，再按完整表格行、语录或招式项目递归拆分，绝不会从中间切开一行；
- Fandom 语录和招式使用结构化 JSON 翻译，LLM 可以翻译操作中的自然语言，但按键、方向、加号、平台对应关系和换行由插件校验；布局和伤害数字不交给 LLM 决定；
- 真格攻略的日文正文和表格由 `shinkaku_translate_enabled` 单独控制，默认关闭；文档模式可以由 `wiki_document_translate_enabled` 单独要求翻译。翻译提示明确要求标题、专名、短句和括号说明全部转成中文，后台日志会列出有限数量的真实假名残留片段及上下文；`・` 等排版符号只记入 `jp_symbols`，不会误判为漏译，严格文档也不会混入失败批次的日文原文；
- LLM 工具调用本身不会再次触发嵌套翻译。

### 名称库与翻译

三个 Wiki 和对应的 LLM 查询工具共用 `resources/kirby_terminology.json` 名称库。正文交给模型前，插件会用 Aho-Corasick 匹配器快速识别专有名词，并将匹配结果替换为不可改写的临时占位符；模型只翻译普通句子，返回后恢复为统一的 `中文（English）`。真格攻略的日文原文也按同一规则输出中文双语名称。

名称库修改后会自动生成新的 revision，旧翻译缓存不会误复用。英文 Wiki 只使用英文别名匹配，真格日文 Wiki 只使用日文和英文别名匹配；短片假名还会检查词边界，避免把 `パターン`、`タイミング`、`クールタイム` 等普通词的一部分误认成角色名。插件会自动修复模型对占位符造成的 Markdown 加粗、反斜杠转义等可逆变形；同一个已知占位符被多输出时也能安全恢复。只有模型漏掉专名或凭空生成未知占位符时，该批内容才回退为已经规范化名称的原文，不会把错误机翻名称写进结果。没有开启 LLM 翻译时，默认仍会执行一次快速名称规范化，不产生模型调用。

| 配置项 | 默认值 | 说明 |
| --- | ---: | --- |
| `terminology_enabled` | 开启 | 启用三个 Wiki、普通指令和 LLM 工具的名称库 |
| `terminology_normalize_without_translation` | 开启 | 关闭 LLM 翻译时仍匹配并输出双语名称；关闭后完全保留原文 |
| `terminology_strict_placeholders` | 开启 | 自动修复可逆占位符变形；专名缺失或出现未知占位符时回退该批规范化原文，建议保持开启 |
| `terminology_log_matches` | 关闭 | 在日志中记录匹配数量，便于维护词库；不记录百科正文 |

名称库维护建议优先使用 WebUI。导入 JSON/CSV 会写入覆盖层，适合批量修订；导出的 merged 文件包含当前内置版本与覆盖结果，可作为审核或版本控制材料。名称库当前覆盖 2389 条记录，并在管理台显示 7 条缺少英文、154 条缺少日文和 41 个别名冲突，便于后续补齐，不会静默把不确定的名称当成官方译名。

维护者需要更新内置名称库时，可准备新的 WiKirby 页面缓存、能力形态清单或 BWIKI 缓存后运行：

```bash
python tools/build_kirby_terminology.py \
  --catalog resources/catalog_profiles.json \
  --forms path/to/catalog_forms.json \
  --wikirby-cache path/to/wikirby_pages.json.gz \
  --shinkaku resources/shinkaku_page_names.json \
  --output-json resources/kirby_terminology.json \
  --output-csv resources/kirby_terminology.csv \
  --audit resources/kirby_terminology_audit.json
```

不需要 BWIKI 网络抓取时可附加 `--skip-bwiki`；脚本默认使用插件目录下的 `.tmp/terminology/`，不会依赖任何维护者的个人电脑路径。生成后应检查审计文件中的缺失语言和冲突，再提交 JSON、CSV 与审计文件。

## 合并转发稳定性

| 配置项 | 默认值 | 说明 |
| --- | ---: | --- |
| `forward_node_max_chars` | `3000` | 单个合并转发文字节点的最大字符数，按段落优先拆分 |
| `forward_max_nodes_per_message` | `20` | 单条合并转发的最大节点数，超过后自动分成下一条合并转发 |
| `forward_max_images_per_message` | `2` | 单条合并转发最多包含的图片数；超长百科的文字与图片会分开打包 |
| `forward_direct_send_enabled` | `true` | aiocqhttp 平台上由插件直接发送，以便捕获异常并执行兜底 |
| `forward_retry_count` | `1` | 已无法继续拆分的单节点转发失败后重试次数 |
| `forward_retry_delay_seconds` | `0.5` | 单节点转发重试间隔 |
| `forward_batch_delay_seconds` | `0.2` | 多条转发之间的发送间隔；设为 `0` 更快，但连续上传稳定性可能降低 |

这些设置由 WiKirby、Kirby Fandom、真格攻略 Wiki 和 `查看简介` 的合并转发共用。普通短正文和一张图片仍会保持为一条转发；只有长正文、多卡片或节点超限时才拆分。通常应保持默认值：把字符数调得过大会重新形成超大节点，调得过小则会制造过多节点，同样可能触发 QQ 或协议端的限制。

## 百科 Cloudflare Worker

部分云服务器出口 IP 访问 WiKirby、Kirby Fandom 或 Seesaa 真格攻略 Wiki 时会收到 HTTP 403、429 或网络错误。WiKirby 会尝试官方 API、备用域名、REST API 和静态页面回退；Fandom 与真格攻略会优先直连，在 WAF、限流、服务器错误或网络失败时才尝试中转。

如果所有官方入口都不可用，可以部署可选中转：

- Worker 代码：[`cloudflare_worker/wikirby_proxy.js`](../cloudflare_worker/wikirby_proxy.js)
- 部署说明：[`cloudflare_worker/README.md`](../cloudflare_worker/README.md)

Worker 必须设置 `WIKIRBY_PROXY_TOKEN`。请勿把真实密钥提交到公开仓库、Issue 或日志截图中。

更新 Worker 后，它会通过严格白名单同时支持 WiKirby API/CDN、Kirby Fandom API/首图 CDN，以及真格攻略 Wiki 的页面、站内搜索和首图；不会转发到任意 URL。Fandom 与真格攻略的 `*_proxy_url`、`*_proxy_token` 留空时都会自动复用 WiKirby 的地址和密钥，也可单独填写另一套 Worker。真格攻略的站内搜索会保留 EUC-JP 原始编码，避免日文检索词在中转时损坏。详细的升级、测试与安全边界见 [Worker 部署说明](../cloudflare_worker/README.md)。


## 数据规则

- 对群友而言，只有 `今日盟友` 会更新自己的当前盟友、抽取次数和个人解锁图鉴；`Bot今日盟友` 只更新 Bot 的独立记录。`看爱丽丝盟友图鉴` 与 `kirby_catalog_view_bot_gallery` 只读取 Bot 在当前群的独立记录。Bot 在每个群每天有与 `daily_draw_limit` 相同的独立机会，默认 3 次。
- `今日盟友`、`随机盟友` 和 `查盟友` 默认显示内置或人工简介；简介与百科提示均可单独关闭。关闭自动简介后仍可引用消息发送 `查看简介`。
- 默认抽取文案显示盟友名、图鉴编号、首次登场作品和剩余次数；三个显示简介的命令均可在 WebUI 自定义基础文案和详情编排。无效模板会自动回退到默认文案。
- `随机盟友` 和 `猜盟友` 不写入用户图鉴。
- 改名会同步素材文件名和所有用户记录；固定编号保持不变。
- 换图不会删除用户解锁记录。
- 人工简介按稳定 `entry_key` 保存；改名不会丢失，恢复简介只删除人工覆盖。
- 个人进度只统计当前仍存在的有效条目；旧名称别名不会重复计数。
- 不同能力、形态、EX 版本和阶段使用不同 `entry_key`，在个人进度与排行榜中分别计数。
- 群图鉴和排行榜按群分别保存，不会跨群合并。
- 规范图鉴按最早登场年份、同年作品顺序、主角优先、条目类型和页面标题生成编号；正式迁移后编号固定，日常改名不会改变编号。
- 插件不包含与收藏图鉴无关的夺取或 NTR 机制，也不会读取对应旧状态文件。
