"""场景测试（对应 MVP_ACCEPTANCE.md §2）。

已实现：S1/S3/S4/S8/S9/S14/S16 + 空结果
未实现（Phase 2）：S2/S5/S6/S7/S10/S11/S12/S13/S15 —— 用 skip 标出来，不假装通过。
"""
from __future__ import annotations

import pytest

from conftest import (POOL, TOKEN, http_for, make_world, url_addr, url_activity, url_goplus,
                      url_pairs, url_token)
from onchain_sm.models import BLOCKED, FAILED, PASSED, WARNING
from onchain_sm.pipeline.run import run_scan


# ---------------------------------------------------------------- S1
def test_s1_happy_path():
    rep = run_scan(http_for(make_world(buyers=5)), TOKEN, max_buyers=5)
    assert rep.steps["token_identity"].status == PASSED
    assert rep.steps["early_buyers"].status == PASSED
    assert rep.steps["early_buyers"].metrics["count"] == 5
    assert len(rep.buyers) == 5
    assert all(b["tx_hash"] for b in rep.buyers)
    assert rep.complete is True
    assert rep.final_grade in ("RESEARCH", "WATCH")


# ---------------------------------------------------------------- S3
def test_s3_contract_buyer_is_skipped():
    rep = run_scan(http_for(make_world(buyers=3, contract_buyers=1)), TOKEN, max_buyers=10)
    m = rep.steps["early_buyers"].metrics
    assert m["count"] == 2
    assert m["skipped_contracts"] == 1


# ---------------------------------------------------------------- S4
def test_s4_transfer_only_is_dormant():
    def activities(i):
        return [{"from": {"hash": "0xhuman", "is_contract": False}, "to": {"hash": "0x" + f"{i+1:040x}"},
                 "token": {"address_hash": "0xt1", "symbol": "AAA"}, "timestamp": "2026-10-15T00:00:00Z"}]

    rep = run_scan(http_for(make_world(buyers=2, activities=activities)), TOKEN, max_buyers=5)
    labels = [a["label"] for a in rep.activity]
    assert labels == ["DORMANT", "DORMANT"]
    assert all(a["swap_30d"] == 0 for a in rep.activity)
    assert all(a["transfer_30d"] > 0 for a in rep.activity)


# ---------------------------------------------------------------- S8
def test_s8_risk_blocker_overrides_score():
    risky = {"buy_tax": "0", "sell_tax": "0", "cannot_sell_all": "1", "is_honeypot": "0",
             "is_blacklisted": "0", "is_mintable": "0", "can_take_back_ownership": "0",
             "owner_address": "0xowner", "creator_address": "0xcreator",
             "holder_count": "1200", "top_10_holder_rate": "0.2"}
    rep = run_scan(http_for(make_world(buyers=20, risk=risky)), TOKEN, max_buyers=20)
    assert "CANNOT_SELL" in rep.risk["blockers"]
    assert rep.steps["risk_gate"].status == BLOCKED
    assert rep.final_grade == BLOCKED
    assert rep.score is not None          # 分数仍被计算，但不作为推进依据


# ---------------------------------------------------------------- S9
def test_s9_source_failure_is_not_zero():
    world = make_world(buyers=3)
    url = [u for u in world if "action=getLogs" in u][0]
    world[url] = (500, "boom")
    rep = run_scan(http_for(world), TOKEN, max_buyers=5)
    step = rep.steps["early_buyers"]
    assert step.status == FAILED
    assert step.metrics["count"] is None      # 关键：失败不是 0
    assert rep.complete is False
    assert rep.final_grade in ("INCOMPLETE", BLOCKED)


# ---------------------------------------------------------------- S14
def test_s14_duplicate_tx_deduped():
    world = make_world(buyers=2)
    url = [u for u in world if "action=getLogs" in u][0]
    world[url]["result"].append(dict(world[url]["result"][0]))     # 同一 tx 重复
    rep = run_scan(http_for(world), TOKEN, max_buyers=10)
    m = rep.steps["early_buyers"].metrics
    assert m["dedup_dropped"] == 1
    assert m["count"] == 2


# ---------------------------------------------------------------- S16
def test_s16_missing_field_is_schema_mismatch():
    rep = run_scan(http_for(make_world(buyers=2, token_meta={})), TOKEN, max_buyers=5)
    assert rep.steps["token_identity"].status == FAILED
    assert "SCHEMA_MISMATCH" in rep.steps["token_identity"].errors[0]
    assert rep.final_grade == BLOCKED


# ---------------------------------------------------------------- 空结果
def test_empty_window_is_confirmed_zero():
    rep = run_scan(http_for(make_world(buyers=0)), TOKEN, max_buyers=5)
    step = rep.steps["early_buyers"]
    assert step.status == PASSED
    assert step.metrics["count"] == 0
    assert step.metrics["pages_exhausted"] is True
    assert "NO_BUYERS_IN_WINDOW" in step.warnings


# ---------------------------------------------------------------- 风险未知
def test_risk_unavailable_lowers_score_and_marks_unknown():
    rep = run_scan(http_for(make_world(buyers=3, risk={})), TOKEN, max_buyers=5)
    # goplus 返回空对象 → EMPTY_CONFIRMED → 风险未核实
    assert rep.risk.get("verified") is False
    assert any("风险未能核实" in u for u in rep.steps["risk_gate"].unknowns)
    assert rep.score < 100


# ---------------------------------------------------------------- Phase 2 未实现
@pytest.mark.skip(reason="Phase 2：需要同名不同合约样本与 symbol 冲突检测")
def test_s2_same_symbol_conflict():
    ...


@pytest.mark.skip(reason="Phase 2：机器人分类需要多特征")
def test_s5_fast_but_thin():
    ...


@pytest.mark.skip(reason="Phase 2：关联钱包需要资金流转图")
def test_s6_related_wallets():
    ...


@pytest.mark.skip(reason="Phase 2：共识需要跨币累计样本")
def test_s7_independent_consensus():
    ...


@pytest.mark.skip(reason="Phase 2：双源市值冲突检测")
def test_s10_source_conflict():
    ...


@pytest.mark.skip(reason="Phase 2：持仓状态需要余额与转移追踪")
def test_s11_position_transfer():
    ...


@pytest.mark.skip(reason="Phase 2：回测框架")
def test_s12_lookahead():
    ...


@pytest.mark.skip(reason="Phase 2：空投识别（sender 聚类）")
def test_s13_airdrop_consensus():
    ...


@pytest.mark.skip(reason="Phase 2：分页（当前只扫一个窗口）")
def test_s15_partial_paging():
    ...
