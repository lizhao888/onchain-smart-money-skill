"""Provider 公共工具。"""
from __future__ import annotations

from typing import Any, Dict, List

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


def pad_topic(addr: str) -> str:
    """地址 → 32 字节 topic（小写，用于 topic1 过滤）。"""
    return "0x" + "0" * 24 + addr.lower()[2:]


def decode_transfer(log: Dict[str, Any]) -> Dict[str, Any] | None:
    """从 getLogs 的 Transfer 事件里取 from / to / block。铸造返回 None。"""
    topics = log.get("topics") or []
    if len(topics) < 3:
        return None
    to = "0x" + topics[2][-40:]
    frm = "0x" + topics[1][-40:]
    if to.lower().startswith("0x00000000"):
        return None
    return {
        "from": frm.lower(),
        "to": to.lower(),
        "block": int(log.get("blockNumber", "0x0"), 16),
        "tx_hash": log.get("transactionHash"),
    }


def dedupe_by_tx(rows: List[Dict[str, Any]]) -> tuple[List[Dict[str, Any]], int]:
    """同一 tx 只保留一次（防重复交易）。返回 (去重后, 丢掉的数量)。"""
    seen, out = set(), []
    for r in rows:
        key = (r.get("tx_hash") or "") + ":" + str(r.get("to") or "")
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out, len(rows) - len(out)
