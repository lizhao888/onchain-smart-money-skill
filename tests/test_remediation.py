"""整改回归测试（2026-10-19 验收 BUG-001/002/003 对应修复）。

BUG-001: Blockscout 兼容层「No logs found」是真空，不是失败（用真实响应体）。
BUG-002: swap_30d 必须按 30 天窗口过滤；合约→EOA 转账不算 swap（T-009）。
BUG-003: --chain 白名单，非 ethereum 直接拒绝。
"""
from __future__ import annotations

import pytest

from conftest import (POOL, TOKEN, http_for, make_world, url_activity, url_transactions,
                      url_token, url_pairs, url_addr)
from onchain_sm.models import FAILED, PASSED
from onchain_sm.pipeline.run import run_scan
from onchain_sm.providers.blockscout import pool_buys
from onchain_sm.http import Http

# 2026-10-19 从 Blockscout 实录的真实响应体（USDT 真空窗口）
REAL_NO_LOGS = {"status": "1", "message": "No logs found", "result": None}
# 实录的真实参数错误响应
REAL_PARAM_ERROR = {"message": "Required query parameters missing: topic0_1_opr", "result": None}


# ---------------------------------------------------------------- BUG-001
def test_bug001_no_logs_found_is_empty_confirmed():
    """真实「No logs found」响应 → EMPTY_CONFIRMED，不得判 FAILED。"""
    rows, r = pool_buys(http_for({}), TOKEN, POOL, 4634748, 4637748)
    # 直接构造响应测试（不经过 make_world 的 getLogs 桩）
    world = {f"https://eth.blockscout.com/api?module=logs&action=getLogs"
             f"&fromBlock=4634748&toBlock=4637748&address={TOKEN}"
             f"&topic0=0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
             f"&topic1=0x000000000000000000000000{POOL[2:].lower()}&topic0_1_opr=and": REAL_NO_LOGS}
    rows, r = pool_buys(Http(fetch=__import__("conftest").make_fetch(world)), TOKEN, POOL, 4634748, 4637748)
    assert r.status == "EMPTY_CONFIRMED"
    assert rows == []


def test_bug001_param_error_still_failed():
    """真实参数错误响应 → 仍是 FAILED，不得混入真空分支。"""
    url = (f"https://eth.blockscout.com/api?module=logs&action=getLogs"
           f"&fromBlock=1&toBlock=2&address={TOKEN}"
           f"&topic0=0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
           f"&topic1=0x000000000000000000000000{POOL[2:].lower()}&topic0_1_opr=and")
    rows, r = pool_buys(Http(fetch=__import__("conftest").make_fetch({url: REAL_PARAM_ERROR})),
                        TOKEN, POOL, 1, 2)
    assert r.status == "FAILED"
    assert "Required query parameters" in (r.error or "")


def test_bug001_usdt_empty_window_end_to_end():
    """USDT 真空窗口端到端：early_buyers PASSED + count=0，报告 complete=true。"""
    rep = run_scan(http_for(make_world(buyers=0, getlogs_response=REAL_NO_LOGS)),
                   TOKEN, max_buyers=5)
    step = rep.steps["early_buyers"]
    assert step.status == PASSED
    assert step.metrics["count"] == 0
    assert step.metrics["pages_exhausted"] is True
    assert rep.complete is True
    assert rep.final_grade != "INCOMPLETE"


# ---------------------------------------------------------------- BUG-002
def _old_item(i, days_ago_timestamp):
    return {"from": {"hash": "0xpool", "is_contract": True}, "to": {"hash": "0x" + f"{i+1:040x}"},
            "token": {"address_hash": "0xt1", "symbol": "AAA"}, "timestamp": days_ago_timestamp}


def test_bug002_old_contract_transfers_not_swaps():
    """1 年前的合约→本人转账大量存在 → 仍判 DORMANT，swap_30d 不含历史记录（T-009）。"""
    def activities(i):
        # 2023 年的合约转账（窗口外）×30
        return [_old_item(i, "2023-04-14T00:00:00Z") for _ in range(30)]

    def transactions(i):
        return [{"method": "swapExactTokensForTokens", "timestamp": "2023-04-14T00:00:00Z"}
                for _ in range(10)]        # 窗口外的 swap 也不算

    rep = run_scan(http_for(make_world(buyers=2, activities=activities, transactions=transactions)),
                   TOKEN, max_buyers=5)
    for a in rep.activity:
        assert a["label"] == "DORMANT", f"历史合约转账被误判为活跃: {a}"
        assert a["swap_30d"] == 0
        assert a["contract_transfers_30d"] == 0        # 窗口外不计入任何指标


def test_bug002_recent_swaps_active():
    """30 天内 3 笔 swap 分布 2 个日期 → ACTIVE。"""
    def activities(i):
        return []

    def transactions(i):
        return [{"method": "swapExactTokensForETH", "timestamp": "2026-10-15T00:00:00Z"},
                {"method": "swapExactTokensForETH", "timestamp": "2026-10-14T00:00:00Z"},
                {"method": "swapExactTokensForETH", "timestamp": "2026-10-13T00:00:00Z"}]

    rep = run_scan(http_for(make_world(buyers=2, activities=activities, transactions=transactions)),
                   TOKEN, max_buyers=5)
    assert all(a["label"] == "ACTIVE" for a in rep.activity)
    assert all(a["swap_30d"] == 3 for a in rep.activity)


def test_bug002_contract_transfer_in_window_not_swap():
    """窗口内的合约→本人转账：不算 swap，记 contract_transfers（空投/撤池不是交易）。"""
    def activities(i):
        return [_old_item(i, "2026-10-15T00:00:00Z") for _ in range(5)]   # 窗口内

    def transactions(i):
        return []                                                          # 无原生 swap

    rep = run_scan(http_for(make_world(buyers=2, activities=activities, transactions=transactions)),
                   TOKEN, max_buyers=5)
    for a in rep.activity:
        assert a["label"] in ("DORMANT", "UNKNOWN")     # 不得因合约转账判 ACTIVE
        assert a["swap_30d"] == 0
        assert a["contract_transfers_30d"] == 5


def test_bug002_transactions_api_down_swap_unknown():
    """原生交易接口挂了 → swap_30d=None + UNKNOWN，不默认休眠。"""
    world = make_world(buyers=2)
    for u in [u for u in world if "addresses/" in u and u.endswith("/transactions")]:
        world[u] = (500, "boom")
    rep = run_scan(http_for(world), TOKEN, max_buyers=5)
    for a in rep.activity:
        assert a["swap_30d"] is None
        assert a["label"] == "UNKNOWN"


def test_bug007_creator_flagged():
    """首买家若就是代币部署者 → 买家带 TOKEN_CREATOR 标注。"""
    def txs(i):
        return []
    rep = run_scan(http_for(make_world(buyers=3, transactions=txs)), TOKEN, max_buyers=5)
    # make_world 的 GoPlus 桩 creator 是 0xcreator（不匹配）→ 无标注
    assert all("flags" not in b for b in rep.buyers)
    # 用真实场景：creator = 第一个买家
    from conftest import url_goplus
    world = make_world(buyers=3, transactions=txs)
    world[url_goplus(TOKEN)]["result"][TOKEN.lower()]["creator_address"] = rep.buyers[0]["address"]
    rep2 = run_scan(http_for(world), TOKEN, max_buyers=5)
    assert rep2.buyers[0].get("flags") == ["TOKEN_CREATOR"]
    assert all("flags" not in b for b in rep2.buyers[1:])


# ---------------------------------------------------------------- BUG-003
def test_bug003_unsupported_chain_rejected():
    """--chain 不在支持列表 → SystemExit，不发任何请求。"""
    from onchain_sm.cli import main
    with pytest.raises(SystemExit) as ei:
        main(["run", "--token", TOKEN, "--chain", "avalanche"])   # 多链整改后仍不在白名单
    assert ei.value.code != 0


def test_bug003_ethereum_chain_accepted():
    """默认链 ethereum 正常（不因白名单误伤）。"""
    rep = run_scan(http_for(make_world(buyers=2)), TOKEN, max_buyers=5)
    assert rep.chain == "ethereum"
