"""数据契约：节点结果、证据、报告。"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

PASSED = "PASSED"
WARNING = "WARNING"
BLOCKED = "BLOCKED"
FAILED = "FAILED"

STATUSES = (PASSED, WARNING, BLOCKED, FAILED)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Evidence:
    kind: str                 # tx | pool | token | risk | price
    ref: str                  # tx hash / 合约地址 / URL
    source: str               # blockscout | dexscreener | goplus | rpc
    retrieved_at: str = field(default_factory=now_iso)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class NodeResult:
    step: str
    status: str = PASSED
    metrics: Dict[str, Any] = field(default_factory=dict)
    evidence: List[Evidence] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    unknowns: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def add_ev(self, kind: str, ref: str, source: str) -> None:
        self.evidence.append(Evidence(kind, ref, source))

    def fail(self, error: str) -> "NodeResult":
        self.status = FAILED
        self.errors.append(error)
        return self

    def as_dict(self) -> dict:
        return {
            "step": self.step, "status": self.status,
            "metrics": self.metrics,
            "evidence": [e.as_dict() for e in self.evidence],
            "errors": self.errors, "warnings": self.warnings,
            "unknowns": self.unknowns,
        }


@dataclass
class Report:
    chain: str
    token: str
    generated_at: str = field(default_factory=now_iso)
    steps: Dict[str, NodeResult] = field(default_factory=dict)
    token_meta: Dict[str, Any] = field(default_factory=dict)
    pool: Optional[str] = None
    buyers: List[Dict[str, Any]] = field(default_factory=list)
    activity: List[Dict[str, Any]] = field(default_factory=list)
    risk: Dict[str, Any] = field(default_factory=dict)
    score: Optional[float] = None
    final_grade: str = "INCOMPLETE"
    rules_version: str = "v0"

    @property
    def complete(self) -> bool:
        # 只有「没查到 / 查失败」才叫不完整；BLOCKED 是有效结论（风险高）
        return bool(self.steps) and not any(s.status == FAILED for s in self.steps.values())

    def as_dict(self) -> dict:
        return {
            "schema": "onchain-sm/v0.1",
            "chain": self.chain, "token": self.token, "pool": self.pool,
            "generated_at": self.generated_at, "rules_version": self.rules_version,
            "token_meta": self.token_meta,
            "steps": {k: v.as_dict() for k, v in self.steps.items()},
            "buyers": self.buyers, "activity": self.activity,
            "risk": self.risk, "score": self.score,
            "final_grade": self.final_grade, "complete": self.complete,
        }
