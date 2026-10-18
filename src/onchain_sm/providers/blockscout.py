"""Blockscout provider（Ethereum，免 key）。

已实测端点见 DATA_SOURCES.md §1。注意：
- 双 topic 过滤必须带 `topic0_1_opr=and`，否则返回 message 但不带错误码。
- 这里所有函数都返回 `(data, Response)`，调用方据 Response.status 判空/失败。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ..http import Http, Response
from .base import TRANSFER_TOPIC, decode_transfer, pad_topic

BASE = "https://eth.blockscout.com"


def token_meta(http: Http, token: str) -> Tuple[Optional[dict], Response]:
    r = http.get_json(f"{BASE}/api/v2/tokens/{token}")
    if not r.ok:
        return None, r
    d = r.payload or {}
    if "symbol" not in d:
        return None, Response("FAILED", None, r.http_status, "SCHEMA_MISMATCH", r.url, r.attempts)
    return {
        "symbol": d.get("symbol") or "",
        "name": d.get("name") or "",
        "mcap": _f(d.get("circulating_market_cap")),
        "holders": _i(d.get("holders_count")),
        "decimals": d.get("decimals"),
    }, r


def pool_buys(http: Http, token: str, pool: str, from_block: int, to_block: int,
              limit_pages: int = 2) -> Tuple[List[dict], Response]:
    """池子 → 用户的 Transfer（= 真实买入）。只取前 `limit_pages` 个窗口。"""
    url = (f"{BASE}/api?module=logs&action=getLogs"
           f"&fromBlock={from_block}&toBlock={to_block}"
           f"&address={token}&topic0={TRANSFER_TOPIC}"
           f"&topic1={pad_topic(pool)}&topic0_1_opr=and")
    r = http.get_json(url)
    if not r.ok:
        return [], r
    payload = r.payload or {}
    # Blockscout 的参数错误也是 HTTP 200：必须查 message
    if payload.get("message") != "OK":
        return [], Response("FAILED", None, r.http_status,
                            payload.get("message") or "BLOCKSCOUT_ERROR", r.url, r.attempts)
    logs = payload.get("result") or []
    rows = [d for d in (decode_transfer(x) for x in logs) if d]
    if not rows:
        return [], Response("EMPTY_CONFIRMED", None, r.http_status, None, r.url, r.attempts)
    return rows, r


def address_info(http: Http, addr: str) -> Tuple[Optional[dict], Response]:
    r = http.get_json(f"{BASE}/api/v2/addresses/{addr}")
    if not r.ok:
        return None, r
    d = r.payload or {}
    if "hash" not in d:
        return None, Response("FAILED", None, r.http_status, "SCHEMA_MISMATCH", r.url, r.attempts)
    return {"is_contract": bool(d.get("is_contract")),
            "creation_tx": d.get("creation_transaction_hash")}, r


def creation_block(http: Http, token: str) -> Tuple[Optional[int], Response]:
    """代币部署区块：v2 address → creation_transaction_hash → v2 tx → block_number。"""
    info, r = address_info(http, token)
    if not info or not info.get("creation_tx"):
        return None, r
    r2 = http.get_json(f"{BASE}/api/v2/transactions/{info['creation_tx']}")
    if not r2.ok:
        return None, r2
    bn = (r2.payload or {}).get("block_number")
    if bn is None:
        return None, Response("FAILED", None, r2.http_status, "SCHEMA_MISMATCH", r2.url, r2.attempts)
    return int(bn), r2


def address_activity(http: Http, addr: str) -> Tuple[Optional[dict], Response]:
    """近 30 天活动：代币转账（按 from 是否合约区分 swap / transfer）+ 原生交易时间。"""
    tr = http.get_json(f"{BASE}/api/v2/addresses/{addr}/token-transfers?type=ERC-20")
    if not tr.ok:
        return None, tr
    items = ((tr.payload or {}).get("items"))
    if items is None:
        return None, Response("FAILED", None, tr.http_status, "SCHEMA_MISMATCH", tr.url, tr.attempts)

    swaps, transfers, last_ts, tokens = 0, 0, None, set()
    for it in items:
        frm = (it.get("from") or {})
        tok = (it.get("token") or {})
        to_addr = ((it.get("to") or {}).get("hash") or "").lower()
        if to_addr == addr.lower():
            tokens.add((tok.get("address_hash") or "").lower())
        if frm.get("is_contract"):
            swaps += 1
        else:
            transfers += 1
        ts = it.get("timestamp")
        if ts and (last_ts is None or ts > last_ts):
            last_ts = ts
    return {"swap_activity": swaps, "transfer_activity": transfers,
            "last_active": last_ts, "tokens_received": sorted(t for t in tokens if t)}, tr


def _f(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _i(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0
