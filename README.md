# google-scholar-search

> 一个 **一次配置、长期复用** 的 Google Scholar 检索 Skill。安装后只需输入一次凭据，之后每次调用都不再需要任何账号或密码。

## 它解决什么问题

Google Scholar **没有官方 API**，而程序化登录 Google 账号既不必要（Scholar 结果全部公开）又有封号风险。
这个 Skill 把"凭据"重新定义为**第三方服务 API Key**，并且：

- **只在首次配置时输入一次**凭据，写入本地 `config.json`；
- 之后每次检索都由脚本直接读取配置，**零输入**；
- **永远不会**索取或存储任何平台登录密码。

## 两个检索通道

| 通道 | 需要什么 | 需要梯子 | 稳定性 | 额度 |
|------|---------|---------|--------|------|
| `serpapi` | 一个 [SerpAPI](https://serpapi.com/users/sign_up) Key | 否 | ★★★★★ | 免费 100 次/月 |
| `direct` | 什么都不需要 | 是 | ★★★☆☆ | 免费，会被 Google 限流 |

两条路都**只依赖 Python 标准库**，不需要 `pip install` 任何东西。

## 安装

把本目录（或解压后的同名文件夹）放到 WorkBuddy 的技能目录：

```
~/.workbuddy/skills/google-scholar-search/
```

Windows 下即 `C:\Users\<你>\.workbuddy\skills\google-scholar-search\`。

## 一次性配置（唯一需要输入凭据的一步）

```bash
# 通道 A：SerpAPI（稳，不需要梯子）
python scripts/gs_setup.py --provider serpapi --api-key <你的KEY>

# 通道 B：直连（免费，需要有梯子）
python scripts/gs_setup.py --provider direct --proxy http://127.0.0.1:7890
```

不加参数直接运行 `gs_setup.py` 会进入交互式引导。

## 日常使用

```bash
# 基本检索
python scripts/gs_search.py "low-rank tensor decomposition LLM quantization" -n 10

# 限定年份 + 按被引降序
python scripts/gs_search.py "tensor decomposition quantization" --year-from 2023 --sort-by citations -n 15

# 精确短语 + 限定作者
python scripts/gs_search.py "mixture-of-experts quantization" --exact --author "Song Han"

# 输出 JSON（便于二次加工）
python scripts/gs_search.py "LLM quantization" --json -n 20

# 查 SerpAPI 剩余额度
python scripts/gs_search.py --quota
```

### 参数

| 参数 | 说明 |
|------|------|
| `-n, --limit` | 返回条数 |
| `--year-from` / `--year-to` | 年份区间（含端点） |
| `--author` | 限定作者 |
| `--exact` | 精确短语匹配 |
| `--sort-by` | `relevance` / `citations` / `year` |
| `--provider` | 临时切换通道 |
| `--json` | JSON 输出 |
| `--no-cache` | 跳过缓存 |
| `--quota` | 查额度 |
| `--selftest` | 离线自检 |

## 自检

```bash
python scripts/gs_search.py --selftest   # 26 项断言，离线
python tests/e2e_mock.py                 # 端到端，本地 mock，不联网不耗额度
python scripts/gs_setup.py --test        # 真实凭据 + 连通性 + 解析条数
```

## 目录结构

```
google-scholar-search/
├── SKILL.md                     # Skill 主入口（供 Agent 读取）
├── README.md
├── config.example.json          # 配置模板
├── .gitignore
├── scripts/
│   ├── gs_setup.py              # 一次性配置
│   └── gs_search.py             # 检索主程序
├── references/
│   └── providers.md             # 通道细节、取 Key 步骤、FAQ
└── tests/
    └── e2e_mock.py              # 端到端测试
```

## 额度说明

- `serpapi`：**每页 20 条 = 1 次额度**。要 25 条 → 2 次；要 50 条 → 3 次（默认上限 3 页 / 60 条）。
- 默认开启 **24 小时缓存**，同一检索式反复看不会重复扣额度。
- `--quota` 走的 account 接口**不计费**。

## 注意

- `config.json` 含你的 API Key，已在 `.gitignore` 中排除，请勿提交。
- `direct` 通道在机房 / 共享 IP 上几乎必然返回 429，家用宽带才稳定。

## License

未指定。如需开源请自行添加。
