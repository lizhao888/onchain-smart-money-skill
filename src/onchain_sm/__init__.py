"""onchain_sm — 证据优先的链上研究工具（只读）。

设计约束（与 IMPLEMENTATION_PLAN.md 一致）：
1. 所有外部请求都走 http.Http，便于测试时注入假传输。
2. 取数失败 ≠ 空结果：状态必须显式（FAILED / EMPTY_CONFIRMED / PARTIAL）。
3. 不接触私钥，不签名，不下单。
"""
__version__ = "0.1.0"
