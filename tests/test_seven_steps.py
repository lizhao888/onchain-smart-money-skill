"""P2-4/5/6 测试：机器人剔除、幸存者画像、共买信号（对应 7 步法第 4/5/6 步）。"""
from __future__ import annotations

import pytest

from onchain_sm.pipeline.botfilter import classify_bot
from onchain_sm.pipeline.consensus import build_signals
from onchain_sm.pipeline import survivor_portrait


# ================================================================ P2-4 机器人
NOW = 1_800_000_000

def test_bot_high_frequency_detected():
    """间隔中位数 5 秒 + 金额整齐 → LIKELY_BOT（7步法第4步核心场景）。"""
    times = [NOW - i * 5 for i in range(20)]
    amounts = [1.0] * 20
    r = classify_bot(tx_times=times, tx_amounts=amounts)
    assert r["label"] == "LIKELY_BOT"
    assert r["score"] >= 4
    assert any("间隔中位数" in e for e in r["evidence"])


def test_human_normal_gaps():
    """人间隔（小时级）→ HUMAN。"""
    times = [NOW - i * 3600 * 7 for i in range(10)]
    r = classify_bot(tx_times=times, tx_amounts=[1.0, 1.2, 0.8, 2.0, 1.5, 0.9, 3.0])
    assert r["label"] == "HUMAN"
    assert r["weight_factor"] == 1.0


def test_bot_same_block_burst():
    """同块多笔 → 加分。"""
    times = [NOW - 3600 * i for i in range(10)]
    blocks = [100] * 6 + [200, 300, 400, 500]      # 6 笔同块
    r = classify_bot(tx_times=times, tx_blocks=blocks)
    assert r["score"] >= 2
    assert any("同块多笔" in e for e in r["evidence"])


def test_bot_tight_amounts():
    """金额高度整齐（ sniper 批量单）→ +1。"""
    times = [NOW - i * 120 for i in range(10)]     # 2 分钟间隔（不触发间隔项）
    amounts = [1.0] * 10
    r = classify_bot(tx_times=times, tx_amounts=amounts)
    assert r["score"] >= 1
    assert any("整齐" in e for e in r["evidence"])


def test_insufficient_data_is_unknown_not_human():
    """笔数<3 → UNKNOWN（禁止默认 HUMAN）。"""
    r = classify_bot(tx_times=[NOW, NOW - 60])
    assert r["label"] == "UNKNOWN"
    assert r["weight_factor"] == 0.0


# ================================================================ P2-6 共买信号
def _portrait(wallet, token, first_buy_at, still=True):
    return {"wallet": wallet,
            "recent_buys": [{"token": token, "symbol": "X", "first_buy_at": first_buy_at,
                             "buy_count": 2, "still_holding": still}],
            "buy_token_count": 1}


def test_consensus_three_wallets_triggers():
    """3 个钱包 5 天内同买 X → 触发（7步法第6步）。"""
    token = "0xabc"
    portraits = [
        _portrait("0xw1", token, "2026-10-15T00:00:00+00:00"),
        _portrait("0xw2", token, "2026-10-16T00:00:00+00:00"),
        _portrait("0xw3", token, "2026-10-18T00:00:00+00:00", still=False),
    ]
    sigs = build_signals(portraits, k=3, window_days=7)
    assert len(sigs) == 1
    s = sigs[0]
    assert s["buyer_count"] == 3
    assert s["still_holding_count"] == 2
    assert s["estimated_entities"] == 3
    assert 0 < s["weight"] <= 1


def test_consensus_two_wallets_no_signal():
    """只有 2 个钱包 → 不触发（K=3）。"""
    token = "0xabc"
    portraits = [_portrait("0xw1", token, "2026-10-15T00:00:00+00:00"),
                 _portrait("0xw2", token, "2026-10-16T00:00:00+00:00")]
    assert build_signals(portraits, k=3, window_days=7) == []


def test_consensus_window_exceeded():
    """3 钱包但首买跨 20 天 → 超出 7 天窗口，不触发。"""
    token = "0xabc"
    portraits = [
        _portrait("0xw1", token, "2026-10-01T00:00:00+00:00"),
        _portrait("0xw2", token, "2026-10-11T00:00:00+00:00"),
        _portrait("0xw3", token, "2026-10-21T00:00:00+00:00"),
    ]
    assert build_signals(portraits, k=3, window_days=7) == []


def test_consensus_same_entity_votes_not_counted():
    """3 地址同 1 主体（同资金源合并）→ 不触发（防刷票红线）。"""
    token = "0xabc"
    portraits = [
        _portrait("0xw1", token, "2026-10-15T00:00:00+00:00"),
        _portrait("0xw2", token, "2026-10-15T01:00:00+00:00"),
        _portrait("0xw3", token, "2026-10-15T02:00:00+00:00"),
    ]
    ent = {"0xw1": 1, "0xw2": 1, "0xw3": 1}      # 全部同一主体
    assert build_signals(portraits, k=3, window_days=7, wallet_entities=ent) == []


# ================================================================ P2-5 幸存者画像（离线桩）
def test_portrait_filters_airdrop_and_counts_swaps():
    """画像只认「合约→钱包」的买入；EOA 转入（空投/转账）不计入。"""
    from onchain_sm.http import Http
    import json
    WALLET = "0x" + "aa" * 20
    TOKEN = "0x" + "bb" * 20
    url = f"https://eth.blockscout.com/api/v2/addresses/{WALLET}/token-transfers?type=ERC-20"
    world = {url: {"items": [
        # 合约→钱包：真买入 ✓
        {"from": {"hash": "0x" + "cc" * 20, "is_contract": True}, "to": {"hash": WALLET},
         "token": {"address_hash": TOKEN, "symbol": "X"}, "timestamp": "2026-10-15T00:00:00Z"},
        # 合约→钱包但收的是 USDT（卖出结算）✗
        {"from": {"hash": "0x" + "cc" * 20, "is_contract": True}, "to": {"hash": WALLET},
         "token": {"address_hash": "0xdac17f958d2ee523a2206206994597c13d831ec7", "symbol": "USDT"},
         "timestamp": "2026-10-15T00:30:00Z"},
        # EOA→钱包：空投 ✗
        {"from": {"hash": "0x" + "dd" * 20, "is_contract": False}, "to": {"hash": WALLET},
         "token": {"address_hash": "0x" + "ee" * 20, "symbol": "SCAM"}, "timestamp": "2026-10-15T01:00:00Z"},
        # 零地址铸造 ✗
        {"from": {"hash": "0x" + "00" * 20, "is_contract": True}, "to": {"hash": WALLET},
         "token": {"address_hash": "0x" + "ff" * 20, "symbol": "MINT"}, "timestamp": "2026-10-15T02:00:00Z"},
        # 30 天前 ✗
        {"from": {"hash": "0x" + "cc" * 20, "is_contract": True}, "to": {"hash": WALLET},
         "token": {"address_hash": "0x" + "11" * 20, "symbol": "OLD"}, "timestamp": "2026-08-01T00:00:00Z"},
        # 转出 ✗（to != wallet）
        {"from": {"hash": WALLET, "is_contract": False}, "to": {"hash": "0x" + "22" * 20},
         "token": {"address_hash": TOKEN, "symbol": "X"}, "timestamp": "2026-10-16T00:00:00Z"},
    ]}}
    def fetch(u):
        if u in world:
            return 200, json.dumps(world[u])
        raise AssertionError(f"未录制的请求：{u}")
    http = Http(fetch=fetch)
    buys, err = survivor_portrait.wallet_recent_buys(http, WALLET, days=30)
    assert err is None
    assert len(buys) == 1
    assert buys[0]["token"] == TOKEN
    assert buys[0]["symbol"] == "X"
    assert buys[0]["buy_count"] == 1
