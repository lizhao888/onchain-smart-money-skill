"""命令行入口：只读研究工具。

用法：
  python -m onchain_sm.cli run --token 0x... [--chain ethereum] [--out out]
  python -m onchain_sm.cli doctor
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

from .http import Http
from .pipeline.run import run_scan


def cmd_run(args) -> int:
    http = Http(min_interval=args.min_interval)
    rep = run_scan(http, args.token, chain=args.chain,
                   scan_blocks=args.scan_blocks, max_buyers=args.max_buyers)
    out_dir = args.out
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    symbol = (rep.token_meta or {}).get("symbol") or args.token[:8]
    path = os.path.join(out_dir, f"report-{symbol}-{stamp}.json")
    payload = rep.as_dict()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    # 快照：不可变追加，供后续回测
    snap = os.path.join(out_dir, "snapshots.jsonl")
    with open(snap, "a", encoding="utf-8") as f:
        f.write(json.dumps({"snapshot_at": payload["generated_at"],
                            "token": rep.token, "grade": rep.final_grade,
                            "score": rep.score, "report": path}, ensure_ascii=False) + "\n")

    print(f"代币 {symbol or args.token}  池子 {(rep.pool or '-')[:12]}…")
    for name, step in rep.steps.items():
        print(f"  {name:15s} {step.status:8s} {step.metrics}")
    print(f"  最终评级: {rep.final_grade}  分数: {rep.score}  complete={rep.complete}")
    print(f"报告: {path}")
    failed = any(st.status == "FAILED" for st in rep.steps.values())
    return 1 if failed else 0


def cmd_doctor(args) -> int:
    http = Http()
    print("onchain-smart-money doctor")
    checks = [
        ("blockscout", "https://eth.blockscout.com/api/v2/tokens/0x6982508145454ce325ddbe47a25d4ec3d2311933"),
        ("dexscreener", "https://api.dexscreener.com/latest/dex/tokens/0x6982508145454ce325ddbe47a25d4ec3d2311933"),
        ("goplus", "https://api.gopluslabs.io/api/v1/token_security/1?contract_addresses=0x6982508145454ce325ddbe47a25d4ec3d2311933"),
    ]
    ok = True
    for name, url in checks:
        r = http.get_json(url)
        print(f"  {name:12s} {r.status:16s} http={r.http_status} attempts={r.attempts}")
        ok = ok and r.ok
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="onchain-sm")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="对一个代币跑 MVP-S 五节点")
    r.add_argument("--token", required=True)
    r.add_argument("--chain", default="ethereum")
    r.add_argument("--scan-blocks", type=int, default=3000)
    r.add_argument("--max-buyers", type=int, default=20)
    r.add_argument("--min-interval", type=float, default=1.0)
    r.add_argument("--out", default="out")
    r.set_defaults(func=cmd_run)

    d = sub.add_parser("doctor", help="检查三个数据源是否可用")
    d.set_defaults(func=cmd_doctor)

    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
