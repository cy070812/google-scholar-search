#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
端到端测试（不联网、不消耗任何额度）

做法：起一个本地 HTTP 服务，模拟 SerpAPI 的 google_scholar 返回，
把 gs_search 的端点指向它，然后跑完整的「请求 → 分页 → 归一化 → 缓存 → 渲染」链路。

为什么需要它：真实 SerpAPI 需要 Key，真实 Google Scholar 需要梯子，
这两样在开发/自检环境里通常都没有。这个 mock 让整条链路可以被验证。

运行：
  python tests/e2e_mock.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR / "scripts"))

PORT = 8791
BASE = f"http://127.0.0.1:{PORT}"
os.environ["GS_SERPAPI_ENDPOINT"] = f"{BASE}/search.json"
os.environ["GS_SERPAPI_ACCOUNT"] = f"{BASE}/account"

import gs_search  # noqa: E402  必须在设置 env 之后导入

VENUES = [
    ("International Conference on Machine Learning", "proceedings.mlr.press"),
    ("Neural Information Processing Systems", "papers.nips.cc"),
    ("IEEE Transactions on Pattern Analysis and Machine Intelligence", "ieeexplore.ieee.org"),
]
AUTHORS = [
    [{"name": "San Zhang"}, {"name": "Wei Li"}, {"name": "Kai Chen"}],
    [{"name": "Qiang Liu"}, {"name": "Rui Wang"}],
    [{"name": "Ming Zhao"}, {"name": "Hua Sun"}, {"name": "Lei Xu"}, {"name": "Yan Ma"}],
]


def make_results(start: int, count: int) -> list[dict]:
    out = []
    for i in range(count):
        idx = start + i
        venue, publisher = VENUES[idx % len(VENUES)]
        authors = AUTHORS[idx % len(AUTHORS)]
        year = 2025 - (idx % 4)
        name_str = ", ".join(a["name"] for a in authors)
        out.append(
            {
                "position": idx + 1,
                "title": f"Mock Paper {idx + 1} on Tensor Decomposition and LLM Quantization",
                "link": f"https://example.org/paper/{idx + 1}",
                "snippet": f"Abstract of mock paper {idx + 1}: low-rank tensor decomposition for efficient LLM inference.",
                "publication_info": {
                    "summary": f"{name_str} - {venue}, {year} - {publisher}",
                    "authors": authors,
                },
                "inline_links": {"cited_by": {"total": 500 - idx * 7}},
                "resources": [{"link": f"https://example.org/paper/{idx + 1}.pdf"}],
            }
        )
    return out


class Handler(BaseHTTPRequestHandler):
    total = 45  # 模拟 Google Scholar 的总命中数

    def log_message(self, *args):  # 静音
        pass

    def _json(self, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)

        if parsed.path == "/account":
            self._json(
                {
                    "account_email": "test@example.com",
                    "plan_name": "Free",
                    "plan_searches_left": 100,
                    "total_searches_left": 100,
                    "this_month_usage": 0,
                    "plan_renewal_date": "2026-11-01",
                }
            )
            return

        if parsed.path == "/search.json":
            if qs.get("api_key", [""])[0] != "MOCK_KEY":
                self.send_response(401)
                body = json.dumps({"error": "Invalid API key."}).encode()
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            start = int(qs.get("start", ["0"])[0])
            num = int(qs.get("num", ["20"])[0])
            n = max(0, min(num, self.total - start))
            payload = {
                "search_metadata": {"status": "Success"},
                "search_parameters": {"q": qs.get("q", [""])[0], "engine": "google_scholar"},
                "organic_results": make_results(start, n),
            }
            if start + n < self.total:
                payload["serpapi_pagination"] = {"next": f"{BASE}/search.json?start={start + num}"}
            self._json(payload)
            return

        self.send_response(404)
        self.end_headers()


class SilentServer(HTTPServer):
    def handle_error(self, request, client_address):  # noqa: D102
        pass


def main() -> int:
    server = SilentServer(("127.0.0.1", PORT), Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    ok = True

    def check(label: str, got, want) -> None:
        nonlocal ok
        good = got == want
        if not good:
            ok = False
        print(f"[{'PASS' if good else 'FAIL'}] {label}: {got!r}")
        if not good:
            print(f"        expected: {want!r}")

    cache_dir = SKILL_DIR / ".cache"
    if cache_dir.exists():
        shutil.rmtree(cache_dir, ignore_errors=True)

    cfg = {
        "provider": "serpapi",
        "serpapi_api_key": "MOCK_KEY",
        "proxy": None,
        "language": "en",
        "default_limit": 10,
        "cache_ttl_hours": 24,
        "max_pages": 3,
    }

    class Args:
        limit = 25
        year_from = None
        year_to = None
        author = None
        sort_by = "relevance"
        timeout = 15

    try:
        print("--- 分页 + 归一化（要 25 条，每页 20，应翻 2 页）---")
        papers, meta = gs_search.search_serpapi("tensor decomposition quantization", cfg, Args())
        check("条数", len(papers), 25)
        check("请求数", meta["requests_used"], 2)
        check("无错误", meta["errors"], [])
        check("第 1 条标题", papers[0].title, "Mock Paper 1 on Tensor Decomposition and LLM Quantization")
        check("第 1 条作者", papers[0].authors, "San Zhang, Wei Li, Kai Chen")
        check("第 1 条年份", papers[0].year, "2025")
        check("第 1 条venue", papers[0].venue, "International Conference on Machine Learning")
        check("第 1 条被引", papers[0].cited_by, 500)
        check("第 1 条PDF", papers[0].pdf_url, "https://example.org/paper/1.pdf")
        check("第 3 条作者(4人应缩略)", papers[2].authors, "Ming Zhao, Hua Sun, Lei Xu et al.")
        check("第 2 条作者(2人)", papers[1].authors, "Qiang Liu, Rui Wang")
        check("第 25 条来自第 2 页", papers[24].title, "Mock Paper 25 on Tensor Decomposition and LLM Quantization")

        print("--- 缓存：同一检索式第二次应命中缓存 ---")
        ck = gs_search.cache_key({"provider": "serpapi", "query": "tensor decomposition quantization", "limit": 25})
        gs_search.cache_put(ck, [p.to_public() for p in papers])
        cached = gs_search.cache_get(ck, 24)
        check("缓存可读回", cached is not None and len(cached), 25)
        check("缓存 TTL=0 时视为未命中", gs_search.cache_get(ck, 0), None)

        print("--- 渲染（Markdown）---")
        md = gs_search.render_markdown(papers, "tensor decomposition quantization", meta, 1.4, False)
        check("含表头", "| # | 标题 | 作者 | 年份 | 被引 |" in md, True)
        check("含第 25 行", "| 25 |" in md, True)
        check("含被引数", "被引：500 次" in md, True)
        check("含 PDF 链接", "(https://example.org/paper/1.pdf)" in md, True)

        print("--- 渲染（JSON）---")
        payload = json.loads(
            json.dumps(
                {
                    "count": len(papers),
                    "results": [p.to_public() for p in papers],
                },
                ensure_ascii=False,
            )
        )
        check("JSON 条数", payload["count"], 25)
        check("JSON 首条字段完整", sorted(payload["results"][0].keys()),
              sorted(["title", "authors", "year", "venue", "abstract", "url", "pdf_url", "cited_by", "source"]))

        print("--- 错误 Key 应给出 401 提示 ---")
        bad = dict(cfg, serpapi_api_key="WRONG")
        papers2, meta2 = gs_search.search_serpapi("x", bad, Args())
        check("无结果", len(papers2), 0)
        check("有 401 报错", any("401" in e for e in meta2["errors"]), True)

        print("--- 额度查询 ---")
        info = gs_search.serpapi_account(cfg)
        check("account 可用", info.get("ok"), True)
        check("剩余额度字段", info["data"]["total_searches_left"], 100)
    finally:
        server.shutdown()
        server.server_close()
        shutil.rmtree(cache_dir, ignore_errors=True)

    print()
    print("E2E OK" if ok else "E2E FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
