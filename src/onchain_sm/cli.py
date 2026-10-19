"""命令行入口：只读研究工具。

用法：
  python -m onchain_sm.cli run --token 0x... [--chain ethereum|bsc|solana] [--out out]
  python -m onchain_sm.cli watch --token 0x... --chain bsc   # 7步法：幸存者画像+共买信号
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


def cmd_watch(args) -> int:
    """7 步法端到端：run 5 节点 → 剔机器人 → 幸存者画像 → 共买信号 → watch 报告。"""
    from .pipeline import botfilter, consensus, survivor_portrait

    http = Http(min_interval=args.min_interval)
    rep = run_scan(http, args.token, chain=args.chain,
                   scan_blocks=args.scan_blocks, max_buyers=args.max_buyers)
    out_dir = args.out
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    symbol = (rep.token_meta or {}).get("symbol") or args.token[:8]

    # --- P2-4：剔机器人（UNKNOWN 不剔除也不入幸存者，单独归 pending） ---
    survivors, excluded, pending = [], [], []
    for a in rep.activity:
        bot = botfilter.classify_bot(
            tx_times=a.get("_tx_times") or [], tx_blocks=a.get("_tx_blocks") or [],
            swap_30d=a.get("swap_30d"))
        row = {**a, "bot": bot}
        if bot["label"] in ("HUMAN", "BOT_SUSPECT"):
            survivors.append(row)
        elif bot["label"] == "LIKELY_BOT":
            excluded.append(row)
        else:
            pending.append(row)                       # UNKNOWN：证据不足，不妄断
    human_wallets = [r["address"] for r in survivors if r["bot"]["label"] == "HUMAN"]

    # --- P2-5：幸存者画像（仅 HUMAN；SOL 暂不支持画像，显式标注） ---
    portraits, unknowns, failed = [], [], []
    if args.chain == "solana":
        from .pipeline import survivor_portrait_sol
        sol_portraits, sol_unknowns, sol_failed = [], [], []
        for w in human_wallets[:3]:                    # R10：单轮限 3 钱包（RPC 成本）
            buys, err = survivor_portrait_sol.wallet_recent_buys_sol(http, w, days=args.survivor_window)
            if err:
                sol_failed.append({"wallet": w, "error": err})
                continue
            buys.sort(key=lambda x: x.get("first_buy_at") or "", reverse=True)
            sol_portraits.append({"wallet": w, "recent_buys": buys,
                                  "buy_token_count": len(buys)})
        portraits = sol_portraits
        unknowns.extend(sol_unknowns)
        failed.extend(sol_failed)
        if not human_wallets:
            unknowns.append("无 HUMAN 幸存者，SOL 画像跳过")
    elif not human_wallets:
        unknowns.append("无 HUMAN 幸存者（全部被机器人筛剔除或 UNKNOWN），画像跳过")
    else:
        portraits, unknowns, failed = survivor_portrait.portrait(
            http, human_wallets, days=args.survivor_window, max_wallets=args.max_portraits)

    # --- P2-6：共买信号 ---
    signals = consensus.build_signals(portraits, k=args.min_buyers,
                                      window_days=args.window_days)

    # --- P2-7：评分接线（信号加成封顶 +10） ---
    base_score = rep.score or 0.0
    bonus = round(min((max((s["weight"] for s in signals), default=0.0)) * 10, 10), 1)
    score = round(base_score + bonus, 1)

    payload = {
        "schema": "onchain-sm/watch-v0.1",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "chain": args.chain, "token": rep.token,
        "base_report": {k: v for k, v in rep.as_dict().items()},
        "survivors": [{"wallet": r["address"], "label": r["bot"]["label"],
                       "bot_score": r["bot"]["score"], "evidence": r["bot"]["evidence"]}
                      for r in survivors],
        "pending_unknown": [{"wallet": r["address"], "evidence": r["bot"]["evidence"]}
                            for r in pending],
        "excluded_bots": [{"wallet": r["address"], "label": r["bot"]["label"],
                           "bot_score": r["bot"]["score"], "evidence": r["bot"]["evidence"]}
                          for r in excluded],
        "portraits": portraits,
        "signals": signals,
        "score": score, "base_score": rep.score, "signal_bonus": bonus,
        "final_grade": rep.final_grade,
        "unknowns": unknowns,
        "failed_wallets": failed,
    }
    path = os.path.join(out_dir, f"watch-{symbol}-{stamp}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    snap = os.path.join(out_dir, "snapshots.jsonl")
    with open(snap, "a", encoding="utf-8") as f:
        f.write(json.dumps({"snapshot_at": payload["generated_at"], "kind": "watch",
                            "token": rep.token, "signals": len(signals),
                            "score": score, "report": path}, ensure_ascii=False) + "\n")

    print(f"代币 {symbol or args.token}  基础分 {rep.score} + 信号加成 {bonus} = {score}")
    print(f"  幸存者: {len(payload['survivors'])} HUMAN/SUSPECT | 剔除机器人: {len(payload['excluded_bots'])}")
    for p in portraits:
        print(f"  画像 {p['wallet'][:12]}… 近期买入 {p['buy_token_count']} 个币")
    if signals:
        for s in signals[:5]:
            print(f"  信号! {s['token'][:14]}… {s['buyer_count']} 钱包同买 "
                  f"({s['estimated_entities']} 主体) 持有 {s['still_holding_count']} weight={s['weight']}")
    else:
        print("  无共买信号（未达 K 个幸存钱包同买）")
    for u in unknowns[:4]:
        print(f"  unknown: {u}")
    print(f"报告: {path}")
    return 0 if rep.complete else 1


def cmd_scan(args) -> int:
    """发射台扫描（7 步法第 1 步自动化）：全网新池 → 优质 meme 候选。"""
    from .pipeline import scanner
    http = Http(min_interval=1.0)
    r = scanner.scan_new_memes(http, chain=args.chain, top_n=args.top,
                               min_liq=args.min_liq, max_liq=args.max_liq,
                               min_tx24=args.min_tx24, max_age_days=args.max_age)
    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, f"scan-{args.chain}-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False, indent=2)
    print(f"链 {args.chain} | 扫描 {r['scanned']} 池 | 合格 {r['qualified']} | Top {len(r['picked'])}")
    for i, c in enumerate(r["picked"], 1):
        print(f"  {i}. {c['symbol']:14s} {c['addr']}")
        print(f"     liq=${c['liq']:,.0f} tx24={c['h24']} age={c['age_days']}d lp={c['launchpad']} score={c['quality_score']}")
    print("⚠️ 候选需经 `run`/`watch` 深度验证（DS 数据有失真案例）后才能采信")
    print(f"报告: {path}")
    return 0


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
    SUPPORTED_CHAINS = ("ethereum", "bsc", "solana")   # 数据源按链绑定
    r = sub.add_parser("run", help="对一个代币跑 MVP-S 五节点")
    r.add_argument("--token", required=True)
    r.add_argument("--chain", default="ethereum",
                   help=f"仅支持: {', '.join(SUPPORTED_CHAINS)}")
    r.add_argument("--scan-blocks", type=int, default=3000)
    r.add_argument("--max-buyers", type=int, default=20)
    r.add_argument("--min-interval", type=float, default=1.0)
    r.add_argument("--out", default="out")
    r.set_defaults(func=cmd_run, chains=SUPPORTED_CHAINS)

    d = sub.add_parser("doctor", help="检查三个数据源是否可用")
    d.set_defaults(func=cmd_doctor)

    w = sub.add_parser("watch", help="7步法：跑完 5 节点后剔机器人、画幸存者像、找共买信号")
    w.add_argument("--token", required=True)
    w.add_argument("--chain", default="ethereum",
                   help=f"仅支持: {', '.join(SUPPORTED_CHAINS)}")
    w.add_argument("--scan-blocks", type=int, default=3000)
    w.add_argument("--max-buyers", type=int, default=20)
    w.add_argument("--min-interval", type=float, default=1.0)
    w.add_argument("--out", default="out")
    w.add_argument("--survivor-window", type=int, default=30, help="幸存者画像窗口（天）")
    w.add_argument("--max-portraits", type=int, default=5, help="最多画像几个钱包（限流保护）")
    w.add_argument("--min-buyers", type=int, default=3, help="共买信号触发的最少钱包数 K")
    w.add_argument("--window-days", type=int, default=7, help="共买时间窗口（天）")
    w.set_defaults(func=cmd_watch, chains=SUPPORTED_CHAINS)

    s = sub.add_parser("scan", help="发射台扫描：全网新池 → 优质 meme 候选（7步法第1步自动化）")
    s.add_argument("--chain", default="bsc", help=f"仅支持: {', '.join(SUPPORTED_CHAINS)}")
    s.add_argument("--top", type=int, default=10, help="输出前 N 个候选")
    s.add_argument("--min-liq", type=float, default=3000)
    s.add_argument("--max-liq", type=float, default=2_000_000)
    s.add_argument("--min-tx24", type=int, default=10)
    s.add_argument("--max-age", type=float, default=14, help="最大池龄（天）")
    s.add_argument("--out", default="out")
    s.set_defaults(func=cmd_scan, chains=SUPPORTED_CHAINS)

    a = ap.parse_args(argv)
    chains = getattr(a, "chains", None)
    if chains and getattr(a, "chain", None) and a.chain not in chains:
        ap.error(f"不支持的链 '{a.chain}'：当前仅支持 {', '.join(chains)}"
                 f"（风险数据源按链绑定，错误链会给出错误结论，故直接拒绝）")
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
