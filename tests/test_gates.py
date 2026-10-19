"""门禁测试：防止「失败被写成 0」「空结果与失败混淆」「报告结构漂移」。"""
from __future__ import annotations

import pytest

from conftest import TOKEN, http_for, load_pepe, make_world
from onchain_sm.models import FAILED, PASSED
from onchain_sm.pipeline.run import run_scan

REQUIRED_REPORT_KEYS = {"schema", "chain", "token", "generated_at", "rules_version",
                        "token_meta", "steps", "buyers", "activity", "risk",
                        "score", "final_grade", "complete"}


@pytest.mark.parametrize("break_url", ["logs", "token", "activity"])
def test_g1_no_fake_success(break_url):
    """任何数据源失败，都不允许把 count 写成 0。"""
    world = make_world(buyers=3)
    if break_url == "logs":
        url = [u for u in world if "action=getLogs" in u][0]
        world[url] = (500, "boom")
    elif break_url == "token":
        url = [u for u in world if "/api/v2/tokens/" in u][0]
        world[url] = (500, "boom")
    else:
        for u in [u for u in world if "token-transfers" in u]:
            world[u] = (500, "boom")

    rep = run_scan(http_for(world), TOKEN, max_buyers=5)
    assert rep.complete is False
    for step in rep.steps.values():
        if step.status == FAILED:
            assert step.metrics.get("count", None) is None or "count" not in step.metrics
    if break_url == "activity":
        assert rep.steps["wallet_activity"].status == FAILED
        assert rep.steps["wallet_activity"].metrics["coverage"] == 0

def test_g2_zero_vs_failure_are_different():
    empty = run_scan(http_for(make_world(buyers=0)), TOKEN, max_buyers=5)
    world = make_world(buyers=3)
    world[[u for u in world if "action=getLogs" in u][0]] = (500, "boom")
    failed = run_scan(http_for(world), TOKEN, max_buyers=5)

    assert empty.steps["early_buyers"].status == PASSED
    assert empty.steps["early_buyers"].metrics["count"] == 0
    assert failed.steps["early_buyers"].status == FAILED
    assert failed.steps["early_buyers"].metrics["count"] is None
    assert empty.as_dict() != failed.as_dict()


def test_g4_report_schema():
    rep = run_scan(http_for(make_world(buyers=3)), TOKEN, max_buyers=5)
    d = rep.as_dict()
    assert REQUIRED_REPORT_KEYS <= set(d)
    assert isinstance(d["steps"], dict) and d["steps"]
    for name, step in d["steps"].items():
        assert set(step) >= {"step", "status", "metrics", "evidence", "errors", "warnings", "unknowns"}
        assert step["status"] in ("PASSED", "WARNING", "BLOCKED", "FAILED")
    assert isinstance(d["buyers"], list)
    assert d["final_grade"] in ("BLOCKED", "INCOMPLETE", "RESEARCH", "WATCH")
    assert isinstance(d["complete"], bool)


def test_g5_evidence_present_for_buyers():
    rep = run_scan(http_for(make_world(buyers=4)), TOKEN, max_buyers=5)
    ev = rep.steps["early_buyers"].evidence
    assert len([e for e in ev if e.kind == "tx"]) == 4
    assert all(e.ref and e.source and e.retrieved_at for e in ev)


def test_g6_deterministic_score():
    w = make_world(buyers=3)
    a = run_scan(http_for(w), TOKEN, max_buyers=5)
    b = run_scan(http_for(w), TOKEN, max_buyers=5)
    assert a.score == b.score
    # 时间戳允许不同，其余应一致
    da, db = a.as_dict(), b.as_dict()
    da.pop("generated_at"); db.pop("generated_at")
    assert da == db


def test_real_fixture_replay_pepe():
    """用 2026-10-18 录制的真实响应离线回放，验证管线在真实数据上能跑通。"""
    rep = run_scan(http_for(load_pepe()), TOKEN, max_buyers=5, scan_blocks=3000)
    assert rep.steps["token_identity"].status == PASSED
    assert rep.steps["early_buyers"].status == PASSED
    assert rep.steps["early_buyers"].metrics["count"] == 5
    assert len(rep.buyers) == 5
    assert all(b["tx_hash"].startswith("0x") for b in rep.buyers)
    assert rep.score is not None
