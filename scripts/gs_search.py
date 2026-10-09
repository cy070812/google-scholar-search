#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Google Scholar 检索 CLI —— 配置一次，长期复用，零第三方依赖。

凭据只在首次安装时由 scripts/gs_setup.py 写入 config.json；
此后本脚本直接读取，不再向用户索取任何账号或密钥。

两个检索通道
------------
  serpapi   走 SerpAPI 的 Google Scholar 引擎。需要一次性填入 API Key（免费 100 次/月）。
            优点：稳定、不需要梯子、返回结构化数据。
  direct    直连 scholar.google.com 并解析页面。不需要任何账号。
            缺点：必须能访问 google（需梯子），且高频请求会触发人机验证。

用法示例
--------
  # 基本检索
  python gs_search.py "tensor decomposition LLM quantization" -n 10

  # 限定年份 + 按被引排序 + 输出 JSON
  python gs_search.py "低秩张量分解" --year-from 2022 --sort-by citations --json

  # 查看 SerpAPI 剩余额度
  python gs_search.py --quota

  # 离线自检（不联网，验证解析与渲染）
  python gs_search.py --selftest
"""
from __future__ import annotations

import argparse
import hashlib
import html as html_mod
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------
# 常量与路径
# --------------------------------------------------------------------------
SKILL_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = SKILL_DIR / "config.json"
CACHE_DIR = SKILL_DIR / ".cache"

SERPAPI_ENDPOINT = os.environ.get("GS_SERPAPI_ENDPOINT", "https://serpapi.com/search.json")
SERPAPI_ACCOUNT = os.environ.get("GS_SERPAPI_ACCOUNT", "https://serpapi.com/account")
SCHOLAR_ENDPOINT = os.environ.get("GS_SCHOLAR_ENDPOINT", "https://scholar.google.com/scholar")

DEFAULTS: dict[str, Any] = {
    "provider": "serpapi",       # serpapi | direct
    "serpapi_api_key": None,
    "proxy": None,               # 例：http://127.0.0.1:7890 或 http://user:pass@host:port
    "language": "en",
    "default_limit": 10,
    "cache_ttl_hours": 24,
    "max_pages": 3,              # 每页 20 条，最多翻多少页（SerpAPI 每页消耗 1 次额度）
}

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

YEAR_RE = re.compile(r"\b(1[89]\d{2}|20\d{2})\b")
TAG_RE = re.compile(r"<[^>]+>")


# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------
def _force_utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass


def eprint(*args: Any) -> None:
    print(*args, file=sys.stderr)


def strip_tags(fragment: str) -> str:
    text = TAG_RE.sub("", fragment or "")
    text = html_mod.unescape(text)
    text = text.replace("\xa0", " ").replace("\u200b", "")
    return re.sub(r"\s+", " ", text).strip()


def load_config() -> dict[str, Any]:
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for k in DEFAULTS:
                    if k in raw:
                        cfg[k] = raw[k]
                for k, v in raw.items():
                    cfg.setdefault(k, v)
        except Exception as exc:  # noqa: BLE001
            eprint(f"[warn] config.json 解析失败，改用默认值：{exc}")
    return cfg


def normalized_proxy(cfg: dict[str, Any]) -> str:
    p = (cfg.get("proxy") or "").strip()
    if p and "://" not in p:
        p = "http://" + p
    return p


def http_get(
    url: str,
    params: dict[str, Any] | None = None,
    cfg: dict[str, Any] | None = None,
    timeout: int = 30,
    extra_headers: dict[str, str] | None = None,
) -> tuple[int, str]:
    """返回 (status_code, body_text)。不抛异常，错误以 status=0 + body=错误信息 返回。"""
    if params:
        url = f"{url}?{urllib.parse.urlencode(params, doseq=True)}"
    headers = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    if extra_headers:
        headers.update(extra_headers)

    req = urllib.request.Request(url, headers=headers)
    proxy = normalized_proxy(cfg or {})
    if proxy:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        )
    else:
        opener = urllib.request.build_opener()

    try:
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
            try:
                body = raw.decode(charset, errors="replace")
            except LookupError:
                body = raw.decode("utf-8", errors="replace")
            return resp.getcode(), body
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            body = ""
        return exc.code, body
    except urllib.error.URLError as exc:
        return 0, f"网络不可达：{exc.reason}"
    except Exception as exc:  # noqa: BLE001
        return 0, f"请求异常：{type(exc).__name__}: {exc}"


# --------------------------------------------------------------------------
# 统一结果结构
# --------------------------------------------------------------------------
@dataclass
class Paper:
    title: str = ""
    authors: str = ""
    year: str = ""
    venue: str = ""
    abstract: str = ""
    url: str = ""
    pdf_url: str = ""
    cited_by: int = 0
    source: str = ""

    def to_public(self) -> dict[str, Any]:
        return asdict(self)


def _pick_year(*candidates: Any) -> str:
    for c in candidates:
        if not c:
            continue
        m = YEAR_RE.search(str(c))
        if m:
            return m.group(1)
    return ""


def _shorten_authors(names: list[str]) -> str:
    names = [n.strip() for n in names if n and n.strip()]
    if not names:
        return ""
    if len(names) > 3:
        return ", ".join(names[:3]) + " et al."
    return ", ".join(names)


def _split_authors_venue(text: str) -> tuple[str, str, str]:
    """解析 'A Zhang, B Li - ICML, 2024 - mlr.press' 这类字符串。"""
    if not text:
        return "", "", ""
    parts = [p.strip() for p in text.split(" - ") if p.strip()]
    if not parts:
        return "", "", ""

    authors = parts[0]
    year = ""
    venue = ""
    if len(parts) >= 2:
        mid = parts[1]
        m = YEAR_RE.search(mid)
        if m:
            year = m.group(1)
            venue = mid[: m.start()].strip().rstrip(",").strip()
            page_part = mid[m.end():].strip().lstrip(",").strip()
            if page_part and not venue.endswith(page_part):
                venue = f"{venue}, {page_part}" if venue else page_part
        else:
            venue = mid
    if not year:
        year = _pick_year(text)
    return authors, venue, year


# --------------------------------------------------------------------------
# 通道 1：SerpAPI
# --------------------------------------------------------------------------
def normalize_serpapi(item: dict) -> Paper:
    info = item.get("publication_info") or {}
    summary = (info.get("summary") or "").strip()

    authors = ""
    raw_authors = info.get("authors")
    if isinstance(raw_authors, list) and raw_authors:
        names = [a.get("name", "") for a in raw_authors if isinstance(a, dict) and a.get("name")]
        authors = _shorten_authors(names)
    if not authors and summary:
        authors = _split_authors_venue(summary)[0]

    _, venue, year = _split_authors_venue(summary)

    inline = item.get("inline_links") or {}
    cited = (inline.get("cited_by") or {}).get("total") or 0
    try:
        cited = int(cited)
    except (TypeError, ValueError):
        cited = 0

    pdf_url = ""
    for res in item.get("resources") or []:
        link = (res or {}).get("link") or ""
        if link.lower().endswith(".pdf"):
            pdf_url = link
            break
    link = (item.get("link") or "").strip()
    if not pdf_url and link.lower().endswith(".pdf"):
        pdf_url = link

    return Paper(
        title=(item.get("title") or "").strip(),
        authors=authors,
        year=year or _pick_year(item.get("year")),
        venue=venue,
        abstract=(item.get("snippet") or "").strip(),
        url=link,
        pdf_url=pdf_url,
        cited_by=cited,
        source="serpapi",
    )


def search_serpapi(query: str, cfg: dict[str, Any], args: argparse.Namespace) -> tuple[list[Paper], dict]:
    key = (cfg.get("serpapi_api_key") or "").strip()
    meta: dict[str, Any] = {"provider": "serpapi", "requests_used": 0, "pages": 0, "errors": []}
    if not key:
        if not CONFIG_PATH.exists():
            meta["errors"].append(
                "还没有配置文件（这是本 skill 唯一需要你做一次的事）。请先运行一次性配置：\n"
                f'        python "{Path(__file__).with_name("gs_setup.py")}"'
            )
        else:
            meta["errors"].append(
                "config.json 中没有 serpapi_api_key，但通道被设成了 serpapi。请先完成一次性配置：\n"
                f'        python "{Path(__file__).with_name("gs_setup.py")}" --provider serpapi --api-key <你的KEY>\n'
                "        或改用免账号的 direct 通道：\n"
                f'        python "{Path(__file__).with_name("gs_setup.py")}" --provider direct --proxy http://127.0.0.1:7890'
            )
        return [], meta

    per_page = 20
    want = max(1, args.limit)
    max_pages = int(cfg.get("max_pages") or 3)
    pages = min(max(1, -(-want // per_page)), max_pages)

    papers: list[Paper] = []
    for page in range(pages):
        params: dict[str, Any] = {
            "engine": "google_scholar",
            "q": query,
            "num": per_page,
            "start": page * per_page,
            "hl": cfg.get("language") or "en",
            "api_key": key,
        }
        if args.year_from:
            params["as_ylo"] = args.year_from
        if args.year_to:
            params["as_yhi"] = args.year_to
        if args.author:
            params["as_sauthors"] = args.author
        if args.sort_by == "year":
            params["scisbd"] = 1

        status, body = http_get(SERPAPI_ENDPOINT, params, cfg=cfg, timeout=args.timeout)
        meta["requests_used"] += 1
        meta["pages"] += 1

        if status == 0:
            meta["errors"].append(body)
            break
        if status == 401:
            meta["errors"].append("SerpAPI 返回 401：API Key 无效或已停用。请重新运行 gs_setup.py 更新 Key。")
            break
        if status == 429:
            meta["errors"].append("SerpAPI 返回 429：额度用尽或请求过快。隔天再试，或改用 direct 通道。")
            break
        if status != 200:
            meta["errors"].append(f"SerpAPI 返回 HTTP {status}：{body[:200]}")
            break

        try:
            data = json.loads(body)
        except Exception as exc:  # noqa: BLE001
            meta["errors"].append(f"响应不是合法 JSON：{exc}")
            break
        if "error" in data:
            meta["errors"].append(f"SerpAPI 报错：{data['error']}")
            break

        organic = data.get("organic_results") or []
        if not organic:
            break
        papers.extend(normalize_serpapi(it) for it in organic)
        if not (data.get("serpapi_pagination") or {}).get("next"):
            break

    return papers[:want], meta


def serpapi_account(cfg: dict[str, Any], timeout: int = 20) -> dict:
    key = (cfg.get("serpapi_api_key") or "").strip()
    if not key:
        return {"ok": False, "message": "config.json 中未配置 serpapi_api_key。"}
    status, body = http_get(SERPAPI_ACCOUNT, {"api_key": key}, cfg=cfg, timeout=timeout)
    if status == 0:
        return {"ok": False, "message": body}
    if status != 200:
        return {"ok": False, "message": f"HTTP {status}: {body[:200]}"}
    try:
        return {"ok": True, "data": json.loads(body)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": f"响应不是合法 JSON：{exc}"}


# --------------------------------------------------------------------------
# 通道 2：直连 scholar.google.com（零依赖解析）
# --------------------------------------------------------------------------
def _split_result_chunks(page: str) -> list[str]:
    """切出每个搜索结果块。

    注意：不能用 '<div class="gs_r' 直接 split——它同时会命中
    '<div class="gs_ri">' 和 '<div class="gs_rs">'，把结果块切碎。
    这里要求 gs_r 后面紧跟空格或引号。
    """
    chunks = re.split(r'<div class="gs_r(?=[\s"])', page)
    if len(chunks) > 1:
        return chunks[1:]
    # 兜底：某些排版变体没有 gs_r 容器
    chunks = re.split(r'<div class="gs_ri[">]', page)
    if len(chunks) > 1:
        return chunks[1:]
    if '<h3 class="gs_rt"' in page:
        return [page]
    return []


def parse_scholar_html(page: str) -> list[Paper]:
    papers: list[Paper] = []
    for chunk in _split_result_chunks(page):
        m_title = re.search(r'<h3 class="gs_rt"[^>]*>(.*?)</h3>', chunk, re.S)
        if not m_title:
            continue
        title_html = m_title.group(1)

        href = ""
        m_href = re.search(r'href="([^"]+)"', title_html)
        if m_href:
            href = html_mod.unescape(m_href.group(1))

        title = strip_tags(title_html)
        title = re.sub(r"^\[(PDF|HTML|BOOK|CITATION|引用|书籍)\]\s*", "", title).strip()
        title = re.sub(r"^\[[^\]]{0,12}\]\s*", "", title).strip()
        if not title:
            continue

        m_a = re.search(r'<div class="gs_a">(.*?)</div>', chunk, re.S)
        authors, venue, year = _split_authors_venue(strip_tags(m_a.group(1)) if m_a else "")

        m_rs = re.search(r'<div class="gs_rs">(.*?)</div>', chunk, re.S)
        abstract = strip_tags(m_rs.group(1)) if m_rs else ""

        cited = 0
        m_cite = re.search(r'<a[^>]+href="[^"]*(?:cites=|cites%3D)[^"]*"[^>]*>(.*?)</a>', chunk, re.S)
        if m_cite:
            cited = int(_first_int(strip_tags(m_cite.group(1))) or 0)

        pdf_url = ""
        m_gsm = re.search(r'<div class="gs_or_ggsm">.*?href="([^"]+)"', chunk, re.S)
        if m_gsm:
            pdf_url = html_mod.unescape(m_gsm.group(1))
        if not pdf_url and href.lower().endswith(".pdf"):
            pdf_url = href

        papers.append(
            Paper(
                title=title,
                authors=authors,
                year=year,
                venue=venue,
                abstract=abstract,
                url=href,
                pdf_url=pdf_url,
                cited_by=cited,
                source="direct",
            )
        )
    return papers


def _first_int(text: str) -> str:
    m = re.search(r"(\d[\d,\s]*)", text or "")
    if not m:
        return ""
    return re.sub(r"[^\d]", "", m.group(1))


def _looks_like_block(page: str) -> str | None:
    low = page.lower()
    if "not a robot" in low or "unusual traffic" in low or "/sorry/" in low or "captcha" in low:
        return (
            "Google 返回人机验证页（Captcha）。对策：挂梯子 + 在 config.json 里配置 proxy，"
            "间隔几分钟再试，或改用 serpapi 通道。"
        )
    if "<html" in low and "gs_r" not in low and "scholar" not in low:
        return "返回内容不是 Google Scholar 结果页（可能被网关/门户劫持）。请检查 proxy 配置。"
    return None


def search_direct(query: str, cfg: dict[str, Any], args: argparse.Namespace) -> tuple[list[Paper], dict]:
    meta: dict[str, Any] = {"provider": "direct", "requests_used": 0, "pages": 0, "errors": []}
    per_page = 20
    want = max(1, args.limit)
    max_pages = int(cfg.get("max_pages") or 3)
    pages = min(max(1, -(-want // per_page)), max_pages)

    papers: list[Paper] = []
    for page in range(pages):
        params: dict[str, Any] = {
            "q": query,
            "hl": cfg.get("language") or "en",
            "as_sdt": "0,5",
            "num": per_page,
            "start": page * per_page,
        }
        if args.year_from:
            params["as_ylo"] = args.year_from
        if args.year_to:
            params["as_yhi"] = args.year_to
        if args.author:
            params["as_sauthors"] = args.author
        if args.sort_by == "year":
            params["scisbd"] = 1

        status, body = http_get(SCHOLAR_ENDPOINT, params, cfg=cfg, timeout=args.timeout)
        meta["requests_used"] += 1
        meta["pages"] += 1

        if status == 0:
            meta["errors"].append(
                f"{body}\n        scholar.google.com 在国内直连不通，需要梯子。"
                "请在 config.json 里填写 proxy（例如 http://127.0.0.1:7890），或改用 serpapi 通道。"
            )
            break
        if status == 429:
            meta["errors"].append(
                "HTTP 429：被 Google 限流。若出口 IP 是机房/共享 IP（云服务器、部分代理节点），几乎必然 429；"
                "换成家宽节点再试，或改用 serpapi 通道。"
            )
            break
        if status != 200:
            meta["errors"].append(f"HTTP {status}：{body[:200]}")
            break

        blocked = _looks_like_block(body)
        if blocked:
            meta["errors"].append(blocked)
            break

        got = parse_scholar_html(body)
        if not got:
            break
        papers.extend(got)
        if len(got) < per_page:
            break

    return papers[:want], meta


# --------------------------------------------------------------------------
# 缓存
# --------------------------------------------------------------------------
def _cache_path(key: str) -> Path:
    return CACHE_DIR / f"{key}.json"


def cache_get(key: str, ttl_hours: float) -> list[dict] | None:
    if ttl_hours <= 0:
        return None
    p = _cache_path(key)
    if not p.exists():
        return None
    try:
        blob = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    if time.time() - float(blob.get("ts", 0)) > ttl_hours * 3600:
        return None
    return blob.get("papers")


def cache_put(key: str, papers: list[dict]) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_path(key).write_text(
            json.dumps({"ts": time.time(), "papers": papers}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:  # noqa: BLE001
        eprint(f"[warn] 缓存写入失败（不影响结果）：{exc}")


def cache_key(params: dict[str, Any]) -> str:
    stable = json.dumps(params, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(stable.encode("utf-8")).hexdigest()[:20]


# --------------------------------------------------------------------------
# 渲染
# --------------------------------------------------------------------------
def render_markdown(papers: list[Paper], query: str, meta: dict, elapsed: float, from_cache: bool) -> str:
    lines: list[str] = ["## 🔍 Google Scholar 检索结果", ""]
    cache_tag = "（本地缓存）" if from_cache else ""
    lines.append(
        f"**检索式**：`{query}`  ·  **通道**：{meta.get('provider')}  ·  "
        f"**返回**：{len(papers)} 条  ·  **耗时**：{elapsed:.1f}s  ·  "
        f"**请求数**：{meta.get('requests_used', 0)} 次{cache_tag}"
    )
    lines.append("")

    if not papers:
        lines.append("未取回任何结果。可能原因与对策：")
        lines.append("")
        lines.append("| 可能原因 | 对策 |")
        lines.append("|------|------|")
        lines.append("| 关键词太窄或中英混用 | 换成领域通用的英文术语，或拆成两次检索 |")
        lines.append("| 年份区间过严 | 放宽 `--year-from` / `--year-to` |")
        lines.append("| 通道被限流 / 验证码 | 间隔几分钟重试，或改用另一个 `--provider` |")
        lines.append("| 网络到不了 google | 配置 `proxy`（梯子），或改用 serpapi 通道 |")
        lines.append("| 凭据未配置 | 重新运行 `gs_setup.py` |")
    else:
        lines.append("| # | 标题 | 作者 | 年份 | 被引 |")
        lines.append("|---|------|------|------|------|")
        for i, p in enumerate(papers, 1):
            title = (p.title or "(无标题)").replace("|", "\\|")
            authors = (p.authors or "—").replace("|", "\\|")
            if len(authors) > 40:
                authors = authors[:40] + "…"
            lines.append(f"| {i} | {title} | {authors} | {p.year or '—'} | {p.cited_by} |")
        lines.append("")
        lines.append("---")
        lines.append("")
        for i, p in enumerate(papers, 1):
            lines.append(f"**{i}. {p.title or '(无标题)'}**")
            if p.authors:
                lines.append(f"- 作者：{p.authors}")
            src = " · ".join(x for x in (p.venue, p.year) if x)
            if src:
                lines.append(f"- 来源：{src}")
            links = []
            if p.url:
                links.append(f"[Scholar]({p.url})")
            if p.pdf_url:
                links.append(f"[PDF]({p.pdf_url})")
            tail = "  |  " + "  |  ".join(links) if links else ""
            lines.append(f"- 被引：{p.cited_by} 次{tail}")
            if p.abstract:
                abstract = p.abstract.strip().replace("\n", " ")
                if len(abstract) > 260:
                    abstract = abstract[:260] + "…"
                lines.append(f"- 摘要：{abstract}")
            lines.append("")

    errs = meta.get("errors") or []
    if errs:
        lines.append("> ⚠️ 请求未完全成功：")
        for e in errs:
            lines.append(f"> - {e}")
        lines.append("")

    if meta.get("provider") == "serpapi":
        lines.append("> 💡 SerpAPI 免费 100 次/月，每翻一页消耗 1 次。用 `--quota` 查剩余额度。")
    else:
        lines.append("> 💡 direct 通道免费但依赖梯子；短时间大量检索会触发 Google 人机验证。")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 离线自检
# --------------------------------------------------------------------------
SELFTEST_SCHOLAR_HTML = """
<div id="gs_res_ccl_mid">
  <div class="gs_r gs_or gs_scl" data-cid="abc123" data-rp="0">
    <div class="gs_ri">
      <h3 class="gs_rt"><a href="https://example.org/tensor-paper"><b>Tensor</b> Decomposition for Large Language Model Compression</a></h3>
      <div class="gs_a">San Zhang, Wei Li, Kai Chen - International Conference on Machine Learning, 2024 - proceedings.mlr.press</div>
      <div class="gs_rs">We propose a low-rank tensor decomposition method that cuts LLM weight memory by 4x with negligible accuracy loss.</div>
      <div class="gs_fl"><a href="/scholar?cites=999111222&amp;as_sdt=2005">Cited by 137</a></div>
    </div>
  </div>
  <div class="gs_r gs_or gs_scl" data-cid="def456" data-rp="1">
    <div class="gs_ri">
      <h3 class="gs_rt"><span class="gs_ctu">[PDF]</span> <a href="https://example.org/quant-paper.pdf">Ultra-Low Bit Quantization for LLMs</a></h3>
      <div class="gs_a">Q Liu, R Wang - NeurIPS, 2023 - openreview.net</div>
      <div class="gs_rs">A ternary quantization scheme for transformer inference.</div>
      <div class="gs_fl"><a href="/scholar?cites=888222333">Cited by 42</a></div>
    </div>
  </div>
</div>
"""

SELFTEST_SERPAPI_JSON = {
    "title": "Tensor Decomposition for Large Language Model Compression",
    "link": "https://example.org/paper",
    "snippet": "We propose a low-rank tensor decomposition method ...",
    "publication_info": {
        "summary": "Zhang S, Li W, Chen K - International Conference on Machine Learning, 2024 - proceedings.mlr.press",
        "authors": [{"name": "San Zhang"}, {"name": "Wei Li"}, {"name": "Kai Chen"}],
    },
    "inline_links": {"cited_by": {"total": 137}},
    "resources": [{"link": "https://example.org/paper.pdf"}],
}


def selftest() -> int:
    ok = True

    def check(label: str, got: Any, want: Any) -> None:
        nonlocal ok
        good = got == want
        if not good:
            ok = False
        print(f"[{'PASS' if good else 'FAIL'}] {label}: {got!r}")
        if not good:
            print(f"        expected: {want!r}")

    print("--- serpapi 归一化 ---")
    p = normalize_serpapi(SELFTEST_SERPAPI_JSON)
    check("title", p.title, "Tensor Decomposition for Large Language Model Compression")
    check("authors", p.authors, "San Zhang, Wei Li, Kai Chen")
    check("year", p.year, "2024")
    check("venue", p.venue, "International Conference on Machine Learning")
    check("cited_by", p.cited_by, 137)
    check("pdf_url", p.pdf_url, "https://example.org/paper.pdf")
    check("source", p.source, "serpapi")

    print("--- direct 页面解析 ---")
    check("结果块切分（不应切碎 gs_ri / gs_rs）", len(_split_result_chunks(SELFTEST_SCHOLAR_HTML)), 2)
    got = parse_scholar_html(SELFTEST_SCHOLAR_HTML)
    check("结果条数", len(got), 2)
    if len(got) == 2:
        check("1.title", got[0].title, "Tensor Decomposition for Large Language Model Compression")
        check("1.authors", got[0].authors, "San Zhang, Wei Li, Kai Chen")
        check("1.venue", got[0].venue, "International Conference on Machine Learning")
        check("1.year", got[0].year, "2024")
        check("1.cited_by", got[0].cited_by, 137)
        check("1.url", got[0].url, "https://example.org/tensor-paper")
        check("2.title", got[1].title, "Ultra-Low Bit Quantization for LLMs")
        check("2.pdf_url", got[1].pdf_url, "https://example.org/quant-paper.pdf")
        check("2.cited_by", got[1].cited_by, 42)
        check("2.year", got[1].year, "2023")
        check("2.venue", got[1].venue, "NeurIPS")

    print("--- 渲染 ---")
    md = render_markdown(got, "tensor decomposition", {"provider": "direct", "requests_used": 1, "errors": []}, 0.8, False)
    for token in ("## 🔍", "| 1 |", "被引：137 次", "[PDF]", "| 被引 |"):
        check(f"markdown 含 {token!r}", token in md, True)

    print("--- 空结果渲染 ---")
    empty_md = render_markdown([], "zzz", {"provider": "direct", "requests_used": 1, "errors": []}, 0.1, False)
    check("空结果含对策表", "| 可能原因 | 对策 |" in empty_md, True)

    print("--- 验证码识别 ---")
    check("识别 Captcha", _looks_like_block("<html>Our systems have detected unusual traffic from your computer network</html>") is not None, True)
    check("正常页不误报", _looks_like_block('<div class="gs_r gs_or gs_scl">...</div>') is None, True)

    print()
    print("SELFTEST OK" if ok else "SELFTEST FAILED")
    return 0 if ok else 1


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="gs_search.py",
        description="Google Scholar 检索（配置一次后长期复用，零第三方依赖）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("query", nargs="*", help="检索词")
    p.add_argument("-n", "--limit", type=int, default=None, help="返回条数，默认取 config.json 的 default_limit")
    p.add_argument("--year-from", type=int, default=None, help="起始年份（含）")
    p.add_argument("--year-to", type=int, default=None, help="结束年份（含）")
    p.add_argument("--author", default=None, help="限定作者")
    p.add_argument("--exact", action="store_true", help="精确短语匹配（给检索词加引号）")
    p.add_argument(
        "--sort-by",
        choices=["relevance", "citations", "year"],
        default="relevance",
        help="排序：relevance(默认) / citations(被引降序，客户端重排) / year(年份降序)",
    )
    p.add_argument("--provider", choices=["serpapi", "direct"], default=None, help="临时覆盖通道，不改 config.json")
    p.add_argument("--json", action="store_true", help="输出 JSON")
    p.add_argument("--md", action="store_true", help="输出 Markdown（默认）")
    p.add_argument("--no-cache", action="store_true", help="跳过缓存")
    p.add_argument("--clear-cache", action="store_true", help="清空本地缓存后退出")
    p.add_argument("--quota", action="store_true", help="查询 SerpAPI 剩余额度后退出")
    p.add_argument("--selftest", action="store_true", help="离线自检，不联网")
    p.add_argument("--timeout", type=int, default=30, help="单次请求超时秒数，默认 30")
    return p


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdout()
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.selftest:
        return selftest()

    if args.clear_cache:
        removed = 0
        if CACHE_DIR.exists():
            for f in CACHE_DIR.glob("*.json"):
                try:
                    f.unlink()
                    removed += 1
                except OSError:
                    pass
        print(f"已清空缓存，删除 {removed} 个文件：{CACHE_DIR}")
        return 0

    cfg = load_config()

    if args.quota:
        info = serpapi_account(cfg, timeout=args.timeout)
        if info.get("ok"):
            d = info["data"]
            print("## SerpAPI 额度")
            print()
            print(f"- 套餐：{d.get('plan_name', d.get('plan_id', '—'))}")
            print(f"- 本月已用：{d.get('this_month_usage', '—')}")
            print(f"- 剩余次数：{d.get('total_searches_left', d.get('plan_searches_left', '—'))}")
            print(f"- 账户：{d.get('account_email', '—')}")
            if d.get("plan_renewal_date"):
                print(f"- 重置日期：{d['plan_renewal_date']}")
        else:
            print("## SerpAPI 额度查询失败")
            print()
            print(info.get("message"))
            if not (cfg.get("serpapi_api_key") or "").strip():
                print()
                print("当前未配置 Key。若要用 SerpAPI 通道，请先运行 gs_setup.py 完成一次性配置。")
        return 0

    query = " ".join(args.query).strip()
    if not query:
        parser.print_help()
        return 1
    if args.exact and not (query.startswith('"') and query.endswith('"')):
        query = f'"{query}"'

    provider = args.provider or cfg.get("provider") or "serpapi"
    cfg["provider"] = provider
    limit = args.limit or int(cfg.get("default_limit") or 10)
    args.limit = limit

    cache_params = {
        "provider": provider,
        "query": query,
        "limit": limit,
        "year_from": args.year_from,
        "year_to": args.year_to,
        "author": args.author,
        "sort_by": args.sort_by,
    }
    ck = cache_key(cache_params)

    ttl = 0.0 if args.no_cache else float(cfg.get("cache_ttl_hours") or 0)
    cached = cache_get(ck, ttl)
    from_cache = cached is not None

    elapsed = 0.0
    meta: dict[str, Any] = {"provider": provider, "requests_used": 0, "pages": 0, "errors": []}

    if from_cache:
        eprint(f"[info] 命中本地缓存（{ck}），未消耗检索额度。加 --no-cache 可强制重新检索。")
        papers = [
            Paper(**{k: v for k, v in d.items() if k in Paper.__dataclass_fields__})
            for d in (cached or [])
        ]
    else:
        t0 = time.time()
        if provider == "serpapi":
            papers, meta = search_serpapi(query, cfg, args)
        else:
            papers, meta = search_direct(query, cfg, args)
        elapsed = time.time() - t0

        if args.sort_by == "citations":
            papers.sort(key=lambda x: x.cited_by, reverse=True)
        elif args.sort_by == "year":
            papers.sort(key=lambda x: x.year or "0", reverse=True)
        else:
            papers = papers[:limit]

        if papers and ttl > 0:
            cache_put(ck, [p.to_public() for p in papers])

    if args.json:
        print(
            json.dumps(
                {
                    "query": query,
                    "provider": meta.get("provider", provider),
                    "from_cache": from_cache,
                    "count": len(papers),
                    "elapsed_sec": round(elapsed, 2),
                    "requests_used": meta.get("requests_used", 0),
                    "errors": meta.get("errors", []),
                    "results": [p.to_public() for p in papers],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(render_markdown(papers, query, meta, elapsed, from_cache))

    if papers:
        return 0
    return 4 if (meta.get("errors") or []) else 0


if __name__ == "__main__":
    raise SystemExit(main())
