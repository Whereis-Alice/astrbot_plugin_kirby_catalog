# WiKirby / Kirby Fandom Cloudflare Worker 中转

这个 Worker 可同时为 WiKirby 与 Kirby Fandom 提供只读中转。它不是开放代理：只接受 `GET`、必须携带 Bearer 密钥，并只允许下列固定资源：

- WiKirby 的 MediaWiki API、REST API、raw 页面与 CDN 图片；
- `kirby.fandom.com/api.php`；
- Kirby Fandom 的首图 CDN：`static.wikia.nocookie.net`、`vignette.wikia.nocookie.net` 与受限的 `kirby.fandom.com/images/` 路径。

未带 `site` 的旧请求默认按 WiKirby 处理，因此已部署的 WiKirby 中转可以直接原地升级，无需新建 Worker 或更换密钥。

## 升级或部署

1. 在 Cloudflare Dashboard 打开已有的 Worker；没有 Worker 时，在 **Workers & Pages** 创建一个 Worker。
2. 用 [`wikirby_proxy.js`](wikirby_proxy.js) 的完整内容替换 Worker 代码并部署。
3. 保留或新建一个密钥：
   - 名称：`WIKIRBY_PROXY_TOKEN`
   - 值：随机长字符串，例如本机执行 `openssl rand -hex 32` 的输出。
4. 记下 Worker 根地址，例如：

   ```text
   https://kirby-wiki-proxy.<你的账户>.workers.dev
   ```

不要把密钥写入 GitHub、Issue、日志截图或群消息。

## 云服务器测试

先测试 WiKirby：

```bash
curl -i \
  -H 'Authorization: Bearer 你的密钥' \
  'https://你的-worker.workers.dev/?path=%2Fw%2Fapi.php&action=query&format=json&formatversion=2&titles=Driblee'
```

再测试 Fandom：

```bash
curl -i \
  -H 'Authorization: Bearer 你的密钥' \
  'https://你的-worker.workers.dev/?site=fandom&path=%2Fapi.php&action=query&format=json&formatversion=2&titles=Kirby'
```

两个请求均应返回 `HTTP/2 200`，并包含对应页面标题。若 Worker 自己也收到 `403`，表示上游站点同样拒绝 Cloudflare Worker 出口；这时请降低请求频率、等待后重试，或联系上游站点确认可用访问方式。

## AstrBot 插件配置

### 已配置 WiKirby Worker

更新 Worker 代码后，通常无需新增配置。Fandom 的“可选 Fandom Cloudflare Worker 中转地址”和“Fandom Cloudflare Worker 中转密钥”留空即可，插件会自动复用已有的 WiKirby Worker 地址与密钥。

Fandom 始终先直连；只有遇到 `403`、`429`、可重试的服务器错误或网络失败时，才通过 Worker 重试。一次中转成功后，插件会在短时间内优先使用该中转，避免一次查询中的多个 API 请求反复直连失败。首图下载也遵循相同规则。

### 使用单独的 Fandom Worker

若希望分开部署，可在 AstrBot 的“Kirby Fandom 查询设置”填写：

- `可选 Fandom Cloudflare Worker 中转地址`：Worker 根地址，不要添加 `/api.php`；
- `Fandom Cloudflare Worker 中转密钥`：该 Worker 的 `WIKIRBY_PROXY_TOKEN`。

保存后重载插件，测试：

```text
卡比F Kirby
卡比F Driblee
```

使用同一个 Worker 时，WiKirby 和 Fandom 共用缓存策略，但缓存键包含完整上游地址，彼此不会混淆。
