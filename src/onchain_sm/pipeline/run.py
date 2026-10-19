"""MVP-S 管线：5 个节点，只读。

设计要点（对应 MVP_ACCEPTANCE.md）：
- 每个节点独立判状态；失败向上传播为 BLOCKED/INCOMPLETE，绝不降级成 0。
- 买家只认「池子 → EOA」的 Transfer；合约地址跳过并计数。
- 活跃度区分 swap / transfer；数据拿不到标 UNKNOWN，不默认休眠。
- 风险闸门失败时，总分不覆盖阻断。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..http import Http
from ..models import (BLOCKED, FAILED, PASSED, WARNING, NodeResult, Report)
from ..providers import blockscout, dexscreener, goplus
from ..providers.base import dedupe_by_tx

ACTIVE_DAYS = 30
SWAP_ACTIVE_MIN = 3
RULES_VERSION = "v0"


def run_scan(http: Http, token: str, chain: str = "ethereum",
             scan_blocks: int = 3000, max_buyers: int = 20) -> Report:
    rep = Report(chain=chain, token=token.lower(), rules_version=RULES_VERSION)

    if not _identity(rep, http, token, chain):
        return rep
    if not _buyers(rep, http, token, scan_blocks, max_buyers):
        return rep
    _activity(rep, http)
    _risk(rep, http, token)
    _finalize(rep)
    return rep


# ---------------------------------------------------------------- N1
def _identity(rep: Report, http: Http, token: str, chain: str) -> bool:
    n = NodeResult("token_identity")
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
        # V4 poolId / 未知格式：当前不支持，不能拿它去过滤日志（会误把所有转账当买入）
        n.warnings.append(f"POOL_FORMAT_UNSUPPORTED:{pool.get('pool_kind')}")
        n.metrics["pool_kind"] = pool.get("pool_kind")
        n.status = WARNING
    else:
        rep.pool = pool["pool"]
        n.metrics.update({"pool": pool["pool"], "dex": pool["dex"],
                          "liquidity_usd": pool["liquidity_usd"],
                          "volume_24h": pool["volume_24h"]})
        n.add_ev("pool", pool["pool"], "dexscreener")

    rep.steps["token_identity"] = n
    if n.status == WARNING and not rep.pool:
        rep.final_grade = "INCOMPLETE"
        return False
    if n.status == FAILED:
        rep.final_grade = BLOCKED
        return False
    return True


# ---------------------------------------------------------------- N2
def _buyers(rep: Report, http: Http, token: str, scan_blocks: int, max_buyers: int) -> bool:
    n = NodeResult("early_buyers")
    cb, rc = blockscout.creation_block(http, token)
    if cb is None:
        n.fail(f"creation_block:{rc.error or rc.status}")
        n.metrics["count"] = None
        rep.steps["early_buyers"] = n
        rep.final_grade = BLOCKED
        return False
    n.metrics["creation_block"] = cb
    n.add_ev("token", token, "blockscout")   # 部署交易可复核

    rows, rb = blockscout.pool_buys(http, token, rep.pool, cb, cb + scan_blocks)
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
        info, _ = blockscout.address_info(http, row["to"])
        if info is None or info.get("is_contract"):
            skipped += 1                     # 无法判定/合约 → 不计为买家
            continue
        buyers.append({"address": row["to"], "block": row["block"], "tx_hash": row["tx_hash"]})
        n.add_ev("tx", row["tx_hash"], "blockscout")

    n.metrics.update({"count": len(buyers), "raw": len(rows) + dedup_dropped,
                      "dedup_dropped": dedup_dropped, "skipped_contracts": skipped,
                      "pages_exhausted": True})
    rep.buyers = buyers
    if not buyers:
        n.warnings.append("ALL_CANDIDATES_FILTERED")
    rep.steps["early_buyers"] = n
    return True


# ---------------------------------------------------------------- N3
def _activity(rep: Report, http: Http) -> None:
    n = NodeResult("wallet_activity")
    ok, failed, rows = 0, 0, []
    for b in rep.buyers:
        info, _ = blockscout.address_activity(http, b["address"])
        if info is None:
            failed += 1
            rows.append({**b, "label": "UNKNOWN", "swap_30d": None, "transfer_30d": None})
            continue
        days = _days_since(info.get("last_active"))
        swap = info.get("swap_activity", 0)
        if swap >= SWAP_ACTIVE_MIN and (days is None or days <= ACTIVE_DAYS):
            label = "ACTIVE"
        elif swap > 0:
            label = "LOW_FREQUENCY"
        else:
            label = "DORMANT"
        rows.append({**b, "label": label, "swap_30d": swap,
                     "transfer_30d": info.get("transfer_activity", 0),
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


# ---------------------------------------------------------------- N4
def _risk(rep: Report, http: Http, token: str) -> None:
    n = NodeResult("risk_gate")
    risk, r = goplus.token_security(http, token)
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
    rep.steps["risk_gate"] = n


# ---------------------------------------------------------------- N5
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
