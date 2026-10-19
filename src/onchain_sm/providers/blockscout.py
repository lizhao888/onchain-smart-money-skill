"""Blockscout provider（Ethereum，免 key）。

已实测端点见 DATA_SOURCES.md §1。注意：
- 双 topic 过滤必须带 `topic0_1_opr=and`，否则返回 message 但不带错误码。
- 这里所有函数都返回 `(data, Response)`，调用方据 Response.status 判空/失败。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
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
    result = payload.get("result")
    message = payload.get("message")
    # Blockscout 兼容层的「真空」也是 HTTP 200：message="No logs found" + result=[]
    # 这是确认没有记录（EMPTY_CONFIRMED），不是取数失败 —— 不得伪装成 FAILED。
    if message != "OK":
        empty = (result is None or (isinstance(result, list) and len(result) == 0))
        if empty and message == "No logs found":
            return [], Response("EMPTY_CONFIRMED", None, r.http_status, None, r.url, r.attempts)
        return [], Response("FAILED", None, r.http_status,
                            f"UPSTREAM:{message or 'BLOCKSCOUT_ERROR'}", r.url, r.attempts)
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


def address_activity(http: Http, addr: str, days: int = 30) -> Tuple[Optional[dict], Response]:
    """近 N 天活动：swap 用原生交易 method 判定；合约→EOA 的转账只记 contract_transfers。

    注意：
    - 窗口按 timestamp 过滤，30 天前的记录不计入（修复「跨年计数」缺陷）。
    - token-transfers 首页默认 50 条，若最老一条已超出窗口，说明可能没扫全，
      记 window_truncated=true，调用方据此降置信度，不假装扫全。
    - 「from 是合约」的代币转账可能是 swap、空投、撤池 —— 一律不算 swap，
      swap 计数只来自原生交易 method（swap*/聚合器路由），拿不到就是 None。
    """
    tr = http.get_json(f"{BASE}/api/v2/addresses/{addr}/token-transfers?type=ERC-20")
    if not tr.ok:
        return None, tr
    items = ((tr.payload or {}).get("items"))
    if items is None:
        return None, Response("FAILED", None, tr.http_status, "SCHEMA_MISMATCH", tr.url, tr.attempts)

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    swaps, transfers, contract_transfers, tokens = 0, 0, 0, set()
    last_ts, oldest_seen, truncated = None, None, False
    parsed = 0
    for it in items:
        ts = it.get("timestamp")
        dt = _parse_ts(ts)
        if dt is not None:
            parsed += 1
            if oldest_seen is None or dt < oldest_seen:
                oldest_seen = dt
            if dt < cutoff:
                continue                              # 窗口外：不计入任何活动指标
        elif ts:
            # 有时间戳但解析失败：保守起见不计入（UNKNOWN 语义由上层处理）
            continue
        to_addr = ((it.get("to") or {}).get("hash") or "").lower()
        tok = (it.get("token") or {})
        if to_addr == addr.lower():
            tokens.add((tok.get("address_hash") or "").lower())
        if (it.get("from") or {}).get("is_contract"):
            contract_transfers += 1                   # 不再冒充 swap
        else:
            transfers += 1
        if dt is not None and (last_ts is None or dt.isoformat() > last_ts):
            last_ts = dt.isoformat()

    if oldest_seen is not None and oldest_seen < cutoff and parsed:
        truncated = True                              # 首页最老记录早于窗口 → 可能没扫全

    # swap 计数：原生交易的 method（swap 相关即计），同样按时间窗过滤。
    # 注意：该端点不支持 filter 参数（422），直接查全部；接口失败时 swap=None（未知）。
    swap_30d: Optional[int] = None
    txr = http.get_json(f"{BASE}/api/v2/addresses/{addr}/transactions")
    if txr.ok:
        tx_items = ((txr.payload or {}).get("items")) or []
        swap_30d = 0
        tx_truncated = False
        tx_times: List[int] = []
        tx_blocks: List[int] = []
        for tx in tx_items:
            dt = _parse_ts(tx.get("timestamp"))
            if dt is not None and dt < cutoff:
                tx_truncated = True
                continue
            method = (tx.get("method") or "").lower()
            if "swap" in method:
                swap_30d += 1
            if dt is not None:
                tx_times.append(int(dt.timestamp()))
            bn = tx.get("block_number")
            if bn is not None:
                try:
                    tx_blocks.append(int(bn))
                except (TypeError, ValueError):
                    pass
        if tx_truncated:
            truncated = True
    else:
        tx_times, tx_blocks = [], []

    return {"swap_activity": swap_30d, "transfer_activity": transfers,
            "contract_transfers": contract_transfers,
            "window_truncated": truncated,
            "tx_times": tx_times, "tx_blocks": tx_blocks,
            "last_active": last_ts, "tokens_received": sorted(t for t in tokens if t)}, tr


def _parse_ts(ts) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None


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
