"""联网冒烟（@live，默认跳过）：每个数据源一次真实请求。跑法：pytest -q -m live"""
from __future__ import annotations

import json
import urllib.request

import pytest

PEPE = "0x6982508145454ce325ddbe47a25d4ec3d2311933"


def _get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": "onchain-smart-money/0.1",
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read().decode("utf-8", "replace"))


@pytest.mark.live
def test_live_blockscout():
    st, d = _get(f"https://eth.blockscout.com/api/v2/tokens/{PEPE}")
    assert st == 200 and d.get("symbol") == "PEPE"


@pytest.mark.live
def test_live_dexscreener():
    st, d = _get(f"https://api.dexscreener.com/latest/dex/tokens/{PEPE}")
    assert st == 200 and (d.get("pairs") is None or isinstance(d.get("pairs"), list))


@pytest.mark.live
def test_live_goplus():
    st, d = _get("https://api.gopluslabs.io/api/v1/token_security/1?contract_addresses=" + PEPE)
    assert st == 200 and d.get("code") == 1
