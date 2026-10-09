---
name: google-scholar-search
description: "用 Google Scholar 检索学术论文。安装后只需一次性配置凭据（或选择免账号通道），之后每次调用都不再需要输入任何账号密码。支持关键词检索、年份区间、作者限定、被引排序、结果缓存、Markdown/JSON 输出，并可通过 refs 追踪研究脉络。当用户说\"用 Google Scholar 查论文\"\"在谷歌学术上搜一下\"\"找一下这个方向被引最高的论文\"\"查引用次数\"时触发。"
description_zh: "Google Scholar 论文检索。一次配置、长期复用：安装后仅需输入一次凭据，后续调用零输入。支持年份区间、作者限定、被引排序、缓存与 JSON 输出。"
description_en: "Search papers on Google Scholar. One-time credential setup, then zero-input reuse forever. Supports year range, author filter, citation sorting, local caching, and Markdown/JSON output."
version: 1.0.0
display_name: "google-scholar-search"
display_name_en: "google-scholar-search"
visibility: "public"
agent_created: true
---

# Google Scholar 检索（一次配置，长期复用）

## 这个 skill 解决的核心问题

**凭据只输入一次。** 安装后跑一次 `gs_setup.py`，凭据写进 `config.json`；之后所有检索都由 `gs_search.py` 直接执行，**不再向用户索取任何账号密码**。

## ⚠️ 先读这一条：Google Scholar 没有官方 API

这是必须先讲清楚的事实，否则后面的一切都会走偏：

| 事实 | 后果 |
|------|------|
| Google Scholar **不提供官方检索 API** | 只能靠第三方通道，没有"官方 Key"这回事 |
| Scholar 的内容**不需要登录** | 所以**不存在"用 Google 账号密码检索"这回事**，拿账号密码登录反而会触发风控、有封号风险 |
| 有用的"凭据"是**第三方 API Key**，不是你的 Google 密码 | 本 skill 只需要前者，且**永远不会**索要后者 |

> **硬规则**：如果用户提出要用 Google 账号密码来跑检索，必须拒绝并解释上面两条，然后引导到下面两个通道。

## 两个通道

| 通道 | 需要什么 | 依赖 | 需要梯子 | 稳定性 | 额度 |
|------|---------|------|---------|--------|------|
| **`serpapi`** | 一个 SerpAPI Key（一次性填入） | 无（纯标准库） | 否 | ★★★★★ | 免费 100 次/月 |
| **`direct`** | 什么都不需要 | 无（纯标准库） | 是 | ★★★☆☆ | 免费，但会被 Google 限流 |

- 选 `serpapi`：要稳、要结构化、不想折腾梯子。注册拿 Key 的步骤见 `references/providers.md`。
- 选 `direct`：不想注册任何东西，且有可用梯子。短时间大量检索会触发人机验证（Captcha）。

两条路都**只依赖 Python 标准库**，不需要 `pip install` 任何东西。

---

## 启动前必须执行：检查配置是否存在

```
读取 <skill_dir>/config.json
  ├── 存在且凭据有效 → 直接进入检索
  └── 不存在 / 凭据为空 → 停下来，问用户一次（这是唯一一次）：
        用 AskUserQuestion 给两个选项，而不是逐个问：
          [1] SerpAPI 通道 —— 稳定，不需要梯子，需要注册拿一个免费 Key（免费 100 次/月）
          [2] 直连通道   —— 不需要任何账号，但需要有梯子
        用户选完 → 按下表执行一次 setup → 之后永远不再问
```

用户选定后执行（把 `<选择>` 替换成实际值）：

```bash
# 通道 1：SerpAPI
python "<skill_dir>/scripts/gs_setup.py" --provider serpapi --api-key <KEY>

# 通道 2：直连（<代理端口> 例如 7890 / 10809；填错可先留空试试）
python "<skill_dir>/scripts/gs_setup.py" --provider direct --proxy http://127.0.0.1:<代理端口>
```

> **密钥安全**：只接受 API Key。若用户粘过来的是类似密码的字符串，先确认它是 Key 再写入。
> `config.json` 已在 `.gitignore` 里，且写入时设为仅本人可读。

---

## 日常调用（配置完成后，零输入）

```bash
# 基本检索
python "<skill_dir>/scripts/gs_search.py" "low-rank tensor decomposition LLM quantization" -n 10

# 限定年份 + 按被引降序（找该方向最有影响力的工作）
python "<skill_dir>/scripts/gs_search.py" "tensor decomposition quantization" --year-from 2023 --sort-by citations -n 15

# 精确短语 + 限定作者
python "<skill_dir>/scripts/gs_search.py" "mixture-of-experts quantization" --exact --author "Song Han"

# 机器可读输出（供后续做统计/画图/写综述）
python "<skill_dir>/scripts/gs_search.py" "LLM quantization" --json -n 20

# 查 SerpAPI 剩余额度
python "<skill_dir>/scripts/gs_search.py" --quota

# 强制不用缓存
python "<skill_dir>/scripts/gs_search.py" "..." --no-cache
```

### 参数一览

| 参数 | 说明 |
|------|------|
| `-n, --limit` | 返回条数（默认取 config 的 `default_limit`，建议 10） |
| `--year-from` / `--year-to` | 年份区间，含端点 |
| `--author` | 限定作者 |
| `--exact` | 精确短语匹配（自动加引号） |
| `--sort-by` | `relevance`（默认）/ `citations`（被引降序）/ `year`（年份降序） |
| `--provider` | 临时切通道，不改 config |
| `--json` | 输出 JSON（含 `errors` 与 `requests_used`） |
| `--no-cache` | 跳过本地缓存 |
| `--quota` | 查 SerpAPI 剩余额度 |
| `--clear-cache` | 清空缓存 |
| `--selftest` | 离线自检，不联网 |

### 额度消耗（重要，别让用户白烧额度）

- **缓存默认开**：同一「检索式 + 参数」在 `cache_ttl_hours`（默认 24h）内不重复请求。调参反复试的时候记得这一点。
- **`serpapi` 按请求计费**：每页 20 条 = 1 次额度。要 25 条 → 2 次；要 50 条 → 3 次（`max_pages` 默认上限 3 页 = 60 条）。
- **`direct` 不花额度但花 IP 信誉**：短时间内连续检索最容易被封。

---

## 输出与后续加工

默认输出 Markdown：一张总表（序号 / 标题 / 作者 / 年份 / 被引）+ 每条明细（作者、来源、被引、Scholar 链接、PDF 链接、摘要截断）。

拿到结果后可以做的事：

| 用户意图 | 怎么做 |
|---------|--------|
| 找这个方向"最硬"的论文 | `--sort-by citations`，看被引数排序 |
| 追最新进展 | `--sort-by year --year-from <今年-2>` |
| 读全文 | 结果里的 `pdf_url`；没有就顺着 `url` 去 Scholar 页看有没有开放版本 |
| 做文献综述/统计 | 加 `--json`，把 JSON 交给后续脚本处理 |
| 顺藤摸瓜找脉络 | 先定位一篇高被引论文，再按它的标题 + 作者二次检索 |

---

## 故障排查

| 现象 | 原因 | 怎么办 |
|------|------|--------|
| `401 Invalid API key` | Key 抄错 / 已停用 | `gs_setup.py --api-key <新KEY>` 覆盖 |
| `429` 且通道是 serpapi | 本月 100 次用完 / 请求过快 | 等重置；或临时 `--provider direct` |
| `429` 且通道是 direct | 出口 IP 被 Google 限流（机房 IP 几乎必然） | 换家宽节点；或改用 serpapi |
| 连接失败 / 超时 | 到不了 scholar.google.com | 挂梯子并把代理填进 `proxy` |
| 返回人机验证页 | Google 判定为自动化流量 | 降频、换节点；或改用 serpapi |
| 结果为空但不报错 | 关键词太窄 | 换英文通用术语；放宽年份 |
| `pip` 装不上导致报错 | —— | 本 skill 不需要任何第三方库，若出现此类报错说明用错了 Python 解释器 |

排查命令：

```bash
python "<skill_dir>/scripts/gs_setup.py" --show     # 看当前配置（Key 打码）
python "<skill_dir>/scripts/gs_setup.py" --test     # 校验凭据 + 连通性 + 真实解析条数
python "<skill_dir>/scripts/gs_search.py" --selftest   # 离线自检
```

---

## 资源文件

| 文件 | 作用 |
|------|------|
| `scripts/gs_setup.py` | 一次性配置（交互式 / 参数式），含凭据与连通性校验 |
| `scripts/gs_search.py` | 检索主程序（双通道 + 缓存 + 渲染 + 自检） |
| `config.example.json` | 配置模板；实际配置写入 `config.json` |
| `references/providers.md` | 怎么拿 SerpAPI Key、怎么找本机代理端口、通道细节与 FAQ |
| `tests/e2e_mock.py` | 端到端测试：本地 mock 出 SerpAPI 返回，跑完整链路，不联网不耗额度 |

---

## 首次安装后的自检清单

```bash
# 1) 解析与渲染逻辑（离线，26 项断言）
python "<skill_dir>/scripts/gs_search.py" --selftest

# 2) 完整链路（本地 mock，不联网不耗额度）
python "<skill_dir>/tests/e2e_mock.py"

# 3) 真实凭据 + 连通性（需要网络）
python "<skill_dir>/scripts/gs_setup.py" --test

# 4) 真跑一次检索
python "<skill_dir>/scripts/gs_search.py" "low-rank tensor decomposition LLM" -n 5
```
