"""MVP-S 管线：5 个节点，只读，多链（ethereum / bsc / solana）。

设计要点（对应 MVP_ACCEPTANCE.md）：
- 每个节点独立判状态；失败向上传播为 BLOCKED/INCOMPLETE，绝不降级成 0。
- 买家只认「池子 → EOA」的 Transfer（EVM）或「DEX 交易中的余额净增」（Solana）。
- 活跃度区分 swap / transfer；数据拿不到标 UNKNOWN，不默认休眠。
- 风险闸门失败时，总分不覆盖阻断。
- 链适配：EVM 走 Blockscout(ETH)/公共 RPC(BSC)；Solana 走独立 RPC 适配器。
  不支持的链在 cli 层就拒绝，不会把 A 链数据冒充 B 链结论。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..http import Http
from ..models import (BLOCKED, FAILED, PASSED, WARNING, NodeResult, Report)
from ..providers import blockscout, dexscreener, evmrpc, goplus, solanarpc
from ..providers.base import dedupe_by_tx

ACTIVE_DAYS = 30
SWAP_ACTIVE_MIN = 3
RULES_VERSION = "v0.1"

SUPPORTED_CHAINS = ("ethereum", "bsc", "solana")


def run_scan(http: Http, token: str, chain: str = "ethereum",
             scan_blocks: int = 3000, max_buyers: int = 20) -> Report:
    if chain not in SUPPORTED_CHAINS:
        raise ValueError(f"unsupported chain: {chain} (supported: {', '.join(SUPPORTED_CHAINS)})")
    # Solana 地址大小写敏感（base58），不下小写；EVM 统一小写
    token_key = token if chain == "solana" else token.lower()
    rep = Report(chain=chain, token=token_key, rules_version=RULES_VERSION)

    if chain == "solana":
        return _run_solana(rep, http, token_key, max_buyers)

    return _run_evm(rep, http, token_key, chain, scan_blocks, max_buyers)


# ================================================================= EVM（ethereum/bsc 共用）
def _run_evm(rep: Report, http: Http, token: str, chain: str,
             scan_blocks: int, max_buyers: int) -> Report:
    is_bsc = chain == "bsc"
    if not _identity_evm(rep, http, token, chain):
        return rep
    if not _buyers_evm(rep, http, token, chain, scan_blocks, max_buyers, is_bsc):
        return rep
    _activity(rep, http, chain=chain)
    _risk_evm(rep, http, token, chain)
    _finalize(rep)
    return rep


def _identity_evm(rep: Report, http: Http, token: str, chain: str) -> bool:
    n = NodeResult("token_identity")
    meta = None
    if chain == "bsc":
        # BSC 无 Blockscout：元数据从 DEX Screener 取，链上元数据标注受限
        n.unknowns.append("BSC 无 Blockscout 实例：holders/decimals 走 DEX Screener，链上核验受限")
    else:
        meta, r = blockscout.token_meta(http, token)
        if meta is None:
            n.fail(f"token_meta:{r.error or r.status}")
        else:
            rep.token_meta = meta
            n.metrics["symbol"] = meta["symbol"]
            n.metrics["holders"] = meta["holders"]
            n.add_ev("token", token, "blockscout")

    pool, rp = dexscreener.best_pool(http, token, chain_id=chain)
    if pool is None:
        n.warnings.append(f"dexscreener:{rp.error or rp.status}")
        n.status = FAILED if n.status != FAILED else n.status
    elif pool.get("pool_kind") != "address":
        n.warnings.append(f"POOL_FORMAT_UNSUPPORTED:{pool.get('pool_kind')}")
        n.metrics["pool_kind"] = pool.get("pool_kind")
        n.status = WARNING
    else:
        rep.pool = pool["pool"]
        n.metrics.update({"pool": pool["pool"], "dex": pool["dex"],
                          "liquidity_usd": pool["liquidity_usd"],
                          "volume_24h": pool["volume_24h"]})
        n.add_ev("pool", pool["pool"], "dexscreener")
        if meta is None and chain == "bsc":
            rep.token_meta = {"symbol": (pool.get("symbol") or ""),
                              "name": "", "mcap": pool.get("mcap", 0.0),
                              "holders": None, "decimals": None}
            n.metrics["symbol"] = pool.get("symbol") or ""

    rep.steps["token_identity"] = n
    if n.status == WARNING and not rep.pool:
        rep.final_grade = "INCOMPLETE"
        return False
    if n.status == FAILED:
        rep.final_grade = BLOCKED
        return False
    return True


def _buyers_evm(rep: Report, http: Http, token: str, chain: str,
                scan_blocks: int, max_buyers: int, is_bsc: bool) -> bool:
    n = NodeResult("early_buyers")
    if is_bsc:
        cb, rc = evmrpc.latest_block(http, "bsc")
        window_start = (cb - scan_blocks) if cb else None   # BSC 只能扫近期（archive 限制）
    else:
        cb, rc = blockscout.creation_block(http, token)
        window_start = cb
    if cb is None or window_start is None:
        n.fail(f"creation_block:{rc.error or rc.status}")
        n.metrics["count"] = None
        rep.steps["early_buyers"] = n
        rep.final_grade = BLOCKED
        return False
    n.metrics["creation_block" if not is_bsc else "reference_block"] = cb
    if is_bsc:
        n.warnings.append(f"BSC_SCAN_RECENT_ONLY:公共 RPC 有 archive 限制，只扫了最近 "
                          f"{scan_blocks} 块，非全史早期买家")
    n.add_ev("token", token, "blockscout" if not is_bsc else "rpc")

    if is_bsc:
        rows, rb = evmrpc.pool_buys_rpc(http, token, rep.pool, window_start, cb, "bsc")
    else:
        rows, rb = blockscout.pool_buys(http, token, rep.pool, window_start, window_start + scan_blocks)
    if rb.status == "FAILED":
        n.fail(f"pool_buys:{rb.error}")
        n.metrics["count"] = None            # 失败不得写 0
        rep.steps["early_buyers"] = n
        rep.final_grade = "INCOMPLETE"
        return False
    if rb.status == "EMPTY_CONFIRMED":
        n.metrics.update({"count": 0, "raw": 0, "pages_exhausted": True})
        n.warnings.append("NO_BUYERS_IN_WINDOW")
        rep.steps["early_buyers"] = n
        return True

    rows, dedup_dropped = dedupe_by_tx(rows)
    buyers, skipped = [], 0
    for row in rows:
        if len(buyers) >= max_buyers:
            break
        if is_bsc:
            info, _ = evmrpc.address_info_rpc(http, row["to"], "bsc")
        else:
            info, _ = blockscout.address_info(http, row["to"])
        if info is None or info.get("is_contract"):
            skipped += 1                     # 无法判定/合约 → 不计为买家
            continue
        buyers.append({"address": row["to"], "block": row["block"], "tx_hash": row["tx_hash"]})
        n.add_ev("tx", row["tx_hash"], "rpc" if is_bsc else "blockscout")

    n.metrics.update({"count": len(buyers), "raw": len(rows) + dedup_dropped,
                      "dedup_dropped": dedup_dropped, "skipped_contracts": skipped,
                      "pages_exhausted": True})
    rep.buyers = buyers
    if not buyers:
        n.warnings.append("ALL_CANDIDATES_FILTERED")
    rep.steps["early_buyers"] = n
    return True


def _risk_evm(rep: Report, http: Http, token: str, chain: str) -> None:
    n = NodeResult("risk_gate")
    risk, r = goplus.token_security(http, token, chain_id=evmrpc.GOPLUS_CHAIN_ID[chain])
    if risk is None:
        n.fail(f"goplus:{r.error or r.status}")
        n.unknowns.append("风险未能核实，不得视为安全")
        rep.risk = {"verified": False}
        rep.steps["risk_gate"] = n
        return
    rep.risk = risk
    n.metrics.update({"blockers": len(risk["blockers"]), "holder_count": risk["holder_count"]})
    n.unknowns.append("蜜罐/可卖性为第三方标签，未做链上实证")
    n.add_ev("risk", token, "goplus")
    if risk["blockers"]:
        n.status = BLOCKED
    _flag_creators(rep, risk)
    rep.steps["risk_gate"] = n


def _flag_creators(rep: Report, risk: dict) -> None:
    """买家与代币 creator/owner 交叉标注。"""
    creator = risk.get("creator")
    owner = risk.get("owner")
    for b in rep.buyers:
        flags = []
        if creator and str(creator).lower() == b["address"].lower():
            flags.append("TOKEN_CREATOR")
        if owner and str(owner).lower() == b["address"].lower():
            flags.append("TOKEN_OWNER")
        if flags:
            b["flags"] = flags


# ================================================================= N3（EVM 共用）
def _activity(rep: Report, http: Http, chain: str = "ethereum") -> None:
    n = NodeResult("wallet_activity")
    if chain == "bsc":
        # BSC 无 Blockscout：30 天活跃数据免 key 不可得 → 显式 UNKNOWN，不冒充
        n.unknowns.append("BSC 无 Blockscout 实例：30 天活跃度免 key 不可得，全部标 UNKNOWN")
        n.warnings.append("ACTIVITY_UNKNOWN_ON_BSC")
        rep.activity = [{**b, "label": "UNKNOWN", "swap_30d": None, "transfer_30d": None,
                         "_tx_times": [], "_tx_blocks": []} for b in rep.buyers]
        n.metrics.update({"wallets_checked": len(rep.buyers), "ok": 0,
                          "failed": len(rep.buyers), "coverage": 0.0, "active": 0})
        n.status = WARNING
        rep.steps["wallet_activity"] = n
        return
    ok, failed, rows = 0, 0, []
    for b in rep.buyers:
        info, _ = blockscout.address_activity(http, b["address"])
        if info is None:
            failed += 1
            rows.append({**b, "label": "UNKNOWN", "swap_30d": None, "transfer_30d": None})
            continue
        days = _days_since(info.get("last_active"))
        swap = info.get("swap_activity")            # None = 原生交易接口不可用（未知）
        truncated = bool(info.get("window_truncated"))
        if swap is None:
            label = "UNKNOWN"                       # swap 数据拿不到：未知，不默认休眠
        elif swap >= SWAP_ACTIVE_MIN and (days is None or days <= ACTIVE_DAYS):
            label = "ACTIVE"
        elif swap > 0:
            label = "LOW_FREQUENCY"
        else:
            label = "DORMANT"
        rows.append({**b, "label": label, "swap_30d": swap,
                     "transfer_30d": info.get("transfer_activity", 0),
                     "contract_transfers_30d": info.get("contract_transfers", 0),
                     "window_truncated": truncated,
                     "_tx_times": info.get("tx_times") or [],
                     "_tx_blocks": info.get("tx_blocks") or [],
                     "days_since": days, "last_active": info.get("last_active")})
        ok += 1

    total = len(rep.buyers) or 1
    n.metrics.update({"wallets_checked": len(rep.buyers), "ok": ok, "failed": failed,
                      "coverage": round(ok / total, 2),
                      "active": sum(1 for r in rows if r["label"] == "ACTIVE")})
    if failed and not ok:
        n.fail("all_wallet_queries_failed")
    elif failed:
        n.status = WARNING
        n.warnings.append(f"{failed}_wallets_unknown")
    rep.activity = rows
    rep.steps["wallet_activity"] = n


# ================================================================= Solana
def _run_solana(rep: Report, http: Http, mint: str, max_buyers: int) -> Report:
    if not _identity_sol(rep, http, mint):
        return rep
    if not _buyers_sol(rep, http, mint, max_buyers):
        return rep
    _activity_sol_stub(rep)
    _risk_sol(rep, http, mint)
    _finalize(rep)
    return rep


def _identity_sol(rep: Report, http: Http, mint: str) -> bool:
    n = NodeResult("token_identity")
    meta, r = solanarpc.mint_info(http, mint)
    if meta is None:
        n.fail(f"mint_info:{r.error or r.status}")
        rep.steps["token_identity"] = n
        rep.final_grade = BLOCKED
        return False
    rep.token_meta = meta
    n.metrics["decimals"] = meta["decimals"]
    n.metrics["supply"] = meta["supply"]
    n.add_ev("token", mint, "solana-rpc")

    pool, rp = dexscreener.best_pool(http, mint, chain_id="solana")
    if pool is None:
        n.warnings.append(f"dexscreener:{rp.error or rp.status}")
        n.status = WARNING
    else:
        n.metrics.update({"pool": pool["pool"], "dex": pool["dex"],
                          "liquidity_usd": pool["liquidity_usd"],
                          "volume_24h": pool["volume_24h"]})
        n.add_ev("pool", pool["pool"], "dexscreener")
        rep.token_meta["symbol"] = rep.token_meta.get("symbol") or pool.get("symbol") or ""

    rep.steps["token_identity"] = n
    if n.status == WARNING and not n.metrics.get("decimals"):
        rep.final_grade = BLOCKED
        return False
    return True


def _buyers_sol(rep: Report, http: Http, mint: str, max_buyers: int) -> bool:
    n = NodeResult("early_buyers")
    buyers, r = solanarpc.classify_buyers(http, mint, max_buyers=max_buyers, max_txs=80)
    if r.status == "FAILED":
        n.fail(f"classify_buyers:{r.error}")
        n.metrics["count"] = None
        rep.steps["early_buyers"] = n
        rep.final_grade = "INCOMPLETE"
        return False
    scanned = (r.payload or {}).get("scanned", 0) if r.payload else 0
    n.metrics.update({"count": len(buyers), "raw": scanned,
                      "scanned_txs": scanned, "pages_exhausted": True})
    n.warnings.append(f"SOLANA_RECENT_ONLY:公共 RPC 限流下只扫了最近 {scanned} 笔签名"
                      f"（新币监控近似，非全史）")
    for b in buyers:
        n.add_ev("tx", b["tx_hash"], "solana-rpc")
    rep.buyers = buyers
    if not buyers:
        n.warnings.append("ALL_CANDIDATES_FILTERED")
    rep.steps["early_buyers"] = n
    return True


def _activity_sol_stub(rep: Report) -> None:
    """Solana 活跃度：公共 RPC 逐钱包拉 30 天签名在限流下不可行 → 显式 UNKNOWN。"""
    n = NodeResult("wallet_activity")
    n.unknowns.append("Solana 免 key 限流下无法逐钱包统计 30 天活跃（需 Helius 等 index 源，Phase 2）")
    rep.activity = [{**b, "label": "UNKNOWN", "swap_30d": None, "transfer_30d": None}
                    for b in rep.buyers]
    n.metrics.update({"wallets_checked": len(rep.buyers), "ok": 0,
                      "failed": len(rep.buyers), "coverage": 0.0, "active": 0})
    n.status = WARNING
    n.warnings.append("ACTIVITY_UNKNOWN_ON_SOLANA_FREE_RPC")
    rep.steps["wallet_activity"] = n


def _risk_sol(rep: Report, http: Http, mint: str) -> None:
    n = NodeResult("risk_gate")
    risk, r = solanarpc.token_security_goplus(http, mint)
    if risk is None:
        n.fail(f"goplus-solana:{r.error or r.status}")
        n.unknowns.append("风险未能核实，不得视为安全")
        rep.risk = {"verified": False}
        rep.steps["risk_gate"] = n
        return
    rep.risk = risk
    n.metrics.update({"blockers": len(risk["blockers"]), "holder_count": risk.get("holder_count")})
    n.unknowns.append("Solana 风险字段（freezable/mintable）为第三方标签，未做链上实证")
    n.unknowns.append("蜜罐/买卖税检测在 Solana 上不适用（SPL 无 tax 机制），字段缺失≠安全")
    n.add_ev("risk", mint, "goplus-solana")
    if risk["blockers"]:
        n.status = BLOCKED
    _flag_creators(rep, risk)
    rep.steps["risk_gate"] = n


# ================================================================= N5（共用）
def _finalize(rep: Report) -> None:
    n = NodeResult("report")
    total = len(rep.buyers) or 0
    active = sum(1 for r in rep.activity if r["label"] == "ACTIVE")
    liq = (rep.steps["token_identity"].metrics or {}).get("liquidity_usd", 0) or 0

    buyer_score = 25 * min(total / 20, 1)
    activity_score = 25 * (active / total if total else 0)
    liq_score = 25 * (1.0 if liq >= 1_000_000 else 0.6 if liq >= 100_000
                      else 0.3 if liq >= 10_000 else 0.0)
    risk_score = 0 if (rep.risk.get("blockers") or not rep.risk.get("verified", False)) else 25
    rep.score = round(buyer_score + activity_score + liq_score + risk_score, 1)

    n.metrics.update({"score": rep.score, "buyers": total, "active": active,
                      "liquidity_usd": liq, "risk_blockers": rep.risk.get("blockers", [])})
    n.unknowns.append("历史盈亏：免费源无法计算成本基础")

    rep.steps["report"] = n          # 先登记，再判 complete（否则会把自己算成"缺步骤"）
    if rep.risk.get("blockers"):
        rep.final_grade = BLOCKED
    elif not rep.complete:
        rep.final_grade = "INCOMPLETE"
    elif rep.score >= 60:
        rep.final_grade = "RESEARCH"
    else:
        rep.final_grade = "WATCH"
    n.metrics["complete"] = rep.complete


def _days_since(ts: Optional[str]) -> Optional[int]:
    if not ts:
        return None
    try:
        t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - t).days
