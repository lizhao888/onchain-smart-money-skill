# onchain-smart-money

**给一个 Ethereum 代币合约，回答三个问题：谁最早买的？他们还在不在？这个币能不能安全退出？**

只读研究工具。**不交易、不接触私钥、不预测涨跌。** 每一条结论都带交易哈希、数据源和抓取时间，可以自己复核。

```
代币合约 → 池子识别 → 最早 20 个「池子→钱包」买入 → 近 30 天活跃/休眠 → 风险闸门 → 带证据的报告
```

---

## 它不做什么（先看这段）

- ❌ 不自动交易，不签名，不读取私钥
- ❌ 不保证信号有效，`score` 只用于排序
- ❌ 不支持 Solana / BSC（Phase 3）
- ❌ 不会把「查不到」写成「没有」——失败就是 `FAILED`，不是 0

## 现在支持什么

| 项 | 状态 |
|---|---|
| 链 | **Ethereum 主网** |
| 数据源 | Blockscout（链上）、DEX Screener（池子/流动性）、GoPlus（风险标签）、公共 RPC（备份） |
| API Key | **不需要**（全部免费源） |
| 节点 | 5 个：identity / early_buyers / wallet_activity / risk_gate / report |
| 测试 | 17 个离线测试通过；9 个 Phase 2 场景显式 skip |

## 安装与快速开始

```bash
git clone <repo> && cd onchain-smart-money
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e . pytest

# 1) 体检（三个源都 OK 再往下）
python -m onchain_sm.cli doctor

# 2) 研究一个代币
python -m onchain_sm.cli run --token 0x6982508145454ce325ddbe47a25d4ec3d2311933 --max-buyers 20
```

产物：

```
out/report-PEPE-20261018-131500.json   # 完整报告（含每个节点的状态与证据）
out/snapshots.jsonl                    # 每次运行的快照（供后续回测）
```

## 报告长什么样

```json
{
  "steps": {
    "early_buyers": {"status": "PASSED", "metrics": {"count": 20, "dedup_dropped": 0},
                     "evidence": [{"kind": "tx", "ref": "0x…", "source": "blockscout"}]},
    "risk_gate":    {"status": "BLOCKED", "metrics": {"blockers": 1}}
  },
  "risk": {"blockers": ["CANNOT_SELL"]},
  "score": 62.5,
  "final_grade": "BLOCKED",
  "complete": true
}
```

**四条不变量**（由测试保证，不靠提示词）
1. 节点 `FAILED` ⇒ 该节点 `count` 为 `null`，不是 0
2. `risk.blockers` 非空 ⇒ `final_grade = BLOCKED`，分数不能覆盖
3. 只有「池子 → EOA」的 Transfer 才算买入（转账/空投不算）
4. 报告里每个买家都有可打开的 `tx_hash`

## 数据源与限制（实测 2026-10-18）

| 源 | 用途 | key | 已知限制 |
|---|---|---|---|
| Blockscout | 代币/转账/日志 | 否 | 无公开限流文档；实测突发会 429 |
| DEX Screener | 池子、流动性 | 否 | 核心端点限额未成文 |
| GoPlus | 风险标签（C 级证据） | 否 | 限额未公布；标签非链上实证 |
| GeckoTerminal | OHLCV（Phase 2） | 否 | **约 10 次/分钟** |

详见 [`DATA_SOURCES.md`](DATA_SOURCES.md)（每条断言都标了证据等级 A/B/C/D）。

## 目录

```
src/onchain_sm/           # 可复用库（providers / pipeline / cli）
skills/onchain-smart-money-research/   # Agent Skill 入口（SKILL.md + references）
tests/                    # 离线测试 + 真实响应夹具
DATA_SOURCES.md           # 数据源核实报告
MVP_ACCEPTANCE.md         # 16 个验收场景与断言
SELF_AUDIT.md             # 自我审计（含已修正的错误）
IMPLEMENTATION_PLAN.md    # 阶段划分与风险
```

## 测试

```bash
pytest -q                 # 离线，全部通过才算数
pytest -q -m live         # 联网冒烟（默认跳过）
```

## 路线图

- **Phase 2**：快照回测、关联钱包（资金源）、共识（跨币累计）、空投识别、分页
- **Phase 1.5**：GMGN 增强源（需 API Key，支持 ETH）
- **Phase 3**：Solana / BSC

## 许可证

Apache-2.0（允许闭源商用，含专利授权条款）。
