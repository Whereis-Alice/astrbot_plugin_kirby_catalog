# 协议端兼容性

[← 返回 README](../README.md)

插件通过 AstrBot 的 `aiocqhttp` 适配器工作，只要求协议端实现 OneBot v11。已验证可用的协议端为 NapCat、LLBot（LuckyLilliaBot）和 SnowLuma。插件代码不包含任何协议端专有 API 调用，也不手工拼接 CQ 码，因此更换协议端不需要修改插件配置中的任何键。

| 协议端 | 部署位置 | 连接方式 | 备注 |
| --- | --- | --- | --- |
| NapCat | 本地或容器 | 反向 WS | 本文档的发送与排查示例多以它为例；与 AstrBot 同机时可直接使用本地文件直发 |
| LLBot（LuckyLilliaBot） | 云端 | 反向 WS | 消息格式必须为 `array`；跨机部署时 `file://` 本地直发不可用 |
| SnowLuma | 本地 | 反向 WS | TypeScript 实现，自带 WebUI；对消息段的校验更严格 |

AstrBot 的 `aiocqhttp` 适配器只支持反向 WebSocket（适配器内部固定 `use_ws_reverse=True`），默认监听 `0.0.0.0:6199`。因此三个协议端都必须以反向 WS（`ws-reverse`）方式主动连接到 AstrBot，而不是等待 AstrBot 主动连接。

## NapCat

在 AstrBot 中新增 `aiocqhttp` 适配器，host 填 `0.0.0.0`、port 填 `6199`，再在 NapCat 中添加一个指向 `ws://127.0.0.1:6199/ws` 的反向 WS 客户端。NapCat 与 AstrBot 运行在同一系统、能够读取 AstrBot 写出的文件路径时，图片可以直接走本地文件直发，不需要填写共享目录；容器部署见 [图片发送与长图保护](configuration.md#图片发送与长图保护)。

## LLBot（LuckyLilliaBot）

LLBot 是云端协议端，官网 [luckylillia.com](https://luckylillia.com)，官方提供 [AstrBot 对接文档](https://luckylillia.com/guide/install_astrbot)。

AstrBot 侧同样新增 `aiocqhttp` 适配器，host 填 `0.0.0.0`、port 填 `6199`；LLBot 侧的连接配置形如：

```json
{"type":"ws-reverse","enable":true,"url":"ws://127.0.0.1:6199/ws","heartInterval":60000,"token":"","messageFormat":"array","reportSelfMessage":false}
```

需要注意：

- `messageFormat` 必须为 `array`。`string` 形式的 CQ 码不会被 AstrBot 正确解析。
- LLBot 的 `onlyLocalhost` 默认为 `true`。AstrBot 与 LLBot 不在同一台机器时，需要关闭它并设置 `token`，同时在 AstrBot 适配器的 `ws_reverse_token` 填入相同值。
- 图片 `file` 字段支持 `file://`、`http://` 和 `base64://`。

## SnowLuma

[SnowLuma](https://github.com/SnowLuma/SnowLuma) 是 TypeScript 实现的本地 OneBot v11 运行时，自带 WebUI，默认地址 `http://localhost:5099`；Lite 版需要 Node.js `22.13` 及以上。在它的 WebUI 中添加一个指向 `ws://127.0.0.1:6199/ws` 的反向 WS 连接即可，AstrBot 侧配置与前两者相同。

它接受的图片 `file` 形式包括 `base64://`、`data:`、`http(s)://`、`file://` 以及裸本地路径。它对消息段的校验比 NapCat 严格：拒绝空消息、消息段 `data` 必须是标量、未知段类型会直接报错。本插件发出的消息已经符合这些约束。

> [!IMPORTANT]
> SnowLuma 的许可为 NOASSERTION（非商业用途）。部署前请自行确认许可条款是否符合你的使用场景。

## 跨机部署与兼容细节

- `media_send_mode` 的本地文件直发依赖协议端能够读到 AstrBot 写出的真实文件路径。协议端与 AstrBot 不在同一文件系统时 `file://` 直发不可用：要么配置 `media_shared_directory`（两侧路径不同时再配置 `media_napcat_directory`），要么让插件自动回退到 AstrBot 标准发送（base64）。云端 LLBot 属于这种情况。
- 合并转发节点在 OneBot node 的 `data` 中同时写入 `user_id`/`nickname` 与 `uin`/`name` 两套字段，兼容 NapCat、LLBot 和 go-cqhttp 风格协议端。
- Bot 自身 QQ 号默认从当前 OneBot 连接自动识别。少数协议端或多账号场景无法识别时，手动填写 `bot_draw_identity`。
