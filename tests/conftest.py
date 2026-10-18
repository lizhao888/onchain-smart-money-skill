"""测试桩：所有请求必须命中夹具，未命中直接报错（防止偷偷联网）。"""
from __future__ import annotations

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from onchain_sm.http import Http          # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
TOKEN = "0x6982508145454ce325ddbe47a25d4ec3d2311933"
POOL = "0xa43fe16908251ee70ef74718545e4fe6c5ccec9f"
CB = 17046105


def make_fetch(responses: dict):
    def fetch(url: str):
        if url in responses:
            v = responses[url]
            if isinstance(v, tuple):          # (status, body)
                return v
            return 200, json.dumps(v)
        raise AssertionError(f"未录制的请求：{url}")
    return fetch


def http_for(responses: dict) -> Http:
    return Http(fetch=make_fetch(responses), min_interval=0, retries=1, backoff=0)


def load_pepe() -> dict:
    with open(os.path.join(FIXTURES, "pepe", "responses.json"), encoding="utf-8") as f:
        return json.load(f)


def url_token(token=TOKEN):
    return f"https://eth.blockscout.com/api/v2/tokens/{token}"


def url_pairs(token=TOKEN):
    return f"https://api.dexscreener.com/latest/dex/tokens/{token}"


def url_addr(a):
    return f"https://eth.blockscout.com/api/v2/addresses/{a}"


def url_activity(a):
    return f"https://eth.blockscout.com/api/v2/addresses/{a}/token-transfers?type=ERC-20"


def url_goplus(token=TOKEN):
    return f"https://api.gopluslabs.io/api/v1/token_security/1?contract_addresses={token.lower()}"


def make_world(buyers=3, *, token=TOKEN, pool=POOL, cb=CB,
               token_meta=None, risk=None, activities=None, contract_buyers=0,
               logs_message="OK", pairs_ok=True):
    """构造一个最小可用的「世界」：N 个买家 + 各自活动。"""
    t = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
    t1 = "0x" + "0" * 24 + pool.lower()[2:]
    world = {}

    world[url_token(token)] = token_meta if token_meta is not None else {
        "symbol": "TEST", "name": "Test Token", "circulating_market_cap": "5000000",
        "holders_count": "1200", "decimals": "18"}

    world[url_pairs(token)] = {"pairs": ([{"chainId": "ethereum", "dexId": "uniswap",
                                           "pairAddress": pool,
                                           "liquidity": {"usd": 250000},
                                           "volume": {"h24": 90000},
                                           "marketCap": 5000000}] if pairs_ok else [])}

    world[url_addr(token)] = {"hash": token, "is_contract": True,
                              "creation_transaction_hash": "0xcreate"}
    world[f"https://eth.blockscout.com/api/v2/transactions/0xcreate"] = {"block_number": cb}

    logs = []
    for i in range(buyers):
        to = "0x" + "a" * 32 + f"{i + 1:08x}"
        logs.append({"blockNumber": hex(cb + i + 1), "transactionHash": f"0xtx{i}",
                     "topics": [t, "0x" + "0" * 24 + pool[2:], "0x" + "0" * 24 + to[2:]],
                     "timeStamp": "0x0"})
    world[f"https://eth.blockscout.com/api?module=logs&action=getLogs&fromBlock={cb}&toBlock={cb+3000}"
          f"&address={token}&topic0={t}&topic1={t1}&topic0_1_opr=and"] = {
        "status": "1", "message": logs_message, "result": logs}

    for i in range(buyers):
        to = "0x" + "a" * 32 + f"{i + 1:08x}"
        world[url_addr(to)] = {"hash": to, "is_contract": i < contract_buyers}
        world[url_activity(to)] = {"items": activities(i) if activities else [
            {"from": {"hash": "0xpool", "is_contract": True}, "to": {"hash": to},
             "token": {"address_hash": "0xt1", "symbol": "AAA"}, "timestamp": "2026-10-15T00:00:00.000000Z"}
            for _ in range(4)]}

    world[url_goplus(token)] = {"code": 1, "message": "OK", "result": {token.lower(): risk if risk is not None else {
        "buy_tax": "0", "sell_tax": "0", "cannot_sell_all": "0", "is_honeypot": "0",
        "is_blacklisted": "0", "is_mintable": "0", "can_take_back_ownership": "0",
        "owner_address": "0xowner", "creator_address": "0xcreator",
        "holder_count": "1200", "top_10_holder_rate": "0.2"}}}
    return world
