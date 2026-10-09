# 两个通道的细节、取 Key 步骤与 FAQ

## 一、为什么没有"官方 Key"

Google Scholar 是 Google 的免费学术检索入口，**从未提供公开 API**。所以市面上所有"Google Scholar API"本质上都是第三方代抓或代查服务。本 skill 因此提供两条路：

| 通道 | 本质 | 你付出什么 |
|------|------|-----------|
| `serpapi` | SerpAPI 这家服务代你去查 Google Scholar，返回结构化 JSON | 注册一个账号，拿 Key；免费档 100 次/月 |
| `direct` | 直接请求 `scholar.google.com/scholar` 并解析 HTML | 一个能访问 Google 的网络（梯子） |

> 不存在"用 Google 账号密码登录 Scholar 来检索"这条路。Scholar 上所有可见结果都是公开的，登录不带来任何额外内容；而程序化登录会触发风控，代价是账号被限制。**本 skill 不会索取、也不会存储任何平台登录密码。**

---

## 二、取 SerpAPI Key（约 3 分钟）

1. 打开 https://serpapi.com/users/sign_up 注册（邮箱 + 密码即可，不需要信用卡）。
2. 登录后进入 https://serpapi.com/manage-api-key ，复制那串 Key。
3. 写入配置：

```bash
python "<skill_dir>/scripts/gs_setup.py" --provider serpapi --api-key <粘贴KEY>
```

4. 脚本会自动调 `/account` 接口校验（**不消耗检索额度**）并打印剩余次数。

### 额度怎么算

- 免费档 **100 次搜索 / 月**，按自然月重置。
- **一个结果页 = 1 次**。每页最多 20 条。
  - 要 10 条 → 1 次
  - 要 25 条 → 2 次
  - 要 50 条 → 3 次（`max_pages` 默认上限 3 页，即最多 60 条）
- `/account` 这个校验接口**不计费**，可以随时 `--quota` 查。
- 本 skill 默认开 24 小时缓存，同一个检索式反复看不会重复扣。

> 100 次/月对个人做文献调研是够的。真不够时，`direct` 通道可以兜底（免费）。

---

## 三、找本机代理端口（走 direct 通道时）

`direct` 需要把流量指向你的梯子。常见客户端的默认端口：

| 客户端 | 默认 HTTP 代理端口 | 填法 |
|--------|------------------|------|
| Clash / Clash Verge / Mihomo | 7890 | `http://127.0.0.1:7890` |
| v2rayN | 10809 | `http://127.0.0.1:10809` |
| Shadowsocks (Windows) | 1080（通常只有 SOCKS5） | 需先在客户端开启"HTTP 代理" |
| 其他 | 看客户端设置里的"HTTP 代理 / 混合端口" | 照抄 |

写入：

```bash
python "<skill_dir>/scripts/gs_setup.py" --provider direct --proxy http://127.0.0.1:7890
```

**注意**：本 skill 用的是 Python 标准库，**只支持 HTTP/HTTPS 代理**，不支持纯 SOCKS5。如果客户端只开了 SOCKS5，请在客户端里把"允许 HTTP 代理 / 混合端口"打开，或者改用 `serpapi` 通道。

### direct 通道为什么容易被限流

Google 对 `scholar.google.com/scholar` 的自动化访问有风控：

- **机房 IP（云服务器、部分机场节点）几乎必然返回 429**，因为同一个出口 IP 被成千上万人共用。
- 家宽 IP 明显宽松，但也别在一个检索里翻太多页。
- 触发 Captcha 后，该 IP 通常会"冷静"几分钟到几小时。

因此：`direct` 适合**低频、精查**（一天几次）；要做批量调研，用 `serpapi`。

---

## 四、配置项说明（`config.json`）

| 字段 | 默认 | 说明 |
|------|------|------|
| `provider` | `serpapi` | 默认通道：`serpapi` / `direct` |
| `serpapi_api_key` | `null` | 只在使用 serpapi 通道时需要 |
| `proxy` | `null` | 例如 `http://127.0.0.1:7890`；留空则用系统/环境代理 |
| `language` | `en` | 传给 Scholar 的 `hl` 参数；中文结果可设 `zh-CN` |
| `default_limit` | `10` | 不写 `-n` 时的默认条数 |
| `cache_ttl_hours` | `24` | 缓存有效期；设 0 关闭缓存 |
| `max_pages` | `3` | 最多翻页数（每页 20 条，直接决定 serpapi 的额度消耗上限） |

配置文件位置：`<skill_dir>/config.json`，写入时权限设为仅本人可读，并已在 `.gitignore` 中排除。

---

## 五、FAQ

**Q：换电脑 / 重装后还要再配置一次吗？**
要。配置是本地文件，不跟着 skill 走。把 `config.json` 拷过去即可，不用重跑 setup。

**Q：能同时配两个通道吗？**
能。`config.json` 里两个字段可以同时有值，日常用 `provider` 指定的那个，临时用 `--provider` 切。

**Q：结果里的 PDF 链接是 Google 提供的吗？**
不一定。`direct` 通道取的是 Scholar 页面上标注的 `[PDF]` 链接源；`serpapi` 取的是同页面的资源字段。两者都可能是出版商页而非直接文件，能不能下载取决于是否有开放版本。

**Q：为什么中文关键词结果少？**
Google Scholar 对中文语料覆盖本来就弱于英文。建议：中文只用来定性，正式调研用英文关键词；同一主题中英各检一次再合并。

**Q：被引数是实时的吗？**
不是。它是 Google 索引里的值，有滞后；而且缓存 24 小时内不会刷新。要最新值加 `--no-cache`。

**Q：`--sort-by citations` 在 serpapi 通道下是 Google 排序吗？**
不是。Google 不提供按被引排序，本 skill 在客户端按返回结果重排。所以**只在你取回的这批结果内排序**——想找"全领域最高被引"，要先把 `-n` 开大一点再排。
