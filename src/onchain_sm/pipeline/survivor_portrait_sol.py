
"""Solana 幸存者画像（P2-5 的 SOL 分支，复用余额对账法）。

成本模型：每钱包 getSignaturesForAddress(1) + 逐笔 getTransaction(N)。
公共 RPC 限流下约 2~4 分钟/钱包，单轮限 3 钱包（IMPLEMENTATION_PLAN R10）。
只统计「DEX 程序触达 + 余额净增」的买入，与 N2 同口径（防空投污染）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from ..http import Http
from ..providers.solanarpc import DEX_PROGRAMS, RPC_URL, _rpc


def wallet_recent_buys_sol(http: Http, wallet: str, days: int = 30,
                           max_txs: int = 40) -> Tuple[Optional[List[dict]], Optional[str]]:
    """钱包维度：拉其近期签名，逐笔对账所有 token 的净增（DEX 触达才算买入）。"""
    sigs, r = _rpc(http, "getSignaturesForAddress", [wallet, {"limit": max_txs}])
    if sigs is None:
        return None, f"getSignaturesForAddress:{r.error or r.status}"
    entries = [s for s in (sigs or []) if not s.get("err") and s.get("signature")]
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    buckets: Dict[str, dict] = {}
    scanned = 0
    for s in entries:
        if scanned >= max_txs:
            break
        tx_res, tr = _rpc(http, "getTransaction",
                          [s["signature"], {"encoding": "json", "maxSupportedTransactionVersion": 0}])
        if tx_res is None:
            continue
        tx = tx_res or {}
        meta = tx.get("meta") or {}
        if meta.get("err"):
            continue
        bt = tx.get("blockTime")
        if bt and datetime.fromtimestamp(bt, timezone.utc) < cutoff:
            continue
        keys = [k if isinstance(k, str) else (k.get("pubkey") or "")
                for k in ((tx.get("transaction") or {}).get("message") or {}).get("accountKeys") or []]
        meta_la = meta.get("loadedAddresses") or {}
        if isinstance(meta_la, dict):
            loaded = list(meta_la.get("writable") or []) + list(meta_la.get("readonly") or [])
        else:
            loaded = []
        is_swap = bool(set(keys) | set(loaded) & DEX_PROGRAMS) or                   bool((set(keys) | set(loaded)) & DEX_PROGRAMS)
        if not is_swap:
            continue
        scanned += 1
        pre = {b["accountIndex"]: b for b in (meta.get("preTokenBalances") or [])}
        post = {b["accountIndex"]: b for b in (meta.get("postTokenBalances") or [])}
        for idx, pb in post.items():
            owner = pb.get("owner") or (keys[idx] if idx < len(keys) else "")
            if owner.lower() != wallet.lower():
                continue
            mint = pb.get("mint") or ""
            if not mint:
                continue
            before = ((pre.get(idx) or {}).get("uiTokenAmount") or {}).get("uiAmount") or 0
            after = (pb.get("uiTokenAmount") or {}).get("uiAmount") or 0
            if (after or 0) - (before or 0) <= 0:
                continue                                 # 净减/持平：不是买入
            iso = datetime.fromtimestamp(bt, timezone.utc).isoformat() if bt else None
            b = buckets.setdefault(mint, {"token": mint, "symbol": "",
                                          "first_buy_at": iso, "last_buy_at": iso, "buy_count": 0})
            b["buy_count"] += 1
            if iso and (b["first_buy_at"] is None or iso < b["first_buy_at"]):
                b["first_buy_at"] = iso
            if iso and (b["last_buy_at"] is None or iso > b["last_buy_at"]):
                b["last_buy_at"] = iso
    return list(buckets.values()), None
