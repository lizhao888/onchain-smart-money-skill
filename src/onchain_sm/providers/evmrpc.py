"""EVM 公共 RPC provider（BSC 等 EVM 链，免 key）。

Blockscout 没有官方 BSC 实例（DATA_SOURCES.md §6 已确认 404），
BSC 的 getLogs 走公共 RPC（bsc.publicnode.com 实测可用）。
注意：公共 RPC 有 archive 限制 —— 大范围历史区间会 4xx，
调用方必须把这种失败如实标 BLOCKED/UNKNOWN，不许冒充空结果。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ..http import Http, Response
from .base import TRANSFER_TOPIC, decode_transfer, pad_topic

# 实测（2026-10-19）：bsc.publicnode.com 窄区间 getLogs OK；全史区间报
# "Archive requests require a personal token" → 历史回溯在 BSC 上是硬限制。
RPC_URLS = {
    "bsc": ["https://bsc.publicnode.com", "https://bsc-dataseed.bnbchain.org"],
}

GOPLUS_CHAIN_ID = {"ethereum": 1, "bsc": 56}


def _rpc_call(http: Http, url: str, method: str, params: list) -> Tuple[Optional[Any], Response]:
    """公共 RPC JSON-RPC 调用（POST）。复用 Http 的重试/节流。"""
    import json as _json
    body = _json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})

    def fetch(url_: str):
        req = urllib.request.Request(
            url_, data=body.encode(),
            headers={"Content-Type": "application/json",
                     "User-Agent": "onchain-smart-money/0.1",
                     "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")[:2000]

    inner = Http(fetch=fetch, min_interval=http.min_interval,
                 retries=http.retries, backoff=http.backoff, timeout=http.timeout)
    r = inner.get_json(url)
    if not r.ok:
        return None, r
    payload = r.payload or {}
    if "error" in payload and payload.get("error"):
        err = payload["error"]
        msg = f"RPC:{err.get('code')} {err.get('message')}"
        # archive 限制不是「没有记录」，是查不了 → FAILED（调用方降级，不当空结果）
        if "archive" in str(err.get("message", "")).lower():
            msg = "RPC:ARCHIVE_REQUIRED"
        return None, Response("FAILED", None, 200, msg, url, r.attempts)
    return payload.get("result"), Response("OK", payload, 200, None, url, r.attempts)


import urllib.error  # noqa: E402  (在 _rpc_call 之后导入也可以，函数运行时才用)
import urllib.request  # noqa: E402


def _first_ok_rpc(http: Http, chain: str, method: str, params: list) -> Tuple[Optional[Any], Response]:
    last: Optional[Response] = None
    for url in RPC_URLS.get(chain, []):
        result, r = _rpc_call(http, url, method, params)
        if r.status == "OK" or (r.error and "ARCHIVE" in r.error):
            return result, r          # archive 错误不换端点（都一样），直接上抛
        last = r
    return None, last or Response("FAILED", None, 0, "RPC:NO_ENDPOINT", "", 1)


def latest_block(http: Http, chain: str = "bsc") -> Tuple[Optional[int], Response]:
    result, r = _first_ok_rpc(http, chain, "eth_blockNumber", [])
    if result is None:
        return None, r
    try:
        return int(result, 16), r
    except (TypeError, ValueError):
        return None, Response("FAILED", None, 200, "SCHEMA_MISMATCH", r.url, r.attempts)


def pool_buys_rpc(http: Http, token: str, pool: str, from_block: int, to_block: int,
                  chain: str = "bsc") -> Tuple[List[dict], Response]:
    """池子 → 用户的 Transfer（= 真实买入），走公共 RPC getLogs。"""
    url = RPC_URLS.get(chain, [None])[0]
    if not url:
        return [], Response("FAILED", None, 0, f"RPC:UNSUPPORTED_CHAIN {chain}", "", 1)
    params = [{
        "fromBlock": hex(from_block), "toBlock": hex(to_block),
        "address": token,
        "topics": [TRANSFER_TOPIC, pad_topic(pool)],
    }]
    logs, r = _first_ok_rpc(http, chain, "eth_getLogs", params)
    if logs is None:
        return [], r
    rows = [d for d in (decode_transfer(x) for x in logs) if d]
    if not rows:
        return [], Response("EMPTY_CONFIRMED", None, 200, None, r.url, r.attempts)
    return rows, r


def address_info_rpc(http: Http, addr: str, chain: str = "bsc") -> Tuple[Optional[dict], Response]:
    """is_contract 判定：eth_getCode 非空即合约。"""
    code, r = _first_ok_rpc(http, chain, "eth_getCode", [addr, "latest"])
    if code is None:
        return None, r
    return {"is_contract": code not in ("0x", "", None), "creation_tx": None}, r
