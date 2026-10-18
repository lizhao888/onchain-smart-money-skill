"""HTTP 层：节流 + 重试 + 可注入传输。

关键设计：`Http.get_json()` 只返回 (payload, status)。它**从不**把失败吞成空结果，
调用方必须自己判断 status。这让「真空 vs 取数失败」在类型层面就无法混淆。
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

USER_AGENT = "onchain-smart-money/0.1 (+https://github.com/)"


@dataclass
class Response:
    status: str                  # OK | EMPTY_CONFIRMED | FAILED
    payload: Any = None
    http_status: int = 0
    error: Optional[str] = None
    url: str = ""
    attempts: int = 1

    @property
    def ok(self) -> bool:
        return self.status == "OK"


class Http:
    """节流 + 有限重试的 JSON 客户端。

    fetch(url) -> (http_status, text) 可注入，测试时用来回放夹具。
    """

    def __init__(self, fetch: Optional[Callable[[str], tuple]] = None,
                 min_interval: float = 0.0, retries: int = 3,
                 backoff: float = 0.5, timeout: int = 30):
        self._fetch = fetch or self._urllib_fetch
        self.min_interval = min_interval
        self.retries = retries
        self.backoff = backoff
        self.timeout = timeout
        self._last = 0.0
        self.calls: list[str] = []

    def _urllib_fetch(self, url: str):
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                   "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")[:2000]

    def get_json(self, url: str) -> Response:
        wait = self.min_interval - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.time()
        self.calls.append(url)

        last_err = None
        for attempt in range(1, self.retries + 1):
            try:
                code, text = self._fetch(url)
            except Exception as e:                      # 网络层异常
                last_err = f"{type(e).__name__}: {e}"
                code, text = 0, ""
            if code == 200:
                try:
                    return Response("OK", json.loads(text), 200, None, url, attempt)
                except json.JSONDecodeError:
                    # 200 但结构不是 JSON：这是契约不符，不当空结果
                    return Response("FAILED", None, 200, "SCHEMA_MISMATCH", url, attempt)
            if code in (429, 500, 502, 503, 504, 0):
                last_err = f"HTTP {code}"
                time.sleep(self.backoff * attempt)
                continue
            # 4xx（非 429）= 确定性错误：不重试
            return Response("FAILED", None, code, f"HTTP {code}", url, attempt)

        return Response("FAILED", None, 0, last_err or "UNKNOWN", url, self.retries)
