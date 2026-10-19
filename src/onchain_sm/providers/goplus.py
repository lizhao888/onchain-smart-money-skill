"""GoPlus provider（免 key 实测可用）。

注意：返回的是**第三方标签**（C 级证据），只能作为风险闸门，不能当链上实证。
实测端点：GET /api/v1/token_security/1?contract_addresses={ca}
"""
from __future__ import annotations

from typing import Optional, Tuple

from ..http import Http, Response

BASE = "https://api.gopluslabs.io/api/v1"
TAX_BLOCK = 0.10          # 买卖税率 ≥10% 视为高风险（阈值可配置）


def token_security(http: Http, token: str, chain_id: int = 1) -> Tuple[Optional[dict], Response]:
    r = http.get_json(f"{BASE}/token_security/{chain_id}?contract_addresses={token.lower()}")
    if not r.ok:
        return None, r
    payload = r.payload or {}
    if payload.get("code") != 1:
        return None, Response("FAILED", None, r.http_status,
                              payload.get("message") or "GOPLUS_ERROR", r.url, r.attempts)
    res = (payload.get("result") or {}).get(token.lower())
    if not res:
        return None, Response("EMPTY_CONFIRMED", None, r.http_status, None, r.url, r.attempts)

    blockers = []
    if _b(res.get("cannot_sell_all")) or _b(res.get("is_honeypot")):
        blockers.append("CANNOT_SELL")
    flags = []
    if _b(res.get("is_blacklisted")) or _b(res.get("is_blacklist")):
        # GoPlus 语义：合约含黑名单功能（可冻结用户），不等于该代币已被拉黑
        flags.append("BLACKLIST_FUNCTION")
    if _f(res.get("buy_tax")) >= TAX_BLOCK or _f(res.get("sell_tax")) >= TAX_BLOCK:
        blockers.append("HIGH_TAX")
    if _b(res.get("can_take_back_ownership")):
        blockers.append("OWNERSHIP_TAKEOVER")

    return {
        "blockers": blockers,
        "flags": flags,
        "buy_tax": _f(res.get("buy_tax")),
        "sell_tax": _f(res.get("sell_tax")),
        "is_honeypot": _b(res.get("is_honeypot")),
        "is_mintable": _b(res.get("is_mintable")),
        "owner": res.get("owner_address"),
        "creator": res.get("creator_address"),
        "holder_count": _i(res.get("holder_count")),
        "top10_rate": _f(res.get("top_10_holder_rate") or res.get("holders")),
        "source": "goplus",
        "evidence_grade": "C",
    }, r


def _b(v) -> bool:
    return str(v).lower() in ("1", "true", "yes")


def _f(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _i(v) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0
