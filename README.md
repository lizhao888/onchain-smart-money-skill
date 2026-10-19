# onchain-smart-money 🐎

**一套跑在命令行里的「链上聪明钱 7 步法」——找到那些在上轮 meme 行情里活下来、现在还在 actively 交易的钱包，看他们这周在买什么。**

> 圈内老炮的玩法：挑一个上轮涨过几百倍的 meme → 找出最早买入的前 20 个钱包 → 剔掉机器人 → 剩下 3~5 个活人就是「幸存聪明钱」→ **看他们最近在买什么** → 好几个幸存钱包同时买同一个币，这就是信号。
>
> 以前这套要开 GMGN、翻 Etherscan、逐个点开钱包，一个币查半天。现在一条命令。

![tests](https://img.shields.io/badge/tests-47%20passed-brightgreen) ![license](https://img.shields.io/badge/license-Apache--2.0-blue) ![chains](https://img.shields.io/badge/chains-ETH%20%7C%20BSC%20%7C%20Solana-orange) ![cost](https://img.shields.io/badge/API%20key-%E4%B8%8D%E9%9C%80%E8%A6%81-success)

---

## 这不是又一个行情工具

市面上的链上工具要么给你看 K 线，要么卖你「聪明钱标签」的黑盒。这个技能反着来：

- **每一条结论都带证据**——tx hash、区块、数据源、抓取时间，随手可上区块浏览器复核
- **失败不冒充**——查不到就写 `FAILED`（count=null），「真空」就写确认 0 条，绝不把故障包装成「没有风险」
- **数据缺失显式标注**——BSC 拿不到 30 天活跃？报告里写 `UNKNOWN`，不编数字
- **零成本零门槛**——全部免费公共数据源，不需要任何 API Key

## 三条命令

```bash
# ① 发射台扫描：全网新池 → 优质 meme 候选（7 步法第 1 步自动化）
python -m onchain_sm.cli scan --chain bsc --top 10

# ② 单币研究：早期买家 / 30 天活跃 / 风险闸门
python -m onchain_sm.cli run --token 0x... --chain bsc

# ③ 7 步法端到端：剔机器人 → 幸存者画像 → 共买信号
python -m onchain_sm.cli watch --token 0x... --chain ethereum
```

## 7 步法 ↔ 实现

| 步 | 原方法 | 对应实现 |
|---|---|---|
| 1 | 挑上轮火过的币 | `scan` 扫全部发射台毕业池（four.meme / Flap / pump.fun / PumpSwap…） |
| 2 | 找最早买入的前 20 个钱包 | `run --max-buyers 20` → `buyers[]` 每人带 tx hash |
| 3 | 筛 30 天还在交易的 | `activity[]`（swap/转账分开算，30 天窗口过滤） |
| 4 | 剔掉机器人 | 四特征打分：间隔中位数 / 同块多笔 / 笔数 / 金额整齐度 |
| 5 | **看幸存钱包在买什么**（最关键） | `portraits[].recent_buys[]`：每个钱包近期买入聚合 + 是否仍持有 |
| 6 | 多钱包同买 = 信号 | `signals[]`：≥3 个幸存钱包 7 天内同买，权重排序，同资金源合并防刷票 |
| 7 | 用自己的标准评估 | `score` = 四维基础分 + 信号加成，全部输入可追溯到链上证据 |

## 三链支持（同一套报告格式）

| 链 | 早期买家判定 | 已实测样本 |
|---|---|---|
| Ethereum | Transfer(池子→EOA) | PEPE / SHIB / USDT |
| BSC | 同上（公共 RPC getLogs） | 13 只 meme |
| Solana | 余额净增 + DEX 程序触达（20 个程序白名单） | 7 只 meme |

能力边界如实标注：BSC 无 Blockscout（30 天活跃=UNKNOWN）；BSC getLogs 有 archive 限制（只能扫近期）；Solana 公共 RPC 限流（只扫最近 ~100 笔签名）。**报告里写什么就是什么，不冒充全量。**

## 它不做什么

- ❌ 不自动交易、不签名、不读私钥（CI grep 门禁强制）
- ❌ 不承诺信号有效——`score` 只是研究排序，共买信号回测验证前只是研究线索
- ❌ 不做 K 线/行情图表（用你顺手的行情工具）
- ❌ 不需要你导入钱包、授权任何东西

## 快速开始

```bash
git clone https://github.com/lizhao888/onchain-smart-money-skill.git
cd onchain-smart-money-skill
python -m venv .venv && .venv\Scripts\activate     # Windows
pip install -e . pytest

python -m onchain_sm.cli doctor        # 数据源体检
python -m onchain_sm.cli run --token 0x6982508145454ce325ddbe47a25d4ec3d2311933 --out out
```

输出：`out/report-<symbol>-<时间>.json`——每个节点带 status / metrics / evidence / unknowns。

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
3. 只有「池子 → EOA」（EVM）或「DEX 触达 + 余额净增」（Solana）才算买入，转账/空投不算
4. 报告里每个买家都有可打开的 `tx_hash`

## 测试与质量

```bash
pytest -q -m "not live"    # 47 项离线测试（全部用真实响应体录制的夹具）
pytest -q -m live          # 联网冒烟（可选）
coverage run -m pytest -m "not live" && coverage report
```

## 作为 Agent Skill 使用

`skills/onchain-smart-money-research/` 是标准 Agent Skill 格式（YAML frontmatter + references），Hermes 等 Agent 客户端可直接加载，让 Agent 听懂「帮我查这个币的早期买家」这类指令。

## 文档

- [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md) — 7 步法 ↔ 实现对照 + BSC 发射台三代演化
- [`MVP_ACCEPTANCE.md`](MVP_ACCEPTANCE.md) — 每条规则的机器判定方式
- [`DATA_SOURCES.md`](DATA_SOURCES.md) — 每个数据源的实测证据等级（A/B/C/D）
- [`SELF_AUDIT.md`](SELF_AUDIT.md) — 开发者自审（含被推翻的错误结论）

## License

Apache-2.0（允许闭源商用）

> **免责声明**：本工具只做只读链上研究，所有输出是「观察到了什么 + 证据在哪」，不构成投资建议。meme 币风险极高，信号 ≠ 买入建议。
