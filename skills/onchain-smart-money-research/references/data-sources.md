# 数据源速查（详见仓库根 DATA_SOURCES.md）

| Provider | 端点 | key | 限额（证据等级） |
|---|---|---|---|
| Blockscout | `/api/v2/tokens/{ca}`、`/api/v2/addresses/{a}/token-transfers`、`/api?module=logs&action=getLogs` | 否 | 无公开文档（实测 ~60–80 次后 429）**C** |
| DEX Screener | `/latest/dex/tokens/{ca}` | 否 | 核心端点限额未成文；token-profiles 60/min **C** |
| GeckoTerminal | `/api/v2/networks/eth/...` | 否 | ~10 次/分钟、缓存 1 分钟 **A（官方明文）** |
| GoPlus | `/api/v1/token_security/1?contract_addresses=` | 否（实测） | 未公布 **C** |
| 公共 RPC | `eth_getLogs`、`eth_getCode` | 否 | drpc/mevblocker 可用；publicnode 对 getLogs 403 |

**踩坑**：Blockscout 双 topic 过滤必须带 `topic0_1_opr=and`，否则 HTTP 200 但 `result=null`，
看起来像「没有记录」——这是最容易伪造出假结论的地方。
