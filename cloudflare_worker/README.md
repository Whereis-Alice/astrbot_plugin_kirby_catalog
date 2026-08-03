# WiKirby Cloudflare Worker 中转

这个 Worker 不是开放代理，只允许转发到固定的 `wikirby.com`，并且要求 Bearer 密钥。

## 部署

1. 在 Cloudflare Dashboard 打开 **Workers & Pages**，创建一个 Worker。
2. 将 `wikirby_proxy.js` 的内容粘贴到 Worker 编辑器并部署。
3. 在 Worker 的 **Settings -> Variables and Secrets** 中新增加密密钥：
   - 名称：`WIKIRBY_PROXY_TOKEN`
   - 值：在本机执行 `openssl rand -hex 32` 生成的随机字符串
4. 记下 Worker 地址，例如：

   ```text
   https://kirby-wikirby-proxy.<你的账户>.workers.dev
   ```

不要把密钥写入 GitHub 或发到群里。

## 测试

在 AstrBot 云服务器上执行，将地址和密钥替换成自己的值：

```bash
curl -i \
  -H 'Authorization: Bearer 你的密钥' \
  'https://你的-worker.workers.dev/?path=%2Fw%2Fapi.php&action=query&format=json&formatversion=2&titles=Driblee'
```

看到 `HTTP/2 200` 且响应中有 `"title":"Driblee"`，说明中转可用。若 Worker 自己也收到 403，说明 WiKirby 连 Cloudflare Worker 出口也拦截了，此方案无法解决，需要联系 WiKirby 管理员或换一个经允许的中转出口。

图片也会由插件通过 Worker 下载，不需要额外开放 CDN 代理路径。

## 插件配置

在 AstrBot 的“星之卡比图鉴”配置中填写：

- `可选 Cloudflare Worker 中转地址`：Worker 根地址，不要添加 `/w/api.php`
- `Cloudflare Worker 中转密钥`：`WIKIRBY_PROXY_TOKEN` 的值

保存配置后重载插件，然后测试：

```text
卡比百科 Driblee
卡比百科名称 Driblee
```
