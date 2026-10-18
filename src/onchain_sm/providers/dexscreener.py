"""DEX Screener provider（免 key）。

用途：给定 token 找出「流动性最大的交易对」= 我们要盯的池子。
实测端点：GET /latest/dex/tokens/{ca}（见 DATA_SOURCES.md §3）
注意：官方 reference 未成文该端点，限额未明文 → 调用方需保守节流。
"""
from __future__ import annotations

from typing import Optional, Tuple

from ..http import Http, Response

BASE = "https://api.dexscreener.com"


def best_pool(http: Http, token: str, chain_id: str = "ethereum") -> Tuple[Optional[dict], Response]:
    r = http.get_json(f"{BASE}/latest/dex/tokens/{token}")
    if not r.ok:
        return None, r
    payload = r.payload or {}
    if "pairs" not in payload:
        return None, Response("FAILED", None, r.http_status, "SCHEMA_MISMATCH", r.url, r.attempts)
    pairs = [p for p in (payload.get("pairs") or []) if p.get("chainId") == chain_id]
    if not pairs:
        return None, Response("EMPTY_CONFIRMED", None, r.http_status, None, r.url, r.attempts)
    pairs.sort(key=lambda p: _f((p.get("liquidity") or {}).get("usd")), reverse=True)
    p = pairs[0]
    return {
        "pool": p.get("pairAddress"),
        "dex": p.get("dexId"),
        "liquidity_usd": _f((p.get("liquidity") or {}).get("usd")),
        "volume_24h": _f((p.get("volume") or {}).get("h24")),
        "price_usd": p.get("priceUsd"),
        "mcap": _f(p.get("marketCap") or p.get("fdv")),
        "pairs_count": len(pairs),
    }, r


def _f(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0
