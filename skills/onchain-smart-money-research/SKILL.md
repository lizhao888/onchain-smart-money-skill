---
name: onchain-smart-money-research
description: Use when 从链上代币反查早期买家、验证钱包与风险。
---

# 链上聪明钱研究（只读 · 证据优先）

给一个 **EVM 代币合约地址**，产出一份可复核的报告：谁是早期真实买家、他们最近还在不在、这个币有没有致命风险。

**这个 Skill 不交易、不接触私钥、不预测涨跌。** 它只回答「我观察到了什么、证据在哪、哪些还不知道」。

## 什么时候用

- 「帮我查这个币的早期买家是谁」
- 「这些钱包现在还活跃吗 / 值得跟吗」
- 「这个币能不能安全退出（风险闸门）」
- 做研究或内容前，需要**可引用的证据**（tx hash / 区块 / 来源 / 时间）

**不适合**：查价格走势（用行情工具）、下单交易、多链跨链研究（当前只支持 Ethereum）。

## 代码在哪 / 怎么跑

- **仓库（源码 + 说明文档）**：`D:\hermes媒体运营\github\onchain-smart-money-skill`
- **本机解释器**：`D:\hermes媒体运营\github\onchain-smart-money-skill\.venv\Scripts\python.exe`
- 先在该目录执行一次 `pip install -e .`，之后任意目录都能用 `python -m onchain_sm.cli`

```bash
cd D:\hermes媒体运营\github\onchain-smart-money-skill
# 1. 体检数据源（三行，全 OK 才继续）
.venv/Scripts/python.exe -m onchain_sm.cli doctor

# 2. 跑一个代币
python -m onchain_sm.cli run --token 0x6982508145454ce325ddbe47a25d4ec3d2311933 --out out
```

输出：`out/report-<symbol>-<时间>.json` + `out/snapshots.jsonl`（供后续回测）。

## 五个节点与验收（每条都必须过）

| 节点 | 做什么 | 通过条件 | 失败时 |
|---|---|---|---|
| `token_identity` | 合约 ↔ 链 ↔ 池子 | 拿到 symbol + 最大流动性池 | `FAILED` → 整份报告 `BLOCKED` |
| `early_buyers` | 前 N 个「池子 → EOA」买入 | 每个买家带 tx hash；合约地址被跳过 | 取数失败 → `count=None`（**不是 0**） |
| `wallet_activity` | 近 30 天 swap / 转账 | 每个钱包有 `ACTIVE / LOW_FREQUENCY / DORMANT / UNKNOWN` | 全失败 → `FAILED`；部分失败 → `WARNING` |
| `risk_gate` | GoPlus 风险标签 | 输出 `blockers`；拿不到 → `verified=false` | 风险未知时**不得视为安全** |
| `report` | 评分 + 评级 | 有阻断项时 `final_grade=BLOCKED`，分数不可覆盖 | — |

## 硬规则（不可协商）

1. **失败不等于空**：`FAILED` 的节点，`metrics.count` 必须是 `null`，报告 `complete=false`。
2. **只认池子买入**：`Transfer` 的 `from` 必须是池子合约；普通地址转入不算买入，空投更不算。
3. **不伪造**：拿不到的字段写进 `unknowns`，不填 0、不猜。
4. **风险不可被分数覆盖**：有 `blockers` 就是 `BLOCKED`。
5. **不交易**：不签名、不调用 swap、不读私钥。

## 输出怎么读

```
report.json
├── steps{}        每步 status + metrics + evidence + unknowns
├── buyers[]       地址 / 区块 / tx_hash
├── activity[]     label / swap_30d / days_since
├── risk{}         blockers / taxes / holder_count（标注 C 级证据）
├── score          0–100（仅用于排序，不是胜率）
└── final_grade    BLOCKED | INCOMPLETE | RESEARCH | WATCH
```

**引用结论时**：每条都要能给出 `tx_hash` 或合约地址 + 数据源 + 抓取时间。

## 参考

- `references/workflow.md` — 每步的输入/动作/失败分支
- `references/data-sources.md` — 数据源与限额（含实测证据等级）
- `references/output-schema.md` — 报告字段定义
- 仓库根目录 `DATA_SOURCES.md` / `MVP_ACCEPTANCE.md` / `SELF_AUDIT.md` — 审计与验收依据
