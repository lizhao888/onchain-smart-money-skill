"""机器人识别（P2-4，对应原方法第 4 步「把机器人钱包筛掉」）。

多特征打分，不搞一票否决；输出 label + 证据，被剔除钱包保留证据供复核。

  交易间隔中位数 <10s            → +3 分
  同块多笔（同 block/slot）      → +2 分
  30 天交易笔数 >500             → +2 分
  金额高度整齐（CV<0.05）        → +1 分
  合计 ≥4 → LIKELY_BOT（剔除）
  2~3   → BOT_SUSPECT（保留但降权 0.5）
  其余   → HUMAN

这是启发式：不声称识别全部机器人；数据不足 → UNKNOWN（禁止默认 HUMAN）。
"""
from __future__ import annotations

from typing import Dict, List, Optional

# 阈值（v0，待回测验证后版本化）
INTERVAL_MEDIAN_BOT = 10          # 秒
MANY_TX_30D = 500
AMOUNT_CV_TIGHT = 0.05
SCORE_LIKELY = 4
SCORE_SUSPECT = 2
SUSPECT_WEIGHT = 0.5


def classify_bot(tx_times: List[int], tx_amounts: Optional[List[float]] = None,
                 tx_blocks: Optional[List[int]] = None,
                 swap_30d: Optional[int] = None) -> Dict:
    """输入一个钱包的交易时间戳（秒，倒序或乱序均可）、金额、区块、30天swap数。

    返回 {label, score, evidence[], weight_factor}。
    weight_factor: HUMAN=1.0, BOT_SUSPECT=0.5, LIKELY_BOT/UNKNOWN 不参与共识。
    """
    ev: List[str] = []
    score = 0
    times = sorted(t for t in (tx_times or []) if t)
    if len(times) < 3:
        return {"label": "UNKNOWN", "score": 0,
                "evidence": ["交易笔数<3，无法判定"], "weight_factor": 0.0}

    intervals = [b - a for a, b in zip(times, times[1:]) if b > a]
    if intervals:
        intervals.sort()
        med = intervals[len(intervals) // 2]
        if med < INTERVAL_MEDIAN_BOT:
            score += 3
            ev.append(f"间隔中位数 {med}s < {INTERVAL_MEDIAN_BOT}s (+3)")

    if tx_blocks:
        from collections import Counter
        dup = sum(c - 1 for c in Counter(tx_blocks).values() if c > 1)
        if dup >= 2:
            score += 2
            ev.append(f"同块多笔交易 {dup} 组 (+2)")

    if swap_30d is not None and swap_30d > MANY_TX_30D:
        score += 2
        ev.append(f"30天交易 {swap_30d} 笔 > {MANY_TX_30D} (+2)")

    if tx_amounts and len(tx_amounts) >= 5:
        mean = sum(tx_amounts) / len(tx_amounts)
        if mean > 0:
            var = sum((a - mean) ** 2 for a in tx_amounts) / len(tx_amounts)
            cv = (var ** 0.5) / mean
            if cv < AMOUNT_CV_TIGHT:
                score += 1
                ev.append(f"金额高度整齐 CV={cv:.3f} (+1)")

    if score >= SCORE_LIKELY:
        label = "LIKELY_BOT"
        wf = 0.0
    elif score >= SCORE_SUSPECT:
        label = "BOT_SUSPECT"
        wf = SUSPECT_WEIGHT
    else:
        label = "HUMAN"
        wf = 1.0
    if not ev:
        ev.append("无机器人特征命中")
    return {"label": label, "score": score, "evidence": ev, "weight_factor": wf}
