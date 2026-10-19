# MVP_ACCEPTANCE.md — 可重复执行的验收方案

> 目标：让「不伪造成功结论」这件事**由测试证明**，而不是由提示词保证。
> 前提：所有测试默认**离线**（用录制的 HTTP 夹具），联网测试单独标记 `@pytest.mark.live`。
> 运行方式：`pytest -q -m "not live"`（CI 必跑）／ `pytest -q -m live`（本地手动，需网络）
> **状态（2026-10-19 整改后）：27 passed / 9 skipped（显式标注 Phase 2）。下表「已实现/未实现」与 tests/ 一一对应。**

---

## 0. 验收分层

| 层 | 内容 | 判定 |
|---|---|---|
| L1 单元 | 纯函数：交易分类、去重、评分、状态机 | 输入→输出与期望完全一致 |
| L2 契约 | Provider 返回结构、字段类型、必填项 | JSON Schema 校验通过 |
| L3 场景 | 业务场景（见 §2） | 状态 + 关键字段符合期望 |
| L4 端到端 | 真实代币跑完整管线（离线夹具 + 真网） | 报告可从证据复核，异常按预期降级 |
| L5 联网冒烟 | 每个 Provider 一次真实请求 | 200 或明确错误码；**不作为 CI 门槛** |

---

## 1. 全局验收标准（任何一次运行都必须满足）

| # | 标准 | 机器判定方式 | 状态 |
|---|---|---|---|
| A1 | 每个节点都有 `status` | 报告 JSON 里 5 个节点全部存在且 ∈ 枚举 | 已实现（test_g4） |
| A2 | 关键结论都有证据引用 | `evidence[]` 非空，且每条含 `ref/source/retrieved_at` | 已实现（test_g5） |
| A3 | 取数失败不得变成空结果 | `status=FAILED` 时 `metrics` 不得为 0 计数成功 | 已实现（test_g1/g2） |
| A4 | 真空必须可证明 | `status=PASSED` 且 `count=0` 时，必须带 `pages_exhausted=true` | 已实现（test_empty_window） |
| A5 | 关联钱包不得直接当独立主体 | 报告必须同时给出 `raw_addresses` 与 `estimated_entities` | **未实现（Phase 2）** |
| A6 | 硬风险不得被总分覆盖 | 存在 `BLOCKED` 风险项时，`final_grade` 必须为 `BLOCKED` | 已实现（test_s8） |
| A7 | 时间不泄漏 | 所有用于回测的字段 `retrieved_at <= signal_time` | **未实现（Phase 2）** |
| A8 | 不接触私钥 | 代码里不存在签名/转账调用（静态检查 grep `sendTransaction|signTransaction|private_key`） | 已实现（ci.yml） |

## 1bis. 评分公式（与代码 `_finalize` 逐字一致，2026-10-19 对齐）

```text
score = buyer_score + activity_score + liq_score + risk_score   # 四项各 0–25，总 0–100
buyer_score    = 25 × min(买家数 / 20, 1)
activity_score = 25 × (ACTIVE 钱包数 / 买家数)
liq_score      = 25 × (1.0 if liq≥1M else 0.6 if ≥100k else 0.3 if ≥10k else 0)
risk_score     = 0 if 有 blockers 或风险未核实 else 25

final_grade: 有 blockers→BLOCKED；有节点 FAILED→INCOMPLETE；score≥60→RESEARCH；否则→WATCH
```

> 注：v0 权重（25/25/25/25）是待验证的启发式初始值。`rules_version=v0` 当前是常量
> 标识，独立规则文件与版本治理在 Phase 2（快照回测）一起做。

---

## 2. 场景矩阵（已实现 ✓ / 未实现 ✗ 与 tests/ 一一对应）

| ID | 场景 | 期望 | 状态 | 测试函数 |
|---|---|---|---|---|
| S1 | 历史赢家，数据完整 | 20 个买家、每人 1 条 tx 证据 | ✓ | test_s1_happy_path |
| S2 | 同名不同合约 | 以合约地址为准，标 symbol 冲突 | ✗ Phase 2 | skip |
| S3 | 混有合约接收方 | 跳过并计数 skipped_contracts | ✓ | test_s3_contract_buyer_is_skipped |
| S4 | 只有转账无 swap | DORMANT，swap_30d=0，transfer>0 | ✓ | test_s4_transfer_only_is_dormant |
| S5 | 交易快但证据薄 | 疑似机器人降权不删除 | ✗ Phase 2 | skip |
| S6 | 三个钱包同一资金来源 | raw=3, entities=1 | ✗ Phase 2 | skip |
| S7 | 独立钱包共识 | 分窗口共识度 | ✗ Phase 2 | skip |
| S8 | 严重风险（不可卖/高税） | BLOCKED 且分数不覆盖 | ✓ | test_s8_risk_blocker_overrides_score |
| S9 | 数据源超时/429/500 | FAILED + count=None | ✓ | test_s9_source_failure_is_not_zero |
| S10 | 两个源结果冲突 | WARNING + delta_pct | ✗ Phase 2 | skip |
| S11 | 持仓转移到别址 | PARTIALLY_VERIFIED | ✗ Phase 2 | skip |
| S12 | 采集晚于信号时间 | 拒绝未来数据 | ✗ Phase 2 | skip |
| S13 | 空投伪装共识 | 排除不计入共识 | ✗ Phase 2 | skip |
| S14 | 重复交易 | 按 tx_hash 去重计数 | ✓ | test_s14_duplicate_tx_deduped |
| S15 | 分页未扫完 | WARNING + pages_exhausted=false | ✗ Phase 2 | skip |
| S16 | 200 但字段缺失 | FAILED(SCHEMA_MISMATCH) | ✓ | test_s16_missing_field_is_schema_mismatch |
| — | 真空窗口 | PASSED + count=0 + pages_exhausted | ✓ | test_empty_window_is_confirmed_zero |
| — | 风险不可得 | verified=false + unknowns | ✓ | test_risk_unavailable_lowers_score_and_marks_unknown |

### 整改回归（2026-10-19，tests/test_remediation.py）

| ID | 场景 | 期望 | 测试函数 |
|---|---|---|---|
| R1 | Blockscout 真实「No logs found」响应 | EMPTY_CONFIRMED 不是 FAILED | test_bug001_no_logs_found_is_empty_confirmed |
| R2 | 真实参数错误响应 | 仍是 FAILED | test_bug001_param_error_still_failed |
| R3 | USDT 真空窗口端到端 | PASSED + count=0 + complete=true | test_bug001_usdt_empty_window_end_to_end |
| R4 | 1 年前合约→本人转账 30 笔 | DORMANT，swap_30d=0（对应验收规程 T-009） | test_bug002_old_contract_transfers_not_swaps |
| R5 | 30 天内 3 笔 swap | ACTIVE | test_bug002_recent_swaps_active |
| R6 | 窗口内合约转账 | 不算 swap，记 contract_transfers | test_bug002_contract_transfer_in_window_not_swap |
| R7 | 原生交易接口挂 | swap=None + UNKNOWN，不默认休眠 | test_bug002_transactions_api_down_swap_unknown |
| R8 | 部署者自买 | 买家带 TOKEN_CREATOR 标注 | test_bug007_creator_flagged |
| R9 | --chain bsc | SystemExit 退出码≠0，不发请求 | test_bug003_unsupported_chain_rejected |
| R10 | 默认链 ethereum | 正常 | test_bug003_ethereum_chain_accepted |

## 2bis. 门禁与回归测试（现状）

| 测试 | 状态 |
|---|---|
| test_g1_no_fake_success / test_g2_zero_vs_failure / test_g4_report_schema / test_g5_evidence / test_g6_deterministic_score | 已实现 |
| test_real_fixture_replay_pepe（真实响应离线回放） | 已实现 |
| test_g3_no_lookahead（回测泄漏） | **未实现**（依赖 Phase 2 快照/回测框架，届时补） |
| test_golden_score / test_rule_version_pinned / rules/ 目录 | **未实现**（依赖 Phase 2 规则文件；当前权重以 §1bis 与代码同步为准） |
| output-schema.json（additionalProperties=false） | **未实现**（当前用 test_g4 的 key 白名单近似；Phase 2 换 jsonschema） |

---

## 3. 关键判定规则（写进代码，不靠提示词）

### 3.1 真假买入

```text
真买入 = Transfer(from == pool_address, to == EOA)，且接收方经 /addresses 核验非合约

非买入（必须剔除并计数）：
  铸造   —— to == 0x0000...0（decode_transfer 返回 None）
  合约   —— 接收方 is_contract=true → skipped_contracts
  重复   —— 同 (tx_hash, to) 只保留一次 → dedup_dropped
  空投/撤池 —— 不计为买入；钱包侧的「合约→本人」转账记 contract_transfers，不算 swap
```

### 3.2 真空 vs 失败（2026-10-19 修订，按真实源实测响应体）

| 条件 | 结论 |
|---|---|
| 200 + `message="OK"` + 有记录 | PASSED |
| 200 + `message="No logs found"` + result 空或 **null** | `EMPTY_CONFIRMED` → PASSED + count=0 |
| 200 + 其他 message（参数错等） | FAILED（`UPSTREAM:` 前缀） |
| 429 / 5xx / 超时 | FAILED → 下游必须 UNKNOWN |
| 200 但关键字段缺失（如 `pairs` 无） | FAILED（SCHEMA_MISMATCH） |

### 3.3 活跃度（2026-10-19 修订）

```text
窗口：timestamp >= now_utc - 30 天（window_truncated=true 表示首页数据不够新，未扫全）
swap_30d：只认原生交易 method 含 "swap"。
  token-transfers 里「合约→本人」的转账一律不算 swap（可能是空投/撤池/路由转入），
  单独记 contract_transfers。
原生交易接口不可得 → swap_30d=None + label=UNKNOWN（禁止默认休眠）
ACTIVE: swap_30d ≥ 3 且 last_active ≤ 30 天；LOW_FREQUENCY: swap_30d ≥ 1；其余 DORMANT
```

### 3.4 关联钱包 / 机器人 —— Phase 2（见 IMPLEMENTATION_PLAN.md）

> 关联钱包权重表（0.6/0.3/0.1）与机器人分级（LIKELY_BOT 等）保留为 Phase 2 设计稿，
> 当前代码未实现，不作为本轮验收依据。

### 3.5 评分与阻断

```text
if risk.blockers 非空: final_grade = BLOCKED（分数照算但不展示为可推进）
权重与公式见 §1bis（与 src/onchain_sm/pipeline/run.py::_finalize 逐字同步）
```

---

## 4. 端到端验收（离线夹具 + 真网各一轮）

| 检查项 | 通过标准 | 2026-10-19 真网结果 |
|---|---|---|
| 报告可复核 | 买家 tx hash 可在区块浏览器打开 | ✓ PEPE 首买家 tx 直查一致 |
| 状态完备 | 5 个节点全部有 status | ✓ |
| 未知项显式 | `unknowns[]` 非空 | ✓（PnL/蜜罐） |
| 阻断生效 | 风险样本 BLOCKED | ✓（离线 test_s8） |
| 真空可辨 | USDT 早期窗口 → PASSED+count=0 | ✓（整改后实测） |
| 活跃度真实 | swap_30d 无跨年数字 | ✓（整改后 0+DORMANT+truncated） |

## 5. 联网冒烟（L5，不进 CI）

| Provider | 请求 | 通过标准 |
|---|---|---|
| Blockscout | `/api/v2/tokens/{PEPE}` | 200 且 `symbol=="PEPE"` |
| DEX Screener | `/latest/dex/tokens/{PEPE}` | 200 且 `pairs.length>0` |
| GoPlus | `token_security/1?contract_addresses={PEPE}` | 200 且 `code==1` |

## 6. CI 门槛（发布 v0.1.0 前全部满足）

- [x] `pytest -q -m "not live"` 全绿（27 passed, 9 skipped）
- [x] 静态检查：无 `sendTransaction` / 无私钥读取 / src+tests+skills 无硬编码路径
- [ ] 覆盖率 ≥70%（未接 coverage 工具）
- [ ] S5/S6/S7/S10~S13/S15（Phase 2 场景）补齐

---

## 7. 失败即阻断的硬规则（给 CI 之外的人工复核用）

1. 关键节点 `FAILED` 时，报告顶部必须显示 `INCOMPLETE — 不得用于结论`。
2. 任何 `BLOCKED` 风险项，禁止生成「候选清单」，只能生成「待查清单」。
3. 没有快照的信号，不允许进入回测样本（Phase 2 补 `test_no_lookahead`）。
4. 报告里每个数字都要能回答：哪个源、什么时间、哪个接口。
