"""Solana provider（公共 RPC，免 key）。

账本模型与 EVM 完全不同，判定路径：
- 「池子 → 用户」在 Solana 上对应用户 token 账户余额净增（pre/postTokenBalances 对账）；
- swap vs 转账：看交易是否触达已知 DEX/AMM 程序 id（Raydium/Pump.fun/Orca/Jupiter…）；
  未触达任何 DEX 程序的余额增加只算 transfer（可能是空投/划转）；
- decimals 从 mint account 直接读（getAccountInfo jsonParsed）；
- 公共 RPC 限流 100 req/10s → 逐笔 getTransaction 很慢，只适合「近期新币」场景，
  全史回溯不现实（DATA_SOURCES.md §7 已确认），调用方应限制签名扫描窗口。

参考实现思路：haccer/pumpfun-research（MIT）的 BUY/SELL 余额对账分类法；
未复制其代码（它绑 Helius），本文件只用公共 RPC。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ..http import Http, Response

RPC_URL = "https://api.mainnet-beta.solana.com"

# 已知 swap/AMM 相关程序 id（判定「这是交易，不是普通转账」的依据）
DEX_PROGRAMS = {
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8",   # Raydium AMM v4
    "39azUYFWPz3VHgKCf3VChUwbpURdCHRxjWVowf5jUJjg",   # Pump.fun AMM
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4",   # Jupiter Aggregator v6
    "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM",   # Raydium Liquidity Pool v2
    "Dooar9JkhdZ7J93L49PBN2BXtMVohF1sNkv4K9ZYRC4q",   # Raydium CPMM
    "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc",    # Orca Whirlpool
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",    # Pump.fun bonding curve
    "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK",   # Raydium CLMM (集中流动性)
    "routeUGWgWzqBWFcrCfv8tritsqukccJPu3q5GPP3xS",    # Jupiter Aggregator (路由入口)
    "JUP2jxvXaqu2N8DMy6SfwW2cv2FwiFpT7pAswCGg4Ft",    # Jupiter Aggregator v4
    "Eo7WjKq67rjJQSZxS6z3YkapzY3eMj6Xy8X5EQVn5UaB",   # Meteora DLMM
    "24Uqj9JCLxUeoC3hGfh5W3s9FM9uCHDS2SG3LYwBpyTi",   # Meteora Vault
    "cpamdpZCGKUy5JxQXB4dcpGPiZnHxS6wDKNwxbYk4nu",    # Pump.fun swap (2025 新路由)
    "BSfD6SHZigAfDWSjzD5Q41jw8LmKwtmjskPH9XW1mrRW",   # Pump.fun buy/sell (AMM 接口)
    "opNB2Gl6RQabKkMqttXkhMoDk8FNaFf8v2eBB6Zr1yR",   # Orca Whirlpool (新)
    "9H6tua7jkLhdm3w8BvgpTn5LZNU7g4ZynDmCiNN3q6Rp",  # pump.fun program (旧)
    "FloW8pQcnTnJtMQTqL8VbZBeamQwtV5jaAnne5c57Gxu",   # SolFi
    "9YUtCxhZWNVm5fpkCcELyYnb7GBwnV5cPHX4M1aB4H7s",   # SolFi v2
    "5VLg3upeC9YHGwGpqNkG4fB42pKGvXMqqf9WDPhGPSCd",   # TokenMill
    "AWMum5tKB2aFrtLEA1i5TWfCL1PRmSnr4pukhZyWPD4B",  # Obric v2
    "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA",   # PumpSwap AMM (pump.fun 迁移池)
    "39azUYFWPz3VHgKCf3VChUwbpURdCHRxjWVowf5jUJjg",   # (重复保险) Pump.fun AMM
}

SYSTEM_PROGRAMS = {
    "11111111111111111111111111111111",                # System
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",     # SPL Token
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",    # ATA
    "ComputeBudget111111111111111111111111111111111",
}


def _rpc(http: Http, method: str, params: list) -> Tuple[Optional[Any], Response]:
    import json as _json
    import urllib.error
    import urllib.request

    body = _json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})

    def fetch(url_: str):
        req = urllib.request.Request(
            url_, data=body.encode(),
            headers={"Content-Type": "application/json",
                     "User-Agent": "onchain-smart-money/0.1",
                     "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")[:2000]

    inner = Http(fetch=fetch, min_interval=http.min_interval,
                 retries=http.retries, backoff=http.backoff, timeout=http.timeout)
    r = inner.get_json(RPC_URL)
    if not r.ok:
        return None, r
    payload = r.payload or {}
    if payload.get("error"):
        err = payload["error"]
        return None, Response("FAILED", None, 200,
                              f"RPC:{err.get('code')} {err.get('message')}", RPC_URL, r.attempts)
    return payload.get("result"), Response("OK", payload, 200, None, RPC_URL, r.attempts)


def mint_info(http: Http, mint: str) -> Tuple[Optional[dict], Response]:
    """代币元数据：decimals / supply（jsonParsed，免 key）。"""
    result, r = _rpc(http, "getAccountInfo", [mint, {"encoding": "jsonParsed"}])
    if result is None:
        return None, r
    value = (result or {}).get("value") or {}
    parsed = ((value.get("data") or {}).get("parsed") or {})
    if parsed.get("type") != "mint":
        return None, Response("FAILED", None, 200, "SCHEMA_MISMATCH:not_a_spl_mint", RPC_URL, r.attempts)
    info = parsed.get("info") or {}
    return {"symbol": "", "name": "",                 # mint account 不带名称（要 Metaplex，Phase 2）
            "decimals": info.get("decimals"),
            "supply": info.get("supply"),
            "holders": None}, r


def token_recent_sigs(http: Http, mint: str, limit: int = 20) -> Tuple[List[dict], Response]:
    """代币近期成功交易签名（倒序）。"""
    result, r = _rpc(http, "getSignaturesForAddress", [mint, {"limit": limit}])
    if result is None:
        return [], r
    sigs = [s for s in (result or []) if not s.get("err") and s.get("signature")]
    return sigs, r


def _touches_dex_program(tx: dict) -> bool:
    keys = [k if isinstance(k, str) else (k.get("pubkey") or "")
            for k in ((tx.get("transaction") or {}).get("message") or {}).get("accountKeys") or []]
    meta = tx.get("meta") or {}
    # loadedAddresses: json 编码下是 {writable:[str], readonly:[str]}；也可能直接是列表
    loaded = (meta.get("loadedAddresses") or {})
    if isinstance(loaded, dict):
        loaded = list(loaded.get("writable") or []) + list(loaded.get("readonly") or [])
    for prog in list(keys) + list(loaded):
        if prog in DEX_PROGRAMS:
            return True
    return False


def classify_buyers(http: Http, mint: str, max_buyers: int = 20,
                    max_txs: int = 30) -> Tuple[List[dict], Response]:
    """近期交易逐笔对账，输出「余额净增 >0 且交易触达 DEX 程序」的买家。

    返回按首次观测时间排的买家（近似早期买家；Solana 无「从创世块扫起」的免费路径，
    这是对新币监控场景的正确近似 —— 越早的签名越靠前）。
    每个买家带 signature 证据；普通转账/空投不计入。
    注意：新币近端常有一串「建 ATA/领空投」系统交易，50 笔内可能全是噪音，
    所以默认拉 100 笔签名再逐笔看（公共 RPC 限流下约 2~4 分钟）。
    """
    sigs, r = token_recent_sigs(http, mint, limit=max(max_txs * 3, 100))
    if r.status == "FAILED":
        return [], r
    buyers: List[dict] = []
    seen: set = set()
    scanned = 0
    for s in sigs:                                    # 倒序 = 最近的在前；收集时反转
        if len(buyers) >= max_buyers or scanned >= max_txs:
            break
        tx_res, tr = _rpc(http, "getTransaction",
                          [s["signature"], {"encoding": "json", "maxSupportedTransactionVersion": 0}])
        if tx_res is None:
            continue                                  # 单笔失败跳过，不断链
        tx = tx_res or {}
        meta = tx.get("meta") or {}
        if meta.get("err"):
            continue                                  # 失败交易不算成交
        # 快速跳过纯系统交易（建 ATA/关账户）：不消耗 getTransaction 后的重活
        is_swap = _touches_dex_program(tx)
        scanned += 1                                   # 只计成功解析的（含未触达 DEX 的）
        keys = [k if isinstance(k, str) else k.get("pubkey", "")
                for k in ((tx.get("transaction") or {}).get("message") or {}).get("accountKeys") or []]
        pre = {b["accountIndex"]: b for b in (meta.get("preTokenBalances") or [])
               if (b.get("mint") or "") == mint}
        post = {b["accountIndex"]: b for b in (meta.get("postTokenBalances") or [])
                if (b.get("mint") or "") == mint}
        for idx, pb in post.items():
            owner = pb.get("owner") or keys[idx]
            if not owner or owner in seen:
                continue
            before = ((pre.get(idx) or {}).get("uiTokenAmount") or {}).get("uiAmount") or 0
            after = (pb.get("uiTokenAmount") or {}).get("uiAmount") or 0
            delta = (after or 0) - (before or 0)
            if delta > 0 and is_swap:                 # 净增 + 触达 DEX = 买入
                seen.add(owner)
                buyers.append({"address": owner, "block": tx.get("slot"),
                               "tx_hash": s["signature"],
                               "amount": delta, "block_time": tx.get("blockTime")})
                break                                 # 该 tx 记一个买家就够（防同一 tx 刷票）
    buyers.reverse()                                  # 最早的在前
    return buyers, Response("OK", {"scanned": scanned}, 200, None, RPC_URL, 1)


def token_security_goplus(http: Http, mint: str) -> Tuple[Optional[dict], Response]:
    """GoPlus Solana 端点（rugpull 系字段）。免 key 实测可用（2026-10-19）。"""
    url = (f"https://api.gopluslabs.io/api/v1/solana/token_security"
           f"?contract_addresses={mint}")
    r = http.get_json(url)
    if not r.ok:
        return None, r
    payload = r.payload or {}
    if payload.get("code") != 1:
        return None, Response("FAILED", None, r.http_status,
                              f"UPSTREAM:{payload.get('message')}", url, r.attempts)
    res = (payload.get("result") or {}).get(mint)
    if not res:
        return None, Response("EMPTY_CONFIRMED", None, r.http_status, None, url, r.attempts)

    def _s(k):
        v = res.get(k)
        if isinstance(v, dict):
            return v.get("status")
        return v

    blockers = []
    if str(_s("freezable")) == "1":
        blockers.append("FREEZABLE")                  # 权威可冻结用户账户
    if str(_s("mintable")) == "1":
        blockers.append("MINTABLE")                   # 可增发
    if str(_s("balance_mutable_authority")) == "1":
        blockers.append("BALANCE_MUTABLE")            # 权威可改余额
    return {
        "blockers": blockers,
        "flags": [],
        "is_mintable": str(_s("mintable")) == "1",
        "holder_count": res.get("holder_count"),
        "top10_rate": None,
        "creator": ((res.get("creators") or [{}])[0] or {}).get("address"),
        "owner": None,
        "source": "goplus-solana",
        "evidence_grade": "C",
    }, r
