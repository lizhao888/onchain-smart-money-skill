# IMPLEMENTATION_PLAN.md — 技术可行性审计与 MVP 实施规划

> 版本：v0.1（审计稿，不含任何“已实现”声明）
> 依据：《链上聪明钱发现与验证 Skill 设计方案与开源计划》v0.1（2026-10-18）
> 审计时间：2026-10-18　环境：Windows 11 / git-bash / Python 3.11.16 / Node 22.23.2 / git 2.55

---

## 1. 环境审计结论

| 项 | 实测结果 | 对设计的影响 |
|---|---|---|
| OS / Shell | Windows 11，`terminal` 走 git-bash（MSYS） | 脚本必须**不依赖 bash**、不写死路径；CI 用 ubuntu 跑，本地用 Windows 跑，两边都要过 |
| Python | 3.11.16（两个解释器同版本） | 只用标准库 + `pytest`；不引入重依赖（pandas 等），降低安装门槛 |
| pytest | **未安装**（`No module named pytest`） | Phase 0 必须先建 venv 并装 pytest；否则「自动化测试」无法落地 |
| Node | 22.23.2，npm 10.9.8 | 可选（GMGN 官方 CLI 是 npm 包），MVP 不依赖 |
| git | 2.55.0 | `git push` 到 github.com 在本机会超时（已实测）→ 发布走 GitHub API（Contents API），CI 仍可用 GitHub Actions |
| 磁盘 | D: 剩 68G | SQLite + 快照足够 |
| 管理员权限 | 无（免 UAC） | 不装系统级服务；venv 放项目目录 |
| Skill 格式 | 本机 Hermes：`skills/<category>/<name>/SKILL.md` + YAML frontmatter（`name`、`description`），支持 `scripts/`、`references/`、`assets/` | 与公开 Agent Skills 规范同构，可直接用设计文档 `skills/<name>/` 结构 |
| **description 长度** | 本机 Hermes 强限制 **≤60 字符**（超了会拒绝创建/截断索引） | 公共规范允许更长，但为双兼容，`description` 保持 ≤60 字符，细节全部写进 `SKILL.md` 正文 |
| 网络 | GitHub API 通；raw.githubusercontent 200；GMGN API 403/超时 | 依赖外网的数据源必须有失败分支（见 DATA_SOURCES.md） |
| 测试可离线 | 需要「录制式夹具」 | 所有单测默认离线；联网测试单独标记 `@pytest.mark.live` |

**结论**：环境足以支撑「只读研究 MVP」，但必须先补 pytest 与离线夹具；跨平台（Windows/CI）是从第一天就要遵守的约束。

---

## 2. 与设计文档的差异（审计后调整）

| 设计文档 | 审计结论 | 调整 |
|---|---|---|
| 多链起步 | Solana 公共 RPC 可用但解析成本高；BSC 无 Blockscout、需 BscScan key；GMGN 需 key 且不含 ETH | **MVP 只做 Ethereum**，其余链进 Phase 3 |
| GMGN 作为钱包画像/早期买家来源 | 需 API Key（上传公钥）；**端点与限额有公开文档**（Free 5/5 漏桶、端点权重 1–5）；支持 ETH/SOL/BSC/Base；直连被 Cloudflare 挡，官方通道是 `gmgn-cli` | Phase 1.5 接入（key 到位后），MVP 先用免费源 |
| 评分含“钱包历史质量” | 历史盈亏需要完整成交换手记录；免费源只有「转账 + 池子事件」，无法精确算 PnL | MVP 用**可验证的替代指标**（近 30 天真实 swap 数、独立交易日、持仓是否仍在），PnL 标 `UNKNOWN` |
| 关联钱包分析 | 需要资金流向图（首次入金地址） | MVP 只做**一层**：同一资金源/直接互转；深度聚类进 Phase 2 |
| 回测 | 需要历史价格 + 快照 | MVP 保存快照；回测框架在 Phase 2 落地（GeckoTerminal OHLCV 10 次/分钟，够低频回测） |

---

## 3. 模块划分与依赖关系

```
                    ┌─────────────────────────────┐
                    │  SKILL.md（调度 + 状态机）    │
                    └──────────────┬──────────────┘
                                   │ 调用
        ┌──────────────────────────┼──────────────────────────┐
        ▼                          ▼                          ▼
┌───────────────┐        ┌─────────────────┐        ┌────────────────┐
│  providers/   │        │   pipeline/     │        │   storage/     │
│ (数据适配器)   │───────▶│ (确定性计算)     │───────▶│ (SQLite+快照)  │
└───────┬───────┘        └────────┬────────┘        └───────┬────────┘
        │                         │                         │
        ▼                         ▼                         ▼
  Blockscout /            classify / cluster /        runs / signals /
  DEX Screener /          score / verify              snapshots / outcomes
  GeckoTerminal /
  GoPlus / RPC
```

**依赖规则（硬约束）**
1. `pipeline/` 不许直接发 HTTP —— 只能通过 `providers/`，便于录制回放与离线测试。
2. `providers/` 返回统一结构，必带 `provider`、`retrieved_at`、`chain_id`、`request_status`、`data_coverage`、`source_reference`。
3. 任何下游只能消费 `request_status ∈ {OK, EMPTY_CONFIRMED}` 的结果；`FAILED/TIMEOUT/BLOCKED` 必须向上冒泡成 `UNKNOWN`，不得转成 0 或空列表。
4. 评分函数必须**纯函数**（输入 JSON → 输出分数+理由），便于单测与回测复现。

---

## 4. 阶段目标（Phase 0–3）

### Phase 0：地基与验收标准（预计 2–3 天）
- 建 venv + pytest；建立仓库骨架、CI（lint + 离线测试）。
- 落地**统一数据契约**（`schemas/`）与 7 个 Provider 接口的空实现（返回 `NOT_IMPLEMENTED`）。
- 建立**离线夹具**：3 个代币的 Blockscout/DEXScreener/GoPlus JSON 录制文件 + 8 个异常场景夹具。
- 产出：`SKILL.md` 骨架、`DATA_SOURCES.md`（已完成）、`MVP_ACCEPTANCE.md`（已完成）、CI 绿灯。

**完成判据**：`pytest -q` 全绿（离线）；`python -m skill_doctor` 能打印每个数据源的健康状态；CI 在 GitHub 上跑通。

### Phase 1：只读研究 MVP（预计 5–8 天）
- 输入：`chain=ethereum` + `token_address`。
- 链路：代币身份核验 → 池子识别（DEX Screener）→ 最早 20 个真实买家（池子 Transfer/Swap）→ 钱包 30 天活跃 → 机器人初判 → 关联初筛 → 共买候选 → GoPlus 风险门槛 → 评分 → 报告 + 快照。
- 每个节点输出 `status`（PASSED/WARNING/BLOCKED/FAILED）+ `evidence[]`。
- 产出：`SKILL.md` v0.1、可跑 CLI、样例报告（离线可复现）。

**完成判据**：用 3 个真实代币跑通，每个结论都能追到 tx hash / 合约 / 来源 / 时间戳；异常场景（见 MVP_ACCEPTANCE.md）全部按预期降级。

### Phase 2：证据、跟踪与回测（预计 5–7 天）
- SQLite 落库（`runs/signals/snapshots/outcomes`），信号时刻冻结。
- 跟踪 24h/7d/30d 结果（GeckoTerminal OHLCV + DEX Screener 流动性快照）。
- 时间顺序回测 + 防未来数据测试（`test_no_lookahead`）。
- 规则版本表 + 新旧对比报告 + 回滚脚本。

### Phase 3：多链与受控改进（后续）
- 新增链：Solana（Helius key）或 BSC（BscScan key），按 Provider 隔离。
- 关联钱包深度聚类（一层 → 两层资金图）。
- 规则候选发布流程：研究环境 → 验证环境 → 正式环境。

---

## 4bis. MVP-S：把范围再砍一半（自我审计后修正）

初版 Phase 1 写了 8 个 Provider、10 个节点、7 个脚本、SQLite —— 这已经不是一个「最小可用版本」，而是一个中型项目。按「先证明一条主路径」的原则，**第一版应该只做 5 个节点、4 个数据源、零数据库**：

| 节点 | 做什么 | 用到的源 | 输出 |
|---|---|---|---|
| N1 身份 | 合约地址 ↔ 链 ↔ 池子 | DEX Screener + Blockscout `/tokens/{ca}` | `token_id`、`pool_address`、`pairs[]` |
| N2 早期买家 | 前 20 个「池子 → EOA」买入 | Blockscout `getLogs`（`topic0_1_opr=and`）+ RPC 备份 | `buyers[]`（含 txHash/区块） |
| N3 活跃度 | 近 30 天真实 swap 数 / 最后活跃 | Blockscout `token-transfers` + `transactions` | `activity[]` |
| N4 风险闸门 | 不可卖/高税/黑名单 | GoPlus `token_security` | `risk.blockers[]` |
| N5 报告 | 状态机 + 评分（纯函数）+ 快照 | 本地计算 | `report.json` + `snapshot.jsonl` |

**明确不在第一版**：多链、共买共识（需要多币样本）、关联钱包聚类、回测、SQLite、Web UI、任何交易能力。
**对应工时**：3–4 天（不是 5–8 天）。
**判定「MVP 完成」**：给定一个 ETH 合约地址，一条命令产出带证据和状态的报告；16 个异常场景里有 12 个（S1–S16 中与单币相关的）通过。

---

## 5. 风险登记表

| # | 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|---|
| R1 | Blockscout 限流导致管线中断 | 高 | 中 | 节流 + 429 退避 + RPC 备份 + 本地缓存 |
| R2 | 免费源字段口径不一致 | 高 | 中 | 双源比对，超阈值 `WARNING`，不取平均 |
| R3 | 把空投/转账误判成买入 | 高 | **高** | 只认「池子 → EOA」的 Transfer/Swap；空投检测（同一发送方多钱包） |
| R4 | 把同一人多个地址当独立共识 | 中 | 高 | 资金源一层检查 + 明确输出「地址数 vs 估计独立主体数」 |
| R5 | 历史盈亏无法精确计算 | 高 | 中 | MVP 标 `UNKNOWN`，只用可验证代理指标 |
| R6 | GMGN 缺 key（有文档、有 ETH） | 中 | 低 | Provider 占位，key 到位后接；MVP 不依赖 |
| R7 | 回测泄漏未来数据 | 中 | **高** | 快照冻结 + `retrieved_at` 过滤 + 专项测试 |
| R8 | Windows/CI 路径差异 | 中 | 中 | 全用 `pathlib`；CI 跑 ubuntu + windows 双平台 |
| R9 | 规则被自动改写 | 低 | **高** | 规则文件带版本 + 必须过验收 + 人工/独立验证才能发布 |

---

## 6. 推荐目录树（GitHub 仓库）

```
onchain-smart-money-skill/
├── README.md                       # 项目定位、支持矩阵、5 分钟快速开始、明确不做什么
├── LICENSE                         # 建议 Apache-2.0（见 §8）
├── SECURITY.md                     # 漏洞上报、密钥处理、数据合规
├── CONTRIBUTING.md                 # 如何加链/加 Provider/加测试夹具
├── CODE_OF_CONDUCT.md
├── CHANGELOG.md
├── .gitignore                      # .env / out/ / *.db / fixtures/tmp
├── .env.example                    # 仅占位符：BLOCKSCOUT_BASE、HELIUS_API_KEY...
├── pyproject.toml                  # 项目元数据 + pytest/ruff 配置
├── skills/
│   └── onchain-smart-money-research/
│       ├── SKILL.md                # 入口：何时用、状态机、每步验收、禁止自动交易
│       ├── references/
│       │   ├── workflow.md         # 10 个节点的输入/动作/验收/失败/输出
│       │   ├── data-sources.md     # 与 DATA_SOURCES.md 同步的速查表
│       │   ├── scoring-model.md    # 权重、口径、分级、置信度
│       │   ├── chain-specific-risks.md   # ETH 现在写；Solana/BSC 占位
│       │   └── output-schema.md    # 报告/快照 JSON Schema
│       ├── scripts/
│       │   ├── run_pipeline.py     # 一键：token → 报告（只读）
│       │   ├── validate_data.py    # 数据契约校验 + 空值/冲突检测
│       │   ├── classify_trades.py  # 买入/卖出/转账/空投/加池 分类
│       │   ├── analyze_wallets.py  # 30 天活跃、swap 统计、持仓
│       │   ├── detect_clusters.py  # 资金源一层关联
│       │   ├── score.py            # 纯函数评分 + 风险门槛
│       │   └── track_outcomes.py   # 24h/7d/30d 结果跟踪
│       └── assets/
│           ├── sample-config.yaml
│           └── sample-report.md
├── src/onchain_sm/                 # 可复用库（CLI + Provider 实现）
│   ├── __init__.py
│   ├── config.py                   # 读 env / yaml，校验必填
│   ├── models.py                   # dataclass / pydantic-free schema
│   ├── http.py                     # 节流、重试、退避、超时、UA、缓存
│   ├── providers/
│   │   ├── base.py                 # Provider 协议 + 统一返回结构
│   │   ├── blockscout.py           # ✅ 已实测
│   │   ├── dexscreener.py          # ✅ 已实测
│   │   ├── geckoterminal.py        # ✅ 已实测（10 次/分钟）
│   │   ├── goplus.py               # ✅ 已实测
│   │   ├── evm_rpc.py              # ✅ 已实测（多端点轮换）
│   │   ├── etherscan.py            # ⚠️ 需 key（未验证）
│   │   ├── solana_rpc.py           # ⚠️ 可用但成本高（未实现）
│   │   └── gmgn.py                 # ⚠️ 需 key（文档已核实，Phase 1.5）
│   ├── pipeline/
│   │   ├── token_identity.py       # 代币/链/池子核验
│   │   ├── early_buyers.py         # 最早 N 个真实买家
│   │   ├── wallet_profile.py       # 30 天活跃/分类
│   │   ├── clusters.py             # 关联钱包
│   │   ├── consensus.py            # 多窗口共买
│   │   ├── risk.py                 # GoPlus + 流动性 + 集中度门槛
│   │   └── scoring.py              # 评分（纯函数）
│   ├── storage/
│   │   ├── db.py                   # SQLite schema + 迁移
│   │   └── snapshots.py            # 不可变快照（json + sha256）
│   └── cli.py                      # `python -m onchain_sm run --token 0x...`
├── tests/
│   ├── conftest.py                 # 夹具加载器、离线 HTTP 桩
│   ├── fixtures/
│   │   ├── pepe/{token,transfers,holders,pairs,goplus}.json
│   │   ├── edge_empty/*.json       # 真空结果
│   │   ├── edge_failure/*.json     # 超时/429/500/字段缺失
│   │   ├── edge_airdrop/*.json     # 单源批量分发
│   │   └── edge_related/*.json     # 同资金源多钱包
│   ├── test_http_resilience.py
│   ├── test_trade_classification.py
│   ├── test_early_buyers.py
│   ├── test_wallet_clusters.py
│   ├── test_scoring.py
│   ├── test_status_machine.py
│   ├── test_no_lookahead.py
│   └── test_live_smoke.py          # @pytest.mark.live，默认跳过
└── .github/
    ├── workflows/ci.yml            # ubuntu+windows × py3.10/3.11：ruff + pytest -m "not live"
    ├── ISSUE_TEMPLATE/{bug,data_source,new_chain}.yml
    └── pull_request_template.md
```

**每个文件的作用**

| 文件 | 作用 | MVP 是否必需 |
|---|---|---|
| `skills/.../SKILL.md` | Agent 入口：触发条件、状态机、每步验收、禁止项 | ✅ |
| `references/workflow.md` | 10 节点的完整协议（输入/动作/验收/失败/输出） | ✅ |
| `references/output-schema.md` | 报告与快照的 JSON Schema，供验收测试直接用 | ✅ |
| `scripts/*.py` | 可单独执行的确定性计算（便于人工复核） | ✅（可先写 3 个） |
| `src/onchain_sm/providers/*` | 数据适配器，隔离外部变化 | ✅ |
| `src/onchain_sm/http.py` | 限流/重试/缓存——所有失败的根因都在这 | ✅ |
| `tests/fixtures/*` | 离线夹具，保证 CI 不依赖外网 | ✅ |
| `.github/workflows/ci.yml` | 双平台 CI，防 Windows 特有问题 | ✅ |
| `.env.example` | 只放占位符 | ✅ |
| `SECURITY.md` | 密钥与漏洞上报流程 | ✅（开源必需） |

---

## 7. 证据与状态机落地方式

每个节点输出统一结构：

```json
{
  "step": "early_buyers",
  "status": "PASSED|WARNING|BLOCKED|FAILED",
  "started_at": "...", "finished_at": "...",
  "inputs": {"chain": "ethereum", "token": "0x..."},
  "evidence": [
    {"type": "tx", "ref": "0x...", "source": "blockscout", "retrieved_at": "..."},
    {"type": "pool", "ref": "0x...", "source": "dexscreener", "retrieved_at": "..."}
  ],
  "metrics": {"candidates": 20, "verified_buys": 18, "dropped_transfers": 2},
  "unknowns": ["wallet 0x... 的历史盈亏：数据源不含成本基础"],
  "reasons": ["3 个地址由同一资金源注资 → 独立主体估计 1"]
}
```

**判定「真空 vs 取数失败」的唯一依据**

| 情况 | 判据 | 状态 |
|---|---|---|
| 真空 | HTTP 200 + 分页到底 + 结构合法 + 计数为 0 | `PASSED` 且 `metrics.count = 0` |
| 覆盖不足 | 200 但只拿到首页且 `next_page_params` 非空 | `WARNING`（未扫完） |
| 接口失败 | 超时 / 429 / 5xx / 结构不符 | `FAILED`（禁止下游用空列表） |
| 源冲突 | 两源关键字段差异超阈值 | `WARNING` + 列出两值 |

---

## 8. 开源与许可建议

- **许可证：Apache-2.0**。理由：明确专利授权条款，适合可能被企业采用的工具；MIT 更简单但无专利条款。两者都允许闭源商用。最终由你拍板。
- 不打包任何第三方数据（价格、持有人、标签）进仓库；夹具只用**结构相同的脱敏/截断样例**，并注明来源与获取时间。
- 仓库开启：Secret Scanning、Dependabot、分支保护（CI 必过）。
- README 首屏必须回答设计文档 §11.5 的 10 个问题，并明确「不交易、不接触私钥」。

---

## 9. 目前无法验证的事项 / 技术瓶颈 / 前置条件

| # | 事项 | 状态 | 需要什么才能推进 |
|---|---|---|---|
| B1 | GMGN 端点运行时验证 | **文档已核实，运行时未验证** | 一个 GMGN API Key（你本人在 gmgn.ai/ai 上传公钥生成）+ IPv4 出口，用 `gmgn-cli` 实测 |
| B2 | GMGN 是否支持 ETH 查询 | **文档显示支持**（`gmgn-token` 有 ETH 示例），**未实测** | 同上；跑一次 `/v1/token/info`（eth）确认 |
| B3 | Etherscan/BscScan 具体限额 | **未核实** | 免费 key 注册后实测 429 阈值 |
| B4 | Blockscout 官方限流阈值 | **无公开文档** | 只能实测标定（已有 429 现象） |
| B5 | Solana 最早买家 | **未实现** | Helius key 或 SolanaTracker 付费；公共 RPC 分页成本高（100 req/10s） |
| B6 | pump.fun 早期买家 | **未核实** | 需实测 SolanaTracker/madeonsol 接口与价格 |
| B7 | 历史 PnL 精确计算 | **不可行（免费源）** | 需要完整 swap 历史 + 成本基础；或付费索引（Bitquery 等） |
| B8 | 蜜罐/可卖性「链上实证」 | **部分** | 只读模式无法真实卖出；只能用 GoPlus 标签（C 级证据） |
| B9 | GeckoTerminal OHLCV 覆盖率（长尾币） | **未核实** | 需对 20 个长尾样本实测命中率 |
| B10 | GitHub 直接 `git push` | **不可用（本机）** | 网络受限，发布走 Contents API；CI 不受影响 |

**前置条件（开工前必须满足）**
1. `pytest` 装好，且有离线夹具加载器（否则「自动验收」是空话）。
2. 明确 MVP 只做 Ethereum，产品文案不得宣传多链。
3. 评分规则与阈值写进 `scoring-model.md` 并**冻结版本 v0**，后续改动必须走版本流程。

---

## 10. 审计结论

1. **可行性：成立**，但只限于「Ethereum + 只用免费源 + 只读」这条窄路径。
2. **最大技术风险不是数据源，而是「真假买入」的判定**——必须只认池子事件，且把空投/转账显式剔除（已在上一轮实测中踩到：5 个钱包"共买"实为同一合约批量空投）。
3. **GMGN 不是「不能用」，而是「缺 key」**：官方仓库 `GMGNAI/gmgn-skills` 已公开端点表与限额（Free 5/5、端点权重 1–5），并且有 ETH 示例；直连被 Cloudflare 挡，官方通道是 `gmgn-cli`。key 到位前不进 MVP。
4. **测试基础设施必须先于功能开发**，否则「不伪造成功结论」无法被机器验证。
5. 下一步：Phase 0 建骨架 + 夹具 + CI，再写第一个真实节点（代币身份 → 池子 → 最早买家），逐节点交付。
