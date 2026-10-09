#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Google Scholar 检索 · 一次性配置

整个 skill 只在「安装后」需要输入一次凭据，写进 config.json；
之后 gs_search.py 每次调用都直接读取，不再问你任何账号或密钥。

用法
----
  # 交互式（推荐，什么都不用记）
  python gs_setup.py

  # 直接给参数
  python gs_setup.py --provider serpapi --api-key <KEY>
  python gs_setup.py --provider direct  --proxy http://127.0.0.1:7890

  # 看看现在配了什么（Key 会打码）
  python gs_setup.py --show

  # 只做连通性校验，不改配置
  python gs_setup.py --test

  # 清空配置
  python gs_setup.py --reset
"""
from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gs_search import (  # noqa: E402
    CONFIG_PATH,
    DEFAULTS,
    SCHOLAR_ENDPOINT,
    http_get,
    load_config,
    serpapi_account,
)

BAR = "=" * 62


def _force_utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass


def mask(secret: str | None) -> str:
    s = (secret or "").strip()
    if not s:
        return "(未设置)"
    if len(s) <= 8:
        return s[0] + "*" * (len(s) - 1)
    return f"{s[:4]}{'*' * 8}{s[-4:]}  (共 {len(s)} 位)"


def save_config(cfg: dict[str, Any]) -> None:
    ordered = {k: cfg.get(k, DEFAULTS.get(k)) for k in DEFAULTS}
    for k, v in cfg.items():
        ordered.setdefault(k, v)
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(ordered, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(CONFIG_PATH, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    print(f"✅ 已写入配置：{CONFIG_PATH}")


def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        raw = input(f"{prompt}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        print("已取消，配置未改动。")
        raise SystemExit(130)
    return raw or default


def validate_serpapi(cfg: dict[str, Any], quiet: bool = False) -> bool:
    if not quiet:
        print("  正在用 SerpAPI 的 account 接口校验 Key（不消耗检索额度）...")
    info = serpapi_account(cfg)
    if not info.get("ok"):
        print(f"  ❌ 校验失败：{info.get('message')}")
        print("     常见原因：Key 抄漏字符 / Key 已停用 / 网络不通。")
        return False
    d = info["data"]
    plan = d.get("plan_name", d.get("plan_id", "—"))
    left = d.get("total_searches_left", d.get("plan_searches_left", "—"))
    used = d.get("this_month_usage", "—")
    print(f"  ✅ Key 有效  |  套餐：{plan}  |  本月剩余：{left} 次  |  本月已用：{used} 次")
    if d.get("account_email"):
        print(f"     绑定账户：{d['account_email']}")
    if isinstance(left, (int, float)) and left <= 5:
        print("     ⚠️ 免费额度快用完了（免费档 100 次/月）。")
    return True


def validate_direct(cfg: dict[str, Any]) -> bool:
    proxy = (cfg.get("proxy") or "").strip()
    print(f"  正在探测 scholar.google.com 连通性{('（经代理 ' + proxy + '）') if proxy else '（直连 / 系统代理）'}...")
    status, body = http_get(SCHOLAR_ENDPOINT, {"q": "tensor decomposition", "hl": "en"}, cfg=cfg, timeout=25)
    if status == 0:
        print(f"  ❌ 连不上：{body}")
        print("     该域名在国内直连不通。请挂上梯子，并把代理地址填进 proxy。")
        return False
    if status == 429:
        print("  ⚠️ 通了，但已被 Google 限流（429）。说明网络路径没问题，只是请求太频繁。")
        print("     等几分钟再试；或改用 serpapi 通道（更稳）。")
        return True
    if status != 200:
        print(f"  ❌ 返回 HTTP {status}。代理可能没生效，或出口 IP 被 Google 拦了。")
        return False

    low = body.lower()
    if "not a robot" in low or "unusual traffic" in low or "/sorry/" in low:
        print("  ⚠️ 通了，但 Google 直接给了人机验证页。换一个节点，或隔几分钟再试。")
        return True

    try:
        from gs_search import parse_scholar_html  # noqa: PLC0415

        n = len(parse_scholar_html(body))
    except Exception:  # noqa: BLE001
        n = 0

    if n > 0:
        print(f"  ✅ 通了，并成功解析出 {n} 条真实检索结果——解析器工作正常。")
        return True
    if "gs_res_ccl" in body or 'class="gs_r' in body:
        print("  ⚠️ 通了，页面是 Scholar 结果页，但这一屏没解析出条目（可能是零结果页）。")
        return True
    print("  ⚠️ 通了，但返回内容不像 Scholar 结果页（可能被中间网关改写）。建议换节点后重试。")
    return True


def show(cfg: dict[str, Any]) -> None:
    print(BAR)
    print(" 当前配置")
    print(BAR)
    print(f"  配置文件      : {CONFIG_PATH}")
    print(f"  存在          : {'是' if CONFIG_PATH.exists() else '否'}")
    print(f"  默认通道      : {cfg.get('provider')}")
    print(f"  serpapi_api_key: {mask(cfg.get('serpapi_api_key'))}")
    print(f"  proxy         : {cfg.get('proxy') or '(未设置，直连)'}")
    print(f"  语言          : {cfg.get('language')}")
    print(f"  默认返回条数  : {cfg.get('default_limit')}")
    print(f"  缓存有效期    : {cfg.get('cache_ttl_hours')} 小时")
    print(f"  最多翻页      : {cfg.get('max_pages')} 页（每页 20 条）")
    print(BAR)


def interactive(cfg: dict[str, Any]) -> dict[str, Any]:
    print(BAR)
    print(" Google Scholar 检索 · 一次性配置")
    print(BAR)
    print(" 凭据只在这里输入一次，写入本地 config.json，之后长期复用。")
    print()
    print(" 请选择检索通道：")
    print()
    print("   [1] serpapi   走 SerpAPI 的 Google Scholar 引擎")
    print("                 · 最稳定、不需要梯子、返回结构化数据")
    print("                 · 需要注册拿一个 API Key（免费 100 次/月）")
    print("                 · 注册地址：https://serpapi.com/users/sign_up")
    print()
    print("   [2] direct    直连 scholar.google.com 并解析页面")
    print("                 · 完全免费、不需要任何账号")
    print("                 · 必须能访问 google（也就是要有梯子）")
    print("                 · 短时间大量检索会被 Google 要求过人机验证")
    print()
    choice = ask(" 输入 1 或 2", "1")

    if choice.strip() == "2":
        cfg["provider"] = "direct"
        print()
        print("  direct 通道需要一个能访问 google 的代理地址。")
        print("  常见写法：http://127.0.0.1:7890（Clash）/ http://127.0.0.1:10809（v2rayN）")
        proxy = ask("  代理地址（留空表示直连试试）", cfg.get("proxy") or "")
        cfg["proxy"] = proxy.strip() or None
    else:
        cfg["provider"] = "serpapi"
        print()
        print("  请粘贴 SerpAPI Key（形如 64 位十六进制串）。")
        print("  在 https://serpapi.com/manage-api-key 页面可以看到。")
        print("  ⚠️ 请勿在这里填任何平台账号的登录密码——本 skill 不需要，也不该存。")
        key = ask("  API Key", cfg.get("serpapi_api_key") or "")
        cfg["serpapi_api_key"] = key.strip() or None
        print()
        print("  SerpAPI 是否需要走代理？（国内直连 serpapi.com 通常没问题，可直接回车跳过）")
        proxy = ask("  代理地址（可留空）", cfg.get("proxy") or "")
        cfg["proxy"] = proxy.strip() or None

    print()
    lang = ask("  结果语言 hl（en / zh-CN，默认 en）", cfg.get("language") or "en")
    cfg["language"] = lang.strip() or "en"

    limit = ask("  默认返回条数（建议 10）", str(cfg.get("default_limit") or 10))
    try:
        cfg["default_limit"] = max(1, int(limit))
    except ValueError:
        cfg["default_limit"] = 10

    ttl = ask("  缓存有效期小时数（同一检索式在此时间内不重复请求，填 0 关闭）", str(cfg.get("cache_ttl_hours") or 24))
    try:
        cfg["cache_ttl_hours"] = max(0, float(ttl))
    except ValueError:
        cfg["cache_ttl_hours"] = 24

    return cfg


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdout()
    p = argparse.ArgumentParser(
        prog="gs_setup.py",
        description="Google Scholar 检索的一次性配置（只做一次）",
    )
    p.add_argument("--provider", choices=["serpapi", "direct"], default=None, help="检索通道")
    p.add_argument("--api-key", default=None, help="SerpAPI Key（provider=serpapi 时）")
    p.add_argument("--proxy", default=None, help="代理地址，如 http://127.0.0.1:7890")
    p.add_argument("--language", default=None, help="结果语言 hl，默认 en")
    p.add_argument("--limit", type=int, default=None, help="默认返回条数")
    p.add_argument("--show", action="store_true", help="只显示当前配置（Key 打码）")
    p.add_argument("--test", action="store_true", help="只做连通性/凭据校验，不改配置")
    p.add_argument("--reset", action="store_true", help="删除 config.json")
    p.add_argument("--no-verify", action="store_true", help="跳过写入后的校验")
    args = p.parse_args(argv)

    cfg = load_config()

    if args.show:
        show(cfg)
        return 0

    if args.reset:
        if CONFIG_PATH.exists():
            CONFIG_PATH.unlink()
            print(f"已删除 {CONFIG_PATH}")
        else:
            print("本来就没有配置文件。")
        return 0

    if args.test:
        print(BAR)
        print(" 连通性 / 凭据校验")
        print(BAR)
        show(cfg)
        if (cfg.get("provider") or "serpapi") == "serpapi":
            return 0 if validate_serpapi(cfg) else 1
        return 0 if validate_direct(cfg) else 1

    non_interactive = any(
        [args.provider, args.api_key, args.proxy, args.language, args.limit is not None]
    )

    if non_interactive:
        if args.provider:
            cfg["provider"] = args.provider
        elif args.api_key:
            cfg["provider"] = "serpapi"
        elif args.proxy and not cfg.get("provider"):
            cfg["provider"] = "direct"
        if args.api_key is not None:
            cfg["serpapi_api_key"] = args.api_key.strip() or None
        if args.proxy is not None:
            cfg["proxy"] = args.proxy.strip() or None
        if args.language:
            cfg["language"] = args.language
        if args.limit is not None:
            cfg["default_limit"] = max(1, int(args.limit))
    else:
        if not sys.stdin.isatty():
            print("非交互环境。请带参数运行，例如：")
            print("  python gs_setup.py --provider serpapi --api-key <KEY>")
            print("  python gs_setup.py --provider direct --proxy http://127.0.0.1:7890")
            print()
            show(cfg)
            return 0
        cfg = interactive(cfg)
        print()

    save_config(cfg)

    if args.no_verify:
        return 0

    print()
    print(BAR)
    print(" 校验")
    print(BAR)
    ok = False
    provider = cfg.get("provider")
    if provider == "serpapi":
        if not (cfg.get("serpapi_api_key") or "").strip():
            print("  ⚠️ 未填写 API Key。请重新运行并填入，或改用 direct 通道。")
        else:
            ok = validate_serpapi(cfg)
    else:
        ok = validate_direct(cfg)

    print()
    print(BAR)
    if ok:
        print(" 配置完成。之后直接检索即可，不会再问你要凭据：")
        print()
        py = sys.executable
        print(f'  "{py}" "{Path(__file__).with_name("gs_search.py")}" "你的检索词" -n 10')
        print()
        print(f'  "{py}" "{Path(__file__).with_name("gs_search.py")}" --quota   # 查 SerpAPI 剩余额度')
    else:
        print(" 配置已保存，但上面的校验没通过——先按提示修好再用。")
        print(" 改完后可随时：python gs_setup.py --test 复检")
    print(BAR)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
