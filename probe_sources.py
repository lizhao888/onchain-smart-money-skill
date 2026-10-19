"""数据源探针：对全部依赖的免费源发真实请求，输出 evidence/probe_results.json。

用法：python probe_sources.py   （需外网，约 30 秒）
只读 GET，无密钥。结果附 HTTP 状态与耗时，用于 DATA_SOURCES.md 的证据更新。
"""
import json
import time
import urllib.request
import urllib.error

PROBES = [
    ("blockscout-token", "https://eth.blockscout.com/api/v2/tokens/0x6982508145454ce325ddbe47a25d4ec3d2311933"),
    ("blockscout-addr", "https://eth.blockscout.com/api/v2/addresses/0x6982508145454ce325ddbe47a25d4ec3d2311933"),
    ("dexscreener", "https://api.dexscreener.com/latest/dex/tokens/0x6982508145454ce325ddbe47a25d4ec3d2311933"),
    ("goplus-eth", "https://api.gopluslabs.io/api/v1/token_security/1?contract_addresses=0x6982508145454ce325ddbe47a25d4ec3d2311933"),
    ("goplus-bsc", "https://api.gopluslabs.io/api/v1/token_security/56?contract_addresses=0x0e09fabb73bd3ade0a17ecc321fd13a19e81ce82"),
    ("geckoterminal", "https://api.geckoterminal.com/api/v2/networks/bsc/new_pools?page=1"),
    ("bsc-rpc-blocknumber", "RPC:https://bsc.publicnode.com"),
    ("sol-rpc-gethealth", "SOL:https://api.mainnet-beta.solana.com"),
]


def probe(name, target):
    t0 = time.time()
    try:
        if target.startswith("RPC:"):
            body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []}).encode()
            req = urllib.request.Request(target[4:], data=body, headers={"Content-Type": "application/json"})
        elif target.startswith("SOL:"):
            body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "getHealth", "params": []}).encode()
            req = urllib.request.Request(target[4:], data=body, headers={"Content-Type": "application/json"})
        else:
            req = urllib.request.Request(target, headers={"User-Agent": "onchain-smart-money/0.1"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            return {"name": name, "status": resp.status, "ms": int((time.time() - t0) * 1000)}
    except urllib.error.HTTPError as e:
        return {"name": name, "status": e.code, "ms": int((time.time() - t0) * 1000), "error": "http"}
    except Exception as e:
        return {"name": name, "status": 0, "ms": int((time.time() - t0) * 1000), "error": type(e).__name__}


if __name__ == "__main__":
    results = [probe(n, t) for n, t in PROBES]
    out = {"probed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "results": results}
    print(json.dumps(out, ensure_ascii=False, indent=1))
    import os
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "evidence", "probe_results.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
