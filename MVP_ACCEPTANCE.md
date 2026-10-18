# MVP_ACCEPTANCE.md — 可重复执行的验收方案

> 目标：让「不伪造成功结论」这件事**由测试证明**，而不是由提示词保证。
> 前提：所有测试默认**离线**（用录制的 HTTP 夹具），联网测试单独标记 `@pytest.mark.live`。
> 运行方式：`pytest -q -m "not live"`（CI 必跑）／ `pytest -q -m live`（本地手动，需网络）

---

## 0. 验收分层

| 层 | 内容 | 判定 |
|---|---|---|
| L1 单元 | 纯函数：交易分类、去重、评分、状态机 | 输入→输出与期望完全一致 |
| L2 契约 | Provider 返回结构、字段类型、必填项 | JSON Schema 校验通过 |
| L3 场景 | 12 个业务场景（见 §2） | 状态 + 关键字段符合期望 |
| L4 端到端 | 3 个真实代币跑完整管线（离线夹具） | 报告可从证据复核，异常按预期降级 |
| L5 联网冒烟 | 每个 Provider 一次真实请求 | 200 或明确错误码；**不作为 CI 门槛** |

---

## 1. 全局验收标准（任何一次运行都必须满足）

| # | 标准 | 机器判定方式 |
|---|---|---|
| A1 | 每个节点都有 `status` | 报告 JSON 里 10 个节点全部存在且 ∈ 枚举 |
| A2 | 关键结论都有证据引用 | `evidence[]` 非空，且每条含 `ref/source/retrieved_at` |
| A3 | 取数失败不得变成空结果 | `status=FAILED` 时 `metrics` 不得为 0 计数成功 |
| A4 | 真空必须可证明 | `status=PASSED` 且 `count=0` 时，必须带 `pages_exhausted=true` |
| A5 | 关联钱包不得直接当独立主体 | 报告必须同时给出 `raw_addresses` 与 `estimated_entities` |
| A6 | 硬风险不得被总分覆盖 | 存在 `BLOCKED` 风险项时，`final_grade` 必须为 `BLOCKED` |
| A7 | 时间不泄漏 | 所有用于回测的字段 `retrieved_at <= signal_time` |
| A8 | 不接触私钥 | 代码里不存在签名/转账调用（静态检查 grep `sendTransaction|signTransaction|private_key`） |

---

## 2. 场景矩阵（12 个必测场景）

| ID | 场景 | 夹具 | 期望状态 | 期望关键结果 | 反例（不许出现） |
|---|---|---|---|---|---|
| S1 | 历史赢家，数据完整 | `fixtures/winner_full/` | PASSED | 20 个买家、每人 1 条 tx 证据 | 少于 20 却声称 20 |
| S2 | 同名不同合约 | `fixtures/same_symbol/` | PASSED | 以合约地址为准，报告标注 symbol 冲突 | 按 symbol 合并两个币 |
| S3 | 交易列表混有转账/空投/加池 | `fixtures/mixed_actions/` | PASSED | `verified_buys=N`，`dropped={transfer:X, airdrop:Y, lp:Z}` | 把转账计入买家 |
| S4 | 近 30 天只有转账、无 swap | `fixtures/transfer_only/` | PASSED | `activity=DORMANT`，`swap_30d=0`，`transfer_30d>0` | 判成 ACTIVE |
| S5 | 交易极快但证据不足 | `fixtures/fast_but_thin/` | WARNING | `bot=LIKELY_BOT_UNCONFIRMED`，降权不删除 | 一票否决删除 |
| S6 | 三个钱包同一资金来源 | `fixtures/related_wallets/` | PASSED | `raw_addresses=3, estimated_entities=1` | 计为 3 票共识 |
| S7 | 多个独立钱包不同时间买入 | `fixtures/independent_consensus/` | PASSED | 3 个实体；分窗口（1h/6h/24h）分别给共识度 | 只给一个模糊结论 |
| S8 | 合约有严重风险（不可卖/高税） | `fixtures/risky_token/` | **BLOCKED** | `risk.blockers=[CANNOT_SELL]`，`final_grade=BLOCKED` | 高分覆盖阻断 |
| S9 | 数据源超时/429/500 | `fixtures/source_failure/` | FAILED | 记录 `attempts`、`error_type`；**不得**输出空列表当成功 | `count=0` 且 PASSED |
| S10 | 两个源结果冲突 | `fixtures/conflict/` | WARNING | 同时列出两个值 + `delta_pct`，标 `CONFLICT` | 取平均或静默选一个 |
| S11 | 钱包把仓位转到另一个地址 | `fixtures/position_transfer/` | PASSED | `holding=PARTIALLY_VERIFIED`，标注转出地址 | 判成「已清仓卖出」 |
| S12 | 采集时间晚于回测信号时间 | `fixtures/lookahead/` | **FAILED** | 报错并剔除该字段 | 静默使用未来数据 |

补充（必测但非编号）：

| ID | 场景 | 期望 |
|---|---|---|
| S13 | 空投伪装共识（上一轮真实踩到） | 同一发送方给 ≥3 个钱包发同一代币 → `airdrop_suspect=true`，不计入共识 |
| S14 | 重复交易（同一 tx 多次出现） | 按 `tx_hash` 去重，`dedup_dropped` 计数 |
| S15 | 分页未扫完 | `status=WARNING`，`pages_exhausted=false` |
| S16 | 字段缺失（返回 200 但没有 `pairs`） | `status=FAILED`（契约不符），不是空结果 |

---

## 2bis. 可执行测试规格（每条规则对应一个测试函数）

> 规则不能只写在文档里，必须有函数名、夹具、断言。下表就是 `tests/` 的施工图。

### 夹具结构（每个场景一个目录）

```
tests/fixtures/<scenario>/
├── request.json      # 被测代码将发出的请求（用于桩匹配）
├── response.json     # 录制的真实响应（或人工构造的边界响应）
├── meta.json         # {"recorded_at": "...", "source": "blockscout", "http_status": 200}
└── expected.json     # 期望输出（只写关键字段，避免整文件比对脆断）
```

桩实现：`tests/conftest.py` 里的 `fake_http` 按 `(method, url)` 匹配 `request.json` → 返回 `response.json`；未命中即 `raise AssertionError("未录制的请求")`（**防止偷偷联网**）。

### 测试清单

| 测试函数 | 夹具 | 断言（具体到字段） |
|---|---|---|
| `test_s1_full_winner` | `winner_full/` | `r.status=="PASSED"` 且 `len(r.buyers)==20` 且 `all(b.tx_hash for b in r.buyers)` |
| `test_s2_same_symbol` | `same_symbol/` | `r.token.address=="0x..."`（以地址为准）；`r.warnings` 含 `SYMBOL_CONFLICT` |
| `test_s3_mixed_actions` | `mixed_actions/` | `r.metrics.verified_buys==N`；`r.metrics.dropped=={"transfer":X,"airdrop":Y,"lp":Z}`；`verified+dropped==total` |
| `test_s4_transfer_only` | `transfer_only/` | `r.activity.label=="DORMANT"`；`r.activity.swap_30d==0`；`r.activity.transfer_30d>0` |
| `test_s5_fast_thin` | `fast_but_thin/` | `r.wallet[0].bot=="BOT_SUSPECT"`（不是 `LIKELY_BOT`）；`r.wallet[0].weight_factor<1`；该钱包仍在列表里 |
| `test_s6_related_three` | `related_wallets/` | `r.consensus.raw_addresses==3`；`r.consensus.estimated_entities==1`；`evidence` 含共同资金源 tx |
| `test_s7_independent` | `independent_consensus/` | `estimated_entities==3`；`windows` 同时含 `1h/6h/24h` 三个键 |
| `test_s8_risky` | `risky_token/` | `r.risk.blockers` 含 `CANNOT_SELL`；`r.final_grade=="BLOCKED"`；**且** `r.score` 仍被计算但不展示为可推进 |
| `test_s9_source_failure` | `source_failure/` | `r.steps.early_buyers.status=="FAILED"`；`r.steps.early_buyers.metrics.count is None`（**不是 0**）；`r.attempts>=3` |
| `test_s10_conflict` | `conflict/` | `r.warnings` 含 `SOURCE_CONFLICT`；`r.market.mcap.values==[v1,v2]`；`delta_pct>0.3` |
| `test_s11_position_transfer` | `position_transfer/` | `r.holding=="PARTIALLY_VERIFIED"`；`r.transfers_out` 非空；不得出现 `"SOLD"` |
| `test_s12_lookahead` | `lookahead/` | 抛 `LookaheadError` 或 `r.status=="FAILED"` 且 `r.errors` 含 `FUTURE_DATA` |
| `test_s13_airdrop_consensus` | `edge_airdrop/` | `r.consensus.excluded` 含该币；`reason=="SINGLE_SENDER_MULTI_WALLET"`；`estimated_entities` 不计入 |
| `test_s14_duplicate_tx` | `dup_tx/` | `r.metrics.dedup_dropped==k`；`len(set(tx_hash))==len(tx_hash)` |
| `test_s15_partial_paging` | `partial_paging/` | `status=="WARNING"`；`pages_exhausted is False` |
| `test_s16_missing_field` | `missing_pairs/` | `status=="FAILED"`；`error_code=="SCHEMA_MISMATCH"`（**不得**变成空结果） |

### 4 个「元规则」门禁测试（防自欺）

| 测试 | 做法 | 失败即视为严重缺陷 |
|---|---|---|
| `test_g1_no_fake_success` | 参数化跑**所有** `edge_failure/*` 夹具：断言 `status=="FAILED"` 时 `metrics.count is None` 且 `report.complete is False` | 任何一次把失败写成 0 都算失败 |
| `test_g2_zero_vs_failure` | 对比 `empty_confirmed/`（真的没有记录）与 `failure_empty/`（超时后返回空）：前者 `PASSED + count==0 + pages_exhausted`，后者 `FAILED + count is None` | 两者输出必须不同 |
| `test_g3_no_lookahead` | 对快照里的每个字段断言 `retrieved_at <= signal_time`；构造一条 `retrieved_at > signal_time` 的数据，必须被剔除 | 泄漏一条即失败 |
| `test_g4_report_schema` | 用 `output-schema.json` 校验报告；`additionalProperties=false` | 字段缺失/多余都失败 |

### 评分与规则的「可回归」测试

| 测试 | 断言 |
|---|---|
| `test_golden_score` | 对 3 个固定输入，分数逐位相等（golden file）；权重表变更必须同步改 golden，并在 PR 里说明 |
| `test_rule_version_pinned` | 报告里 `rules_version` 必须存在于 `rules/` 目录，且 `sha256` 匹配 |
| `test_blocker_overrides_score` | 构造「95 分 + 不可卖」样本 → `final_grade` 必须是 `BLOCKED` |

---

## 3. 关键判定规则（写进代码，不靠提示词）

### 3.1 真假买入

```text
真买入 = Transfer(from == pool_address, to == EOA) 且 tx 中该 EOA 的该代币净增加 > 0
       或  Pool.Swap 事件中 recipient == EOA 且 amount0In/amount1In 方向为「支付计价币换取代币」

非买入（必须剔除并计数）：
  空投   —— from 是合约且同一 from 给 ≥3 个不同钱包发过同一代币
  转账   —— from 是 EOA
  加/撤池 —— from/to 是 pair 合约自身
  铸造   —— from == 0x0000...0
```

### 3.2 真空 vs 失败

| 条件 | 结论 |
|---|---|
| 200 + schema 合法 + 分页到底 + 条目为 0 | `EMPTY_CONFIRMED` → `PASSED` |
| 200 + 有 `next_page_params` 但未继续翻 | `PARTIAL` → `WARNING` |
| 429 / 5xx / 超时 / schema 不符 | `FAILED` → 下游必须 `UNKNOWN` |
| 200 但关键字段缺失（如 `pairs: null`） | `FAILED`（契约不符） |

### 3.3 关联钱包（MVP 只做一层）

```text
evidence:
  - 共同资金源：两地址的首笔入金来自同一地址（权重 0.6）
  - 直接互转：两地址之间存在 ≥1 笔代币/原生转账（权重 0.3）
  - 同步行为：同一分钟内买入同一代币 ≥2 次（权重 0.1）

estimated_entities = 连通分量数（阈值 ≥0.6 才合并）
```
输出必须写「疑似共同控制」，不得写「同一人」。

### 3.4 机器人

不设一票否决，输出概率与证据：
```text
LIKELY_BOT      : 间隔中位数 <10s 且 30 天 >500 笔
SNIPER_LIKE     : 新池后首块/次块买入，且持仓 <10 分钟
BOT_SUSPECT     : 间隔中位数 <60s 或 金额高度整齐（只降权）
ORDINARY_ACTIVE : 其余且 swap_30d ≥ 3
UNKNOWN         : 数据不足（禁止默认归为 ORDINARY）
```

### 3.5 评分与阻断

```text
if risk.blockers 非空: final_grade = BLOCKED（分数照算但不展示为可推进）
if data_quality < 0.6: 输出「低置信度」水印，禁止进入候选清单
```
权重 v0（冻结，改动走版本流程）：钱包质量 25 / 共识 20 / 合约结构 20 / 流动性 20 / 市场活跃 10 / 数据质量 5。

---

## 4. 端到端验收（L4）

**样本**：3 个真实代币（1 个赢家、1 个普通、1 个失败），全部录制为离线夹具。

| 检查项 | 通过标准 |
|---|---|
| 报告可复核 | 随机抽 5 条结论，都能用 `ref` 在链上浏览器打开并看到对应交易 |
| 状态完备 | 10 个节点全部有 status，无 `null` |
| 未知项显式 | `unknowns[]` 非空（至少有 PnL 不可算这一条） |
| 阻断生效 | 风险样本必须 `BLOCKED` |
| 可重复 | 同一夹具跑两次，除时间戳外输出完全一致（`--seed` 固定） |

---

## 5. 联网冒烟（L5，不进 CI）

| Provider | 请求 | 通过标准 |
|---|---|---|
| Blockscout | `/api/v2/tokens/{PEPE}` | 200 且 `symbol=="PEPE"` |
| DEX Screener | `/latest/dex/tokens/{PEPE}` | 200 且 `pairs.length>0` |
| GeckoTerminal | `/networks/eth/tokens/{PEPE}` | 200 且 `data.id` 含 CA |
| GoPlus | `token_security/1?contract_addresses={PEPE}` | 200 且 `code==1` |
| EVM RPC | `eth_blockNumber` | 返回十六进制块号 |
| （可选）Etherscan | 无 key 时应返回明确错误 | 不得静默成功 |

---

## 6. CI 门槛（发布 v0.1.0 前全部满足）

- [ ] `ruff check .` 无错误
- [ ] `pytest -q -m "not live"` 全绿，且至少覆盖 S1–S16
- [ ] 双平台（ubuntu-latest / windows-latest）× Python 3.10/3.11
- [ ] 覆盖率 ≥70%（核心 pipeline 与 providers）
- [ ] 静态检查：无 `sendTransaction` / 无私钥读取 / 无硬编码路径 `D:\`
- [ ] 样例报告能在**无网络**环境生成（离线夹具）
- [ ] README 的支持矩阵与实际代码一致（自动比对 provider 列表）

---

## 7. 失败即阻断的硬规则（给 CI 之外的人工复核用）

1. 关键节点 `FAILED` 时，报告顶部必须显示 `INCOMPLETE — 不得用于结论`。
2. 任何 `BLOCKED` 风险项，禁止生成「候选清单」，只能生成「待查清单」。
3. 没有快照的信号，不允许进入回测样本（测试 `test_no_lookahead` 覆盖）。
4. 报告里每个数字都要能回答：哪个源、什么时间、哪个接口。
