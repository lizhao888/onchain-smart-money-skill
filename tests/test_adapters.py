"""三链适配器离线测试（补 coverage：evmrpc/solanarpc 核心逻辑）。"""
from __future__ import annotations

import json

import pytest

from onchain_sm.http import Http, Response
from onchain_sm.pipeline.botfilter import classify_bot
from onchain_sm.pipeline import consensus, survivor_portrait
from onchain_sm.providers import solanarpc


def make_sol_http(world):
    def fetch(url):
        if url in world:
            return 200, json.dumps(world[url])
        raise AssertionError(f"未录制: {url}")
    return Http(fetch=fetch)


def _mock_sol_rpc(monkeypatch, payload):
    """solanarpc._rpc 直连真网，测试必须 mock 它本身（禁止联网）。"""
    def fake(http, method, params):
        if payload.get("error"):
            return None, Response("FAILED", None, 200,
                                  f"RPC:{payload['error']['code']} {payload['error']['message']}",
                                  solanarpc.RPC_URL, 1)
        return payload.get("result"), Response("OK", payload, 200, None, solanarpc.RPC_URL, 1)
    monkeypatch.setattr(solanarpc, "_rpc", fake)


def test_sol_mint_info_ok(monkeypatch):
    MINT = "5Sqq3NWD98JkcirdDBKRxZFsoZ8f1qqpFp6DSZkoTsHd"
    _mock_sol_rpc(monkeypatch, {"result": {"value": {"data": {"parsed": {"type": "mint",
        "info": {"decimals": 6, "supply": "1000000"}}}, "owner": "TokenkegQ"}}})
    info, r = solanarpc.mint_info(Http(), MINT)
    assert r.status == "OK"
    assert info["decimals"] == 6


def test_sol_mint_info_not_mint(monkeypatch):
    MINT = "5Sqq3NWD98JkcirdDBKRxZFsoZ8f1qqpFp6DSZkoTsHd"
    _mock_sol_rpc(monkeypatch, {"result": {"value": {"data": {"parsed": {"type": "account", "info": {}}}}}})
    info, r = solanarpc.mint_info(Http(), MINT)
    assert info is None and r.status == "FAILED"


def test_sol_rpc_error_propagates(monkeypatch):
    MINT = "5Sqq3NWD98JkcirdDBKRxZFsoZ8f1qqpFp6DSZkoTsHd"
    _mock_sol_rpc(monkeypatch, {"error": {"code": -32005, "message": "limit exceeded"}})
    sigs, r = solanarpc.token_recent_sigs(Http(), MINT)
    assert sigs == [] and r.status == "FAILED" and "limit" in (r.error or "")


def test_sol_loaded_addresses_list_form():
    """loadedAddresses 为列表形态时不崩溃（回归：json 编码差异）。"""
    tx = {"transaction": {"message": {"accountKeys": ["675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"]}},
          "meta": {"loadedAddresses": [["aaa"], ["bbb"]], "err": None}}
    assert solanarpc._touches_dex_program(tx) is True


def test_evm_rpc_archive_error_flagged():
    from onchain_sm.providers import evmrpc
    # archive 错误必须显式标 ARCHIVE_REQUIRED（不是当空结果）
    def fake_urlopen(req, timeout=30):
        class R:
            status = 200
            def read(self): return json.dumps({"jsonrpc":"2.0","id":1,"error":{"code":-32005,"message":"Archive requests require a personal token"}}).encode()
            def __enter__(self): return self
            def __exit__(self, *a): pass
        return R()
    import urllib.request
    orig = urllib.request.urlopen
    urllib.request.urlopen = fake_urlopen
    try:
        result, r = evmrpc._rpc_call(Http(), "https://bsc.publicnode.com", "eth_getLogs", [{}])
    finally:
        urllib.request.urlopen = orig
    assert r.status == "FAILED" and "ARCHIVE" in (r.error or "")


def test_evm_rpc_unsupported_chain():
    from onchain_sm.providers import evmrpc
    rows, r = evmrpc.pool_buys_rpc(Http(), "0xabc", "0xdef", 1, 2, "solana")
    assert r.status == "FAILED" and "UNSUPPORTED_CHAIN" in r.error


def test_goplus_chain_param():
    """GoPlus chain_id 参数化（BSC=56）。"""
    import onchain_sm.providers.goplus as g
    captured = {}
    class FakeHttp:
        def get_json(self, url):
            captured["url"] = url
            class R:
                ok = True; payload = {"code": 1, "result": {}}
                http_status = 200; error = None; status = "OK"; attempts = 1
            r_obj = R(); r_obj.url = url
            return r_obj
    g.token_security(FakeHttp(), "0xABC", chain_id=56)
    assert "/56?" in captured["url"]
