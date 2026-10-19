"""幸存者画像（P2-5，对应原方法第 5 步——「看幸存钱包最近在干什么」）。

输入：N3 过滤后的 HUMAN 钱包列表（botfilter 已剔除机器人）
输出：每个钱包近 30 天的真实买入聚合 + 当前持仓

  recent_buys[]: {token, symbol?, first_buy_at, buy_count, est_amount_usd?, still_holding}

判定口径与 N2 一致：只统计「DEX 程序触达的买入」，空投/普通转账不计入
（防画像被撒出来的垃圾币污染——见 IMPLEMENTATION_PLAN R11）。
USD 估值尽力而为，拿不到就 null 并写 unknowns。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from ..http import Http
from ..providers import blockscout, dexscreener
from ..providers.base import TRANSFER_TOPIC

# 需要排除的「非买入来源」：零地址铸造、常见空投分发合约特征
ZERO_ADDR = "0x" + "0" * 40

# 主流结算币：swap 卖出时「池子→钱包」转的是这些，不是「买入」
# （例：钱包卖 SHIB 换 USDT，会收到 from=USDT 池的 USDT Transfer——不能算「买了 USDT」）
SETTLEMENT_TOKENS = {
    "0xdac17f958d2ee523a2206206994597c13d831ec7",   # USDT
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",   # USDC
    "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",   # WETH
    "0x6b175474e89094c44da98b95eedeac495271d0f",    # DAI
    "0x55d398326f99059ff775485246999027b3197955",   # BSC USDT
    "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d",   # BSC USDC
    "0x2170ed0880ac9a755fd29b2688956bd959f933f8",   # BSC WETH
    "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c",   # BSC WBNB
}


def _parse_ts(ts) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None


def wallet_recent_buys(http: Http, wallet: str, days: int = 30,
                       max_items: int = 200) -> Tuple[Optional[List[dict]], Optional[str]]:
    """EVM：拉钱包的 token-transfers，聚合出近 N 天的真实买入（来自合约的转入）。

    返回 (recent_buys, error)。error 非 None 表示取数失败（不是真空）。
    """
    tr = http.get_json(f"{blockscout.BASE}/api/v2/addresses/{wallet}/token-transfers?type=ERC-20")
    if not tr.ok:
        return None, f"token-transfers:{tr.error or tr.status}"
    items = (tr.payload or {}).get("items")
    if items is None:
        return None, "token-transfers:SCHEMA_MISMATCH"

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    # token → 聚合桶
    buckets: Dict[str, dict] = {}
    scanned = 0
    for it in items:
        scanned += 1
        if scanned > max_items:
            break
        dt = _parse_ts(it.get("timestamp"))
        if dt is None or dt < cutoff:
            continue
        to_hash = ((it.get("to") or {}).get("hash") or "").lower()
        if to_hash != wallet.lower():
            continue                                      # 只看转入
        frm = it.get("from") or {}
        # 买入判定：from 是合约（DEX 池/路由）。from 是 EOA = 普通转账/空投，不计。
        # 零地址铸造也不计（那是 mint，不是市场买入）。
        if not frm.get("is_contract") or (frm.get("hash") or "").lower() == ZERO_ADDR:
            continue
        tok = it.get("token") or {}
        taddr = (tok.get("address_hash") or "").lower()
        if not taddr:
            continue
        if taddr in SETTLEMENT_TOKENS:
            # 结算币：卖出换来的 USDT/WETH 等，不是「买入」
            continue
        b = buckets.setdefault(taddr, {
            "token": taddr, "symbol": tok.get("symbol") or "",
            "first_buy_at": dt.isoformat(), "last_buy_at": dt.isoformat(),
            "buy_count": 0})
        b["buy_count"] += 1
        if dt.isoformat() > b["last_buy_at"]:
            b["last_buy_at"] = dt.isoformat()
        if dt.isoformat() < b["first_buy_at"]:
            b["first_buy_at"] = dt.isoformat()

    return list(buckets.values()), None


def attach_holding(http: Http, wallet: str, buys: List[dict]) -> Tuple[List[dict], List[str]]:
    """给每条 recent_buy 标注「是否还拿着」（当前 token-balances）。"""
    unknowns: List[str] = []
    bal = http.get_json(f"{blockscout.BASE}/api/v2/addresses/{wallet}/token-balances")
    holdings: Dict[str, dict] = {}
    if bal.ok and isinstance(bal.payload, list):
        for t in bal.payload or []:
            ah = (t.get("token") or {}).get("address_hash") or ""
            holdings[ah.lower()] = t
    else:
        unknowns.append(f"持仓核验不可得:{bal.error or bal.status}（still_holding 全部标 UNKNOWN）")

    for b in buys:
        h = holdings.get(b["token"])
        if h is None:
            b["still_holding"] = False if not unknowns else "UNKNOWN"
        else:
            v = h.get("value")
            try:
                b["still_holding"] = (int(v) > 0) if v is not None else "UNKNOWN"
            except (TypeError, ValueError):
                b["still_holding"] = "UNKNOWN"
    return buys, unknowns


def attach_usd(http: Http, buys: List[dict], cache: Dict[str, dict]) -> Tuple[List[dict], List[str]]:
    """尽力估值：DEX Screener 单价 × 我们不知道买入数量 → 只给 token 当前市值参考。

    诚实声明：免费源拿不到「当时买入数量与价格」，est_amount_usd 无法可靠计算，
    统一标 None 并在报告 unknowns 说明。这里只附带 token 当前的 liq/mcap 供排序参考。
    """
    unknowns: List[str] = []
    for b in buys:
        b["est_amount_usd"] = None                    # 不可靠 → 不编造
        if b["token"] in cache:
            meta = cache[b["token"]]
        else:
            pool, r = dexscreener.best_pool(http, b["token"])
            meta = {"liq": (pool or {}).get("liquidity_usd"), "mcap": (pool or {}).get("mcap")} \
                if pool else {}
            if pool is None and r.status not in ("OK", "EMPTY_CONFIRMED"):
                unknowns.append(f"{b['token'][:10]} 行情不可得:{r.error or r.status}")
            cache[b["token"]] = meta
        b["token_liq_usd"] = meta.get("liq")
        b["token_mcap_usd"] = meta.get("mcap")
    return buys, unknowns


def portrait(http: Http, wallets: List[str], days: int = 30,
             max_wallets: int = 5) -> Tuple[List[dict], List[str], List[dict]]:
    """主入口：批量画像。返回 (portraits, unknowns, failed_wallets)。"""
    portraits: List[dict] = []
    unknowns: List[str] = []
    failed: List[dict] = []
    cache: Dict[str, dict] = {}
    for w in wallets[:max_wallets]:
        buys, err = wallet_recent_buys(http, w, days=days)
        if err:
            failed.append({"wallet": w, "error": err})
            continue
        buys, u1 = attach_holding(http, w, buys)
        buys, u2 = attach_usd(http, buys, cache)
        unknowns.extend(u1); unknowns.extend(u2)
        buys.sort(key=lambda x: x["first_buy_at"], reverse=True)
        portraits.append({"wallet": w, "recent_buys": buys,
                          "buy_token_count": len(buys)})
    return portraits, unknowns, failed
