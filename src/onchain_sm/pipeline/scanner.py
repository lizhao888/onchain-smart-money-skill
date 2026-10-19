"""发射台扫描调度器（库1 + 监视器）。

「找 6 个 BSC 优质 meme」的机制：不是挨个对接发射台 API，而是
  1) GeckoTerminal new_pools 多页拉满（覆盖 four.meme/PumpSwap 等毕业流），
  2) DexScreener search 多关键词兜底（覆盖 GT 未收录的池子），
  3) 按 launchpads.json 的发射台标签归因，
  4) 统一「优质度」过滤与打分 → 输出 Top N 候选。

优质度过滤（v0，全部可配）：
  流动性 ≥ $3K（防假池）≤ $2M（排除已毕业大盘）
  24h 真实交易 ≥ 20 笔（防僵尸池）
  池龄 ≤ 14 天（新 meme）
  排除结算币/主流币（USDT/WBNB/CAKE…）
打分 = log(流动性) × log1p(24h笔数) / log1p(池龄天)   （越新越活跃越高）
"""
from __future__ import annotations

import json
import math
import os
import time
from typing import Dict, List, Optional

from ..http import Http

GT = "https://api.geckoterminal.com/api/v2"
DS = "https://api.dexscreener.com"

# 主流/结算币排除表
EXCLUDE_TOKENS = {
    # BSC
    "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c": "WBNB",
    "0x55d398326f99059ff775485246999027b3197955": "USDT",
    "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d": "USDC",
    "0x0e09fabb73bd3ade0a17ecc321fd13a19e81ce82": "CAKE",
    "0x2170ed0880ac9a755fd29b2688956bd959f933f8": "ETH",
    # SOL
    "so11111111111111111111111111111111111111112": "WSOL",
    "epjfwdd5aufqssqem2qn1xzybapc8g4weg4knaeytvo": "USDC",
}
EXCLUDE_SYMBOLS = {"USDT", "USDC", "WBNB", "WETH", "CAKE", "ETH", "BTCB", "SOL", "WSOL", "DAI", "FDUSD"}


def load_launchpads(path: Optional[str] = None) -> dict:
    # 库1固定放仓库根 data/launchpads.json（src 的上两级）
    path = path or os.path.join(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), "data", "launchpads.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _gt_fetch(http: Http, net: str, pages: int = 2) -> List[dict]:
    out = []
    for pg in range(1, pages + 1):
        r = http.get_json(f"{GT}/networks/{net}/new_pools?include=base_token,dex&page={pg}")
        if not r.ok or not r.payload:
            break
        data = r.payload.get("data") or []
        inc = {i.get("id"): i for i in (r.payload.get("included") or [])}
        for p in data:
            a = p.get("attributes") or {}
            rel = p.get("relationships") or {}
            bid = ((rel.get("base_token") or {}).get("data") or {}).get("id") or ""
            base_addr = bid.replace(f"{net}_", "", 1)
            ta = (inc.get(bid) or {}).get("attributes") or {}
            tx = (a.get("transactions") or {}).get("h24") or {}
            out.append({
                "addr": base_addr, "symbol": ta.get("symbol") or "?", "name": ta.get("name"),
                "liq": float(a.get("reserve_in_usd") or 0), "fdv": float(a.get("fdv_usd") or 0),
                "h24": int(tx.get("buys") or 0) + int(tx.get("sells") or 0),
                "buys24": int(tx.get("buys") or 0), "sells24": int(tx.get("sells") or 0),
                "dex": ((rel.get("dex") or {}).get("data") or {}).get("id", ""),
                "created": a.get("pool_created_at"),
                "src": "geckoterminal",
            })
        time.sleep(7)          # GT 限流 ~10/min，页间隔必须 ≥6s
    return out


def _ds_search(http: Http, net: str, keywords: List[str]) -> List[dict]:
    """DexScreener search 兜底：覆盖 GT 未收录的新池/发射台。"""
    chain_map = {"bsc": "bsc", "solana": "solana", "ethereum": "ethereum"}
    out = []
    for kw in keywords:
        r = http.get_json(f"{DS}/latest/dex/search?q={kw}")
        if not r.ok or not r.payload:
            continue
        for p in r.payload.get("pairs") or []:
            if p.get("chainId") != chain_map.get(net):
                continue
            base = p.get("baseToken") or {}
            created = p.get("pairCreatedAt") or 0
            age_days = (time.time() * 1000 - created) / 86400000 if created else 999
            out.append({
                "addr": base.get("address"), "symbol": base.get("symbol") or "?",
                "name": base.get("name"),
                "liq": float((p.get("liquidity") or {}).get("usd") or 0),
                "fdv": float(p.get("fdv") or 0),
                "h24": int(((p.get("txns") or {}).get("h24") or {}).get("buys") or 0)
                       + int(((p.get("txns") or {}).get("h24") or {}).get("sells") or 0),
                "buys24": int(((p.get("txns") or {}).get("h24") or {}).get("buys") or 0),
                "sells24": int(((p.get("txns") or {}).get("h24") or {}).get("sells") or 0),
                "dex": p.get("dexId") or "", "labels": p.get("labels") or [],
                "age_days": age_days, "src": "dexscreener",
            })
        time.sleep(0.4)
    return out


def attribute_launchpad(dex_tag: str, labels: List[str], lp: dict, net: str) -> str:
    """按 dex 标签 + 池子 labels 归因发射台（尽力而为，归不出就 unknown）。"""
    tag = (dex_tag or "").lower()
    for chain_lp in (lp.get("chains") or {}).values():
        for l in chain_lp.get("launchpads") or []:
            for al in l.get("aliases", []):
                if al.lower() in tag:
                    return l["name"]
    if "v2" in (labels or []):
        return "unknown(v2-graduated)"
    return "unknown"


def scan_new_memes(http: Http, chain: str = "bsc", top_n: int = 6,
                   min_liq: float = 3000, max_liq: float = 2_000_000,
                   min_tx24: int = 10, max_age_days: float = 14,
                   ds_keywords: Optional[List[str]] = None,
                   gt_pages: int = 5) -> dict:
    """主入口：全网发射台扫描 → 优质 meme 候选 Top N。"""
    lp = load_launchpads()
    # GT 新池（多页：页少会漏掉刚毕业的池子）
    cands: Dict[str, dict] = {}
    for c in _gt_fetch(http, chain, pages=gt_pages):
        if c.get("addr"):
            cands[c["addr"].lower()] = c
    # DS 兜底
    kws = ds_keywords or ["meme", "pepe", "dog", "cat", "moon", "elon"]
    for c in _ds_search(http, chain, kws):
        a = (c.get("addr") or "").lower()
        if not a:
            continue
        if a in cands:
            # 合并：DS 补充 age/labels
            cands[a]["age_days"] = c.get("age_days", 999)
            cands[a]["labels"] = c.get("labels") or []
            if c.get("h24", 0) > cands[a].get("h24", 0):
                cands[a]["h24"] = c["h24"]
        else:
            cands[a] = c

    # 过滤 + 归因 + 打分
    qualified = []
    seen_syms: Dict[str, int] = {}
    for addr, c in cands.items():
        sym = (c.get("symbol") or "?").upper()
        if addr in EXCLUDE_TOKENS or sym in EXCLUDE_SYMBOLS:
            continue
        liq, tx = c.get("liq") or 0, c.get("h24") or 0
        if not (min_liq <= liq <= max_liq and tx >= min_tx24):
            continue
        age = c.get("age_days")
        if age is None and c.get("created"):
            try:
                t = datetime_from_iso(c["created"])
                age = (time.time() - t) / 86400
            except Exception:
                age = None
        if age is not None and age > max_age_days:
            continue
        # 同名代币防重复（meme 抄袭名常见）：同名只保留流动性最大的
        if sym in seen_syms:
            prev = qualified_idx(seen_syms[sym], qualified) if False else None
        # 简化：同名记录计数，最后排序时同名靠后处理
        seen_syms[sym] = seen_syms.get(sym, 0) + 1
        score = (math.log1p(liq) * math.log1p(tx) /
                 math.log1p(max(age or 1, 0.5)))
        qualified.append({**c, "addr": addr, "age_days": round(age, 2) if age is not None else None,
                          "launchpad": attribute_launchpad(c.get("dex"), c.get("labels"), lp, chain),
                          "quality_score": round(score, 3)})
    qualified.sort(key=lambda x: x["quality_score"], reverse=True)
    # 同名去重：同名只保留第一个（分最高的）
    final, used_syms = [], set()
    for c in qualified:
        sym = (c.get("symbol") or "?").upper()
        if sym in used_syms:
            continue
        used_syms.add(sym)
        final.append(c)
        if len(final) >= top_n:
            break
    return {"chain": chain, "scanned": len(cands), "qualified": len(qualified),
            "picked": final, "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z")}


def datetime_from_iso(s: str) -> float:
    from datetime import datetime, timezone
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d.timestamp()


def qualified_idx(*a):  # 保留占位（历史兼容）
    return None
