# Contributing to onchain-smart-money

## 开发环境

```bash
git clone <repo> && cd onchain-smart-money-skill
python -m venv .venv && .venv/Scripts/activate   # Windows
pip install -e . pytest coverage
pytest -q -m "not live"        # 全部离线测试必须绿
```

## 提交规则

1. **离线测试优先**：所有新功能必须带离线夹具测试（tests/fixtures/），不允许依赖真实网络；
2. **失败不冒充**：新数据路径必须区分 FAILED（count=null）/ EMPTY_CONFIRMED（count=0）/ UNKNOWN；写 0 冒充失败 = 直接拒绝；
3. **只读红线**：禁止引入任何签名、swap、转账代码（CI grep 门禁会拦截）；
4. **providers 统一返回**：新数据源必须走 `Http` 封装并返回 `(data, Response)` 结构；
5. **评分变更**：权重/阈值改动必须同步更新 `MVP_ACCEPTANCE.md §1bis` 并在 PR 说明理由。

## 新增一条链

1. 在 `providers/` 下新建适配器（复用统一 Response 结构）；
2. 在 `pipeline/run.py` 增加链分支（买家判定逻辑按账本模型定制）；
3. `cli.py` SUPPORTED_CHAINS 白名单 + 测试；
4. 至少 3 个真网样本深度验证 + 离线夹具。

## 报告漏洞

见 SECURITY.md（不要用公开 Issue）。
