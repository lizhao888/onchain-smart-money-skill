"""DEX Screener provider（免 key）。

用途：给定 token 找出「流动性最大的交易对」= 我们要盯的池子。
实测端点：GET /latest/dex/tokens/{ca}（见 DATA_SOURCES.md §3）
注意：官方 reference 未成文该端点，限额未明文 → 调用方需保守节流。
"""
from __future__ import annotations

import re
from typing import Optional, Tuple

from ..http import Http, Response

BASE = "https://api.dexscreener.com"


def best_pool(http: Http, token: str, chain_id: str = "ethereum",
              require_active: bool = True) -> Tuple[Optional[dict], Response]:
    """选「流动性最大的**活跃**交易对」。

    实测教训（2026-10-19 BSC/CAKE）：流动性最大的 pair 可能是近期几乎无 swap 的池子，
    而流动性排第 5 的 V2 池 24h 有 2000+ 笔交易。「找早期买家」要的是活跃池 ——
    默认按 流动性 × log(1+24h交易数) 排序；require_active=False 恢复纯流动性排序。
    """
    import math
    r = http.get_json(f"{BASE}/latest/dex/tokens/{token}")
    if not r.ok:
        return None, r
    payload = r.payload or {}
    if "pairs" not in payload:
        return None, Response("FAILED", None, r.http_status, "SCHEMA_MISMATCH", r.url, r.attempts)
    pairs = [p for p in (payload.get("pairs") or []) if p.get("chainId") == chain_id]
    if not pairs:
        return None, Response("EMPTY_CONFIRMED", None, r.http_status, None, r.url, r.attempts)

    def _h24txn(p):
        h = (p.get("txns") or {}).get("h24") or {}
        return int(h.get("buys") or 0) + int(h.get("sells") or 0)

    def _score(p):
        liq = _f((p.get("liquidity") or {}).get("usd"))
        if not require_active:
            return liq
        # ① 「池子→EOA Transfer」判定只对 V2 型 AMM 成立 —— V3/集中流动性池的
        #    swap 结算方式不同（实测 CAKE V3 大池 5000 块仅 2 笔 from=pool Transfer，
        #    而同 token 的 V2 池有 34+ 笔）。有 v2 标签的优先。
        # ② 同为 V2 时按流动性 × log(1+24h 交易数) 排序。
        labels = p.get("labels") or []
        v2_bonus = 1e9 if "v2" in labels else 0
        return v2_bonus + liq * math.log1p(_h24txn(p))

    pairs.sort(key=_score, reverse=True)
    p = pairs[0]
    addr = p.get("pairAddress") or ""
    # Uniswap V4 的 "pool" 是 64 位 poolId，不是地址 —— 不能当 topic1 用
    kind = "address" if re.fullmatch(r"0x[0-9a-fA-F]{40}", addr) else (
        "v4_pool_id" if re.fullmatch(r"0x[0-9a-fA-F]{64}", addr) else "unknown")
    return {
        "pool": addr,
        "pool_kind": kind,
        "dex": p.get("dexId"),
        "liquidity_usd": _f((p.get("liquidity") or {}).get("usd")),
        "volume_24h": _f((p.get("volume") or {}).get("h24")),
        "price_usd": p.get("priceUsd"),
        "mcap": _f(p.get("marketCap") or p.get("fdv")),
        "pairs_count": len(pairs),
        "symbol": ((p.get("baseToken") or {}).get("symbol")) or "",
        "h24_txns": _h24txn(p),
    }, r


def _f(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0
