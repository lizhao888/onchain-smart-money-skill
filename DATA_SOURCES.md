# DATA_SOURCES.md — 数据源核实报告

> 核实时间：2026-10-18（本机实测，Windows 11 / Python 3.11.16）
> 方法：先读官方文档，再发真实请求，记录 HTTP 状态、耗时、字段样例、是否要 key。
> 原则：**没实测到的一律标「未核实」**，不按文档想象补全。

---

## 0. 结论速览

| 数据源 | 用途 | 要 key? | 实测 | 可用于 MVP | 主要限制 |
|---|---|---|---|---|---|
| **Blockscout（eth.blockscout.com）** | 链上事件、代币转账、持有人、合约源码 | 否 | ✅ 全通 | **是（主链上源）** | 无官方限流文档，突发会 429 |
| **GoPlus Labs** | 合约安全（税率/黑名单/可卖性/创建者） | 否（实测免 key） | ✅ 200 | **是（风险源）** | 限额未公开，标签非链上实证 |
| **DEX Screener** | 交易对、价格、流动性、成交量 | 否 | ✅ 4/4 接口 200 | **是（行情源）** | 官方 reference 只写了部分端点；`/latest/dex/*` 限额未明文 |
| **GeckoTerminal** | 池子、OHLCV、新池、搜索 | 否 | ✅ 6/6 接口 200 | 是（历史价格/新池） | **约 10 次/分钟**，全接口缓存 1 分钟 |
| **公共 JSON-RPC（drpc / mevblocker / publicnode）** | `eth_getLogs`、`eth_call` | 否 | ✅ 部分可用 | 是（备份通道） | publicnode 对 getLogs 返 403；drpc 会限流 |
| **Etherscan v2** | EVM 交易/日志 | **是** | ❌ 无 key 报 `Missing/Invalid API Key` | 可选（有 key 时） | 需注册；限额表未抓到具体数值 |
| **BscScan** | BSC 交易/日志 | **是** | ❌（同 Etherscan 体系） | 否（MVP 不做 BSC） | BSC 无 Blockscout 实例 |
| **Solana 公共 RPC** | 签名/交易 | 否 | ✅ getHealth/getSignatures 通 | 否（MVP 不做 Solana） | 100 请求/10s，单方法 40 请求/10s |
| **Helius** | Solana 解析交易 | **是** | ❌ 无 key 401 | 否（Phase 3） | 免费档 1M credits/月、10 RPS |
| **GMGN OpenAPI** | 代币/钱包/盈亏/榜单/信号 | **是** | 文档 ✅（含端点、权重、限额）；运行时未验证（缺 key） | 否（Phase 1.5 增强源） | Free 5/5 漏桶；端点带权重（1–5）；仅 IPv4；支持 ETH/SOL/BSC/Base |

---

## 1. Blockscout（主链上数据源）

**官方实例**：`https://eth.blockscout.com`（Ethereum）

实测（2026-10-18）：

| 接口 | 状态 | 用途 | 样例字段 |
|---|---|---|---|
| `/api/v2/tokens/{ca}` | 200 | 代币元数据 | `symbol`、`circulating_market_cap`、`holders_count` |
| `/api/v2/tokens/{ca}/transfers` | 200 | 该代币全部转账（倒序分页） | `from`、`to`、`token`、`total`、`timestamp`、`block_number` |
| `/api/v2/tokens/{ca}/counters` | 200 | 转账总数/持有人数 | `transfers_count`、`token_holders_count` |
| `/api/v2/tokens/{ca}/holders` | 200 | 持有人排行 | `address`、`value` |
| `/api/v2/addresses/{addr}/token-transfers` | 200 | 某钱包的代币转账 | 同上 |
| `/api/v2/addresses/{addr}/transactions` | 200 | 原生交易 | `timestamp`、`method` |
| `/api/v2/smart-contracts/{ca}` | 200 | 源码/验证状态 | `source_code`、`creation_status` |
| `/api?module=logs&action=getLogs` | 200 | 按区块区间取事件 | `topics`、`data`、`blockNumber` |

**关键限制（实测）**
- 没有公开的限流文档。连续请求约 60–80 次后会开始返回 **429**；退避 12–48 秒可恢复。
- `/api?module=logs` 比 v2 更早被限流。
- 分页是 `next_page_params`（不是 offset/page）。
- **坑（实测踩到）**：用两个 topic 过滤时必须加 `topic0_1_opr=and`，否则返回
  `{"message":"Required query parameters missing: topic0_1_opr","result":null}` ——
  这个错误**不带 HTTP 错误码**，处理不当会被误判成「没有记录」。

**核心可行性已实测（2026-10-18）**

```
池子地址（DEX Screener 最大流动性对）: 0xA43fe169...（PEPE/WETH, uniswap v2）
查询: getLogs(address=PEPE, topic0=Transfer, topic1=池子, topic0_1_opr=and,
              fromBlock=17046105, toBlock=17048105)
结果: 115 笔「池子 → EOA」转账，99 个唯一接收地址，含 txHash 与区块号
```

**结论**：MVP 的「真买入 = 池子 → EOA」判定路径**技术上成立**，且能拿到可复核的交易哈希。

**结论**：MVP 主链上源。必须做「节流 0.9–1.2s + 429 指数退避 + 本地缓存」。

---

## 2. GoPlus Labs（合约风险）

**端点**：`https://api.gopluslabs.io/api/v1/token_security/{chain_id}?contract_addresses={ca}`

实测（chain_id=1，PEPE）：**200，无需 key**。返回字段包含：
`buy_tax`、`sell_tax`、`cannot_buy`、`cannot_sell_all`、`is_honeypot`、`is_blacklisted`、`is_mintable`、`can_take_back_ownership`、`creator_address`、`owner_address`、`holder_count`、`lp_holders` 等。

**限制**
- 官方未公布公共限额（实测单次正常）。
- 字段是**第三方标签**，不是链上实证 → 按设计文档的 C 级证据处理，只能辅助、不能当唯一依据。

---

## 3. DEX Screener（行情/交易对）

实测（均 200，响应 0.25–0.36s）：

| 接口 | 结果 |
|---|---|
| `/latest/dex/tokens/{ca}` | 200，返回 `pairs[]`（含 `chainId`、`dexId`、`pairAddress`、`priceUsd`、`liquidity`、`volume`、`fdv`、`marketCap`） |
| `/latest/dex/search?q=` | 200 |
| `/latest/dex/pairs/{chain}/{pair}` | 200 |
| `/token-pairs/v1/{chain}/{ca}` | 200（数组形式） |

**限制**
- 无需 key。
- 官方 reference 页（抓取 `reference.md` 12,994 字符）**只列了 6 个 v1 元数据端点**（token-profiles / community-takeovers / ads / metas），明示「rate-limit 60 requests per minute」。
- 核心 `/latest/dex/*` 端点官方**未在该页成文**，但实测可用；其限额**未获官方明文** → 标「未核实」，MVP 里按 ≤1 次/秒 保守使用并缓存。

---

## 4. GeckoTerminal（池子/OHLCV/新池）

**文档**：`https://api.geckoterminal.com/api/v2`（Swagger 实测 60,381 字节，20 个端点）

官方文档原文（已抓取）：
> The API is in its Beta release… All endpoints listed below are cached for **1 minute** … This Public API is limited to approximately **10 calls/minute**, which may fluctuate based on network traffic.

实测（均 200）：`/networks`、`/networks/eth/tokens/{ca}`、`/networks/eth/pools/{pool}`、`/networks/eth/pools/{pool}/ohlcv/hour`、`/search/pools?query=`、`/networks/new_pools`。

**结论**：适合**低频**拉取历史价格/新池；10 次/分钟是硬约束 → MVP 必须本地缓存 + 只对候选币调用。

---

## 5. EVM 公共 RPC

| 端点 | `eth_getLogs` | 备注 |
|---|---|---|
| `eth.drpc.org` | ✅（BSC 会 429） | 首选 |
| `rpc.mevblocker.io` | ✅ | 备选 |
| `ethereum.publicnode.com` | ❌ 403 | 仅普通调用可用 |
| `bsc.publicnode.com` | ✅（BSC） | BSC 唯一可用 |
| `bsc-dataseed.binance.org` | ❌ `limit exceeded` | 不能用 getLogs |

**结论**：作为 Blockscout 被限流时的备份；必须实现「多端点轮换 + 失败自动切」。

---

## 6. Etherscan / BscScan

- 实测：`https://api.etherscan.io/v2/api?...` 无 key → `{"status":"0","message":"NOTOK","result":"Missing/Invalid API Key"}`。
- 官方文档页 `<https://docs.etherscan.io/support/rate-limits>` 存在，但具体数值在动态表格中，本次未抓取到 → **限额标「待核实」**。
- BSC 无 Blockscout 实例（`bsc.blockscout.com` / `bnb.blockscout.com` 均 404）→ 做 BSC 必须有 BscScan key。

---

## 7. Solana

- 公共 RPC `https://api.mainnet-beta.solana.com`：`getHealth` → ok；`getSignaturesForAddress(mint, limit=3)` → 正常返回。
- **官方限额（已抓到原文）**：mainnet 每 IP **100 请求/10 秒**，单一 RPC 方法 **40 请求/10 秒**，另有并发上限；超限返回 403/429。
- Helius：无 key → **401 Unauthorized**。官方定价页：免费档 **1M credits/月、10 RPS**、DAS 2 RPS、getProgramAccounts 5/s。
- **pump.fun 早期买家**：无公开免费接口；要走 SolanaTracker / madeonsol 等付费 cap-table，或自己分页解析（见「未核实项」）。

---

## 8. GMGN（有公开文档与限额；缺 key）

**⚠️ 本节的初版审计写错了**：当时只看了 GitBook 的「Agent API」介绍页，得出「端点没公开、不支持 ETH」的结论。复核官方 GitHub 仓库 `GMGNAI/gmgn-skills`（★620，114 个文件）后更正如下。

**官方仓库**：<https://github.com/GMGNAI/gmgn-skills>（`skills/*/SKILL.md` 内含端点表）

| 事实 | 依据 |
|---|---|
| 鉴权：`GMGN_API_KEY`，放 `~/.config/gmgn/.env` | `skills/gmgn-token/SKILL.md` 前置条件 |
| 交易才需私钥（`gmgn-swap`） | GitBook《GMGN Agent API》 |
| **限额（官方明文）** | 「plan-based leaky bucket: Free `5/5`、Plus `20/20`、Pro `50/50`（rate/capacity）」 |
| **每个端点有权重** | `token info`=1、`token security`=1、`token pool_info`=1、`token_top_holders`=5、`token_top_traders`=5 |
| 超限行为 | `RATE_LIMIT_EXCEEDED` / `RATE_LIMIT_BANNED`（冷却期内重试会延长封禁，最多 5 分钟） |
| 只支持 IPv4 | 官方文档原文 |
| **支持 ETH** | `skills/gmgn-token/SKILL.md` 有「Get basic info for an ETH token」「Get pool info for an ETH token」示例；`gmgn-market` 有「ETH Trenches Examples」 |

**已文档化的端点（节选）**

| 能力 | 端点 | 权重 |
|---|---|---|
| 代币信息 | `GET /v1/token/info` | 1 |
| 合约安全 | `GET /v1/token/security` | 1 |
| 池子信息 | `GET /v1/token/pool_info` | 1 |
| 持有人 | `GET /v1/market/token_top_holders` | 5 |
| 交易者（含 smart_degen 标签） | `GET /v1/market/token_top_traders` | 5 |
| 榜单 | `GET /v1/market/rank` | 3 |
| 信号 | `POST /v1/market/token_signal` | 1 |
| 钱包持仓/活动/统计/盈亏 | `GET /v1/user/wallet_holdings`、`/v1/user/wallet_activity`、`/v1/user/wallet_stats`、`POST /v1/user/wallet_profits` | 2–3 |

**实测**
- 直连 `https://gmgn.ai/api/v1/...` → 403 Cloudflare；`https://api.gmgn.ai/...` → SSL 超时。
- 官方做法是走 `gmgn-cli`（`npx` 包）而不是自己拼 HTTP。
- 无 key 时无法验证端点真实响应 → 状态：**文档级已验证（含限额与权重）/ 运行时未验证（缺 key）**。

**结论**：GMGN 是**有文档、有限额、支持 ETH** 的一等公民候选，但需要你本人申请 key（在 `gmgn.ai/ai` 上传公钥）。MVP 可以先用免费源跑通，GMGN 作为 Phase 1.5 的增强源接入。

---

## 9. 数据源之间的口径差异（必须显式处理）

| 差异 | 处理 |
|---|---|
| 市值：Blockscout `circulating_market_cap` vs DEX Screener `marketCap`/`fdv` | 两个都存，报告里注明来源；不一致超过 30% 标 `WARNING` |
| 流动性：DEX Screener `liquidity.usd` vs GeckoTerminal `reserve_in_usd` | 以 DEX Screener 为主，GeckoTerminal 仅做交叉核验 |
| 时间：GeckoTerminal 缓存 1 分钟；DEX Screener 实时；Blockscout 索引延迟 | 所有记录带 `retrieved_at`；回测只用 `retrieved_at <= signal_time` 的数据 |
| 持有人数：Blockscout `token_holders_count` vs GoPlus `holder_count` | 差异 >20% 标 `WARNING`，不取平均 |

---

## 10. 费用与合规摘要

- MVP 全部使用**免 key 源**（Blockscout / DEX Screener / GeckoTerminal / GoPlus / 公共 RPC），成本 0。
- 需遵守：GeckoTerminal ~10 次/分钟；Solana 公共 RPC 100/10s；Helius 免费档 1M credits；Etherscan 免费档需注册。
- 各服务条款禁止的行为（转售数据、绕过限流、冒充官方）不得做；README 不得暗示获得任何平台背书。
