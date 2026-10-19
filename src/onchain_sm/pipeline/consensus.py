"""共买信号（P2-6，对应原方法第 6 步——「多个钱包同时买同一个项目就是信号」）。

纯函数：输入 = P2-5 幸存者画像的 recent_buys 集合，输出 = 共买信号列表。

signal = {token, buyers:[{wallet, first_buy_at}], window_days,
          still_holding_count, estimated_entities, weight}

参数（配置化，默认待回测验证）：
  K=3    至少几个幸存钱包同买才触发（原方法口径「三个不同钱包」）
  D=7    时间窗口（天）
weight = 人数×0.4 + 全部仍持有×0.3 + 时间集中度×0.3   （0~1，只用于排序）

红线：
- 只统计真实买入（P2-5 已排除空投/转账）；
- 地址数 ≠ 独立主体数：输出 estimated_entities（同资金源一层合并由调用方传入）；
- 信号 ≠ 买入建议，回测验证前只用于研究排序。
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

K_DEFAULT = 3
WINDOW_DAYS_DEFAULT = 7


def _iso(t: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(t.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def build_signals(portraits: List[dict], k: int = K_DEFAULT,
                  window_days: int = WINDOW_DAYS_DEFAULT,
                  wallet_entities: Optional[Dict[str, int]] = None) -> List[dict]:
    """portraits: survivor_portrait.portrait() 的输出。

    wallet_entities: {wallet: entity_id}（同资金源合并结果，可缺省=各钱包独立）。
    """
    wallet_entities = wallet_entities or {}
    # token → 参与钱包集合
    by_token: Dict[str, List[dict]] = {}
    for p in portraits:
        w = p.get("wallet")
        for b in p.get("recent_buys") or []:
            by_token.setdefault(b["token"], []).append({
                "wallet": w, "first_buy_at": b.get("first_buy_at"),
                "still_holding": b.get("still_holding")})

    signals: List[dict] = []
    now = datetime.now().astimezone()
    for token, entries in by_token.items():
        # 同一钱包去重（保留最早首买）
        best: Dict[str, dict] = {}
        for e in entries:
            w = e["wallet"]
            if w not in best or (e["first_buy_at"] or "") < best[w]["first_buy_at"]:
                best[w] = e
        uniq = list(best.values())
        if len(uniq) < k:
            continue

        times = [_iso(e["first_buy_at"]) for e in uniq]
        times = [t for t in times if t]
        if len(times) < k:
            continue                                  # 时间解析不足，保守不触发
        span_days = (max(times) - min(times)).total_seconds() / 86400
        if span_days > window_days:
            continue                                  # 窗口外

        still = sum(1 for e in uniq if e.get("still_holding") is True)
        # 独立主体数：一层合并（不同钱包可能同实体）
        ents = {wallet_entities.get(e["wallet"], hash(e["wallet"])) for e in uniq}
        est_entities = len(ents)
        if est_entities < k:
            # 同一主体的多个地址刷票 → 降权但不完全否决
            continue

        # 时间集中度：首买全部落在 48h 内给满分，线性衰减
        concentration = max(0.0, 1.0 - (span_days / 2.0)) if span_days <= 2 else 0.0
        holding_ratio = still / len(uniq)
        weight = round(0.4 * (len(uniq) / max(k, len(uniq))) +
                       0.3 * holding_ratio + 0.3 * concentration, 3)

        signals.append({
            "token": token,
            "buyers": [{"wallet": e["wallet"], "first_buy_at": e["first_buy_at"],
                        "still_holding": e.get("still_holding")} for e in uniq],
            "buyer_count": len(uniq),
            "estimated_entities": est_entities,
            "window_days": round(span_days, 2),
            "still_holding_count": still,
            "weight": weight,
        })

    signals.sort(key=lambda s: s["weight"], reverse=True)
    return signals
