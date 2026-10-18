# 五节点工作流（MVP-S）

每个节点都遵循：**输入 → 动作 → 验收 → 失败分支 → 输出**。

## N1 token_identity
- 输入：`chain=ethereum`、`token`
- 动作：Blockscout `/api/v2/tokens/{ca}`；DEX Screener `/latest/dex/tokens/{ca}` 取最大流动性池
- 验收：`symbol` 非空；`pool` 非空
- 失败：token 失败 → FAILED；池子失败 → FAILED（没有池子无法判定买入）
- 输出：`token_meta`、`pool`、`liquidity_usd`

## N2 early_buyers
- 输入：`token`、`pool`、部署区块
- 动作：`getLogs`（topic0=Transfer，topic1=pool，`topic0_1_opr=and`），扫 `creation .. creation+scan_blocks`
- 验收：每个买家有 `tx_hash`；合约地址跳过并计数
- 失败：取数失败 → `count=null`（**禁止写 0**）；真空 → `count=0 + pages_exhausted=true`
- 输出：`buyers[]`

## N3 wallet_activity
- 输入：`buyers[]`
- 动作：`/api/v2/addresses/{addr}/token-transfers?type=ERC-20`
- 分类：`swap_activity`（from 是合约）与 `transfer_activity`（from 是 EOA）分开计数
- 验收：每个钱包有 label ∈ {ACTIVE, LOW_FREQUENCY, DORMANT, UNKNOWN}
- 失败：全部失败 → FAILED；部分失败 → WARNING + coverage
- 输出：`activity[]`

## N4 risk_gate
- 输入：`token`
- 动作：GoPlus `token_security`
- 验收：输出 `blockers[]`（CANNOT_SELL / BLACKLIST / HIGH_TAX / OWNERSHIP_TAKEOVER）
- 失败：拿不到 → `verified=false`，写进 `unknowns`，评分失去风险分
- 注意：GoPlus 是**第三方标签**（C 级证据），不是链上实证

## N5 report
- 评分：买家 25 + 活跃 25 + 流动性 25 + 风险 25（v0，冻结）
- 评级：有 blockers → `BLOCKED`；不完整 → `INCOMPLETE`；≥60 → `RESEARCH`；否则 `WATCH`
- 输出：JSON 报告 + JSONL 快照
