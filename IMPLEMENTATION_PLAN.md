# IMPLEMENTATION_PLAN.md — 技术可行性审计与 MVP 实施规划

> 版本：v0.2（2026-10-19 修订：对齐「7 步原方法」，补齐步骤 4/5/6/7 的实施路径）
> 原方法出处：老板提供的「找百倍 Meme 币的 7 步捷径」
> 环境：Windows 11 / git-bash / Python 3.11.16

---

## 0. 对齐：7 步原方法 ↔ 技能实现进度（2026-10-19）

| 步骤 | 原方法 | 技能现状 | 缺口与计划 |
|---|---|---|---|
| 1 | 挑上轮火过的币（涨 4500 倍/750 倍均可，归零也行） | ⚠️ 半实现：管线支持任意币，但无「历史赢家样本库」 | 样本库先由人工指定币起家，后续攒 `samples/` 目录 |
| 2 | 找最早买入的前 20 个钱包 | ✅ N2 early_buyers 已实现（ETH/BSC/SOL 三链，带 tx 证据） | — |
| 3 | 筛 30 天还在交易的（一般剩 3~5 个） | ✅ N3 wallet_activity 已实现（30 天窗口+截断标注） | — |
| 4 | 剔除机器人（交易间隔几秒的） | ❌ 未实现 | **P2-4**（本文档 §2.1） |
| 5 | **看幸存钱包 30 天买了什么、拿了多少、是否继续拿**（原方法：最关键的一页） | ❌ 未实现 | **P2-5**（§2.2，核心） |
| 6 | 多个幸存钱包同时买同一币 = 信号 | ❌ 未实现 | **P2-6**（§2.3） |
| 7 | 用自己的评分标准评估 | ⚠️ 半实现：评分公式有，但缺「幸存者共识」输入 | **P2-7**（§2.4） |

**结论：7 步里已实现 2.5 步。本计划把 4/5/6/7 补齐（预计 3 天），补完后原方法的每一步都有对应命令、输出与证据。**

---

## 1. 环境审计结论（v0.1 保留，仍有效）

| 项 | 实测结果 | 对设计的影响 |
|---|---|---|
| OS / Shell | Windows 11，`terminal` 走 git-bash（MSYS） | 脚本不依赖 bash、不写死路径 |
| Python | 3.11.16 | 只用标准库 + pytest |
| Node | 22.23.2 | 可选（GMGN CLI），当前不依赖 |
| git | 2.55.0 | 仓库当前无 .git（待补 init） |
| 磁盘 | D: 剩余充足 | JSONL 快照足够，暂不引 SQLite |
| Skill 格式 | `skills/<category>/<name>/SKILL.md` + frontmatter（description ≤60 字符） | 已符合 |
| 网络 | Blockscout/DEXScreener/GoPlus/GeckoTerminal/公共RPC 实测可用；BSC archive 限制、SOL 限流已实测标定 | 见 DATA_SOURCES.md |

## 1bis. 数据源现状（2026-10-19 实测后更新）

| 源 | 状态 | 用于 |
|---|---|---|
| Blockscout | ✅ ETH 主源 | N1/N2/N3（ETH） |
| BSC 公共 RPC | ✅ 窄区间 getLogs OK；**archive 限制**（只能扫近期 ~N 千块） | N2/N3（BSC） |
| Solana 公共 RPC | ✅ 可用；100 req/10s 限流 → **只能扫最近 ~100 笔签名** | N2/N3（SOL） |
| DEX Screener | ✅ | N1 选池（V2 优先 + 活跃度加权） |
| GoPlus | ✅ ETH=1 / BSC=56 / solana 三端点 | N4 |
| GeckoTerminal | ✅ 10 次/分钟 | 回测价格（P2-7 后续） |
| GMGN | ❌ 缺 key（文档已核实支持 ETH/SOL/BSC） | Phase 1.5 增强 |

---

## 2. 本轮要补的 4 个模块（按 7 步编号）

### 2.1 P2-4 机器人剔除（对应原方法第 4 步）

**判定逻辑（多特征打分，不搞一票否决）：**

```text
对 N2 产出的每个买家钱包，采集其近期交易时间序列（复用 N3 已拉数据）：
  交易间隔中位数 <10s            → +3 分
  同块多笔（同 block/slot）      → +2 分
  30 天交易笔数 >500             → +2 分
  金额高度整齐（方差/均值<0.05） → +1 分
  合计 ≥4 → LIKELY_BOT（剔除）
  2~3   → BOT_SUSPECT（保留但降权 0.5）
  其余   → HUMAN（保留）
```

- 实现位置：`pipeline/wallet_profile.py`（N3 内扩展，输出 `bot_score`/`bot_label`）
- 输出：`activity[].bot = {label, score, evidence[]}`；被剔除钱包进 `excluded_bots[]` 但保留证据
- 测试：夹具「间隔 5s×20 笔」→ LIKELY_BOT；「正常人间隔」→ HUMAN；证据不足 → BOT_SUSPECT 保留

### 2.2 P2-5 幸存者画像（对应第 5 步，核心）

**目标**：回答「这 3~5 个幸存钱包，最近 30 天各买了哪些币、买了多少、现在还拿不拿得住」。

**实现**：新文件 `pipeline/survivor_portrait.py`，输入 = N3 过滤后的 HUMAN 钱包列表

```text
对每个幸存钱包：
  EVM:  /addresses/{addr}/token-transfers（已有）→ 按代币聚合
        近30天买入 = 「from 是已知 DEX 池/路由的转入」聚合（复用 N3 的 swap 判定）
        当前持仓 = Blockscout /addresses/{addr}/token-balances
  SOL:  getSignaturesForAddress(钱包) → 逐笔余额对账（把 solanarpc.classify_buyers
        的方法从「针对某 mint」推广到「该钱包全部 token 账户」）
        当前持仓 = getTokenAccountsByOwner
输出 per 钱包：
  recent_buys[]: {token, symbol?, first_buy_at, buy_count, est_amount_usd?, still_holding}
  （USD 估值用 DEX Screener 单价；拿不到就 null 并写 unknowns，不编造）
```

- 限流预算：ETH 每钱包 2~3 次调用；SOL 每钱包约 10~20 次 → **单轮最多 5 钱包**，超出的排队
- 测试：夹具「幸存者买了 3 个币、其中 1 个已清仓」→ recent_buys 3 条、still_holding 标注正确

### 2.3 P2-6 共买信号（对应第 6 步）

**目标**：跨幸存钱包聚合，找「≥K 个幸存钱包近 D 天都买过同一币」。

**实现**：新文件 `pipeline/consensus.py`（纯函数，输入 = P2-5 的 recent_buys 集合）

```text
signal = {token, buyers:[{wallet, first_buy_at}], window_days, still_holding_count, weight}
参数（配置化，默认待验证）：K=3（原方法口径「三个不同钱包」）、D=7 天
weight = 幸存者人数×0.4 + 全部仍持有×0.3 + 时间集中度（首买间隔<48h）×0.3
输出 signals[] 按 weight 降序；每条信号必须能列出「哪些钱包、何时买的」证据
```

- 红线（照抄验收规程）：只统计真实买入（P2-5 已排除空投/转账）；地址数 ≠ 独立主体数，同时输出 `estimated_entities`（同资金源一层合并）
- 测试：夹具「3 钱包 5 天内都买 X，其中 1 个已卖」→ 触发、still_holding_count=2；「2 钱包」→ 不触发

### 2.4 P2-7 评分接线 + watch 报告（对应第 7 步）

**新命令**：`python -m onchain_sm.cli watch --token <样本币> --chain bsc --survivor-window 30`

```text
流程 = 现 5 节点 + P2-4 剔机器人 + P2-5 画像 + P2-6 共买
输出 out/watch-<symbol>-<时间>.json：
  survivors[]    过机器人筛的幸存钱包+证据
  portraits[]    每个幸存者的近期买入画像
  signals[]      共买信号（含 weight 与证据）
  score          原 4×25 分制 + 信号加成（max(signals.weight)×10，封顶）
  final_grade    语义不变；信号只影响排序与 RESEARCH 提名，不产生交易建议
```

- 快照：signals 一并写入 snapshots.jsonl（为回测留料）

---

## 3. 实施顺序与工时

| 顺序 | 内容 | 预计 | 完成判据 |
|---|---|---|---|
| 1 | P2-4 机器人剔除 + 夹具 | 0.5 天 | 3 个夹具场景全绿 |
| 2 | P2-5 幸存者画像（ETH 先行，SOL 复用对账法） | 1 天 | 真网跑 PEPE 出 ≥3 幸存者画像 |
| 3 | P2-6 共买信号（纯函数） | 0.5 天 | 夹具触发/不触发两例正确 |
| 4 | P2-7 watch 命令 + 报告 | 0.5 天 | 用 BSC/SOL 现有 meme 样本端到端出 watch 报告 |
| 5 | 文档同步（SKILL.md/workflow.md/README 7 步对照表） | 0.5 天 | 7 步每步都有「命令 + 输出字段」映射 |

总计 ~3 天。全程只读、免 key、失败显式标注，与既有硬规则一致。

## 4. 已知边界（诚实声明）

1. **Step 1 样本库**：「挑上轮火过的币」本质是人工输入。工具先支持任意给定币；`samples/` 历史赢家库随使用积累，**不做自动选币**（避免未来函数）。
2. **Solana 画像成本**：每钱包 10~20 次 RPC，5 钱包退避后约 2~4 分钟/币，报告标注覆盖范围。
3. **USD 估值**：免费源拿不到历史成交价，`est_amount_usd` 尽力而为，拿不到就 null。
4. **机器人识别是启发式**：输出 label + 证据 + 置信分，不声称「识别全部机器人」。
5. **共买 ≠ 买入建议**：signals 只用于研究排序；回测验证之前不宣称有效。

---

## 6. v0.1 的模块划分与目录树（继续有效，增量如下）

依赖硬约束不变：`pipeline/` 不直接发 HTTP；providers 统一返回结构；FAILED 向上冒泡成 UNKNOWN 不降级为 0；评分纯函数。

风险登记表新增：
| # | 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|---|
| R10 | Solana 幸存者画像超限流 | 中 | 中 | 每轮限 5 钱包 + 退避 + 报告标注覆盖范围 |
| R11 | 幸存者画像被「空投代币」污染 | 中 | 中 | 画像只统计 DEX 程序触达的买入（与 N2 同口径） |
| R12 | 共买信号被同主体多地址刷高 | 中 | 高 | estimated_entities 一层合并（同资金源） |

目录树增量：`pipeline/survivor_portrait.py`、`pipeline/consensus.py`、`cli.py watch 子命令`、`tests/fixtures/{bot_wallet,survivor,consensus}/`。
