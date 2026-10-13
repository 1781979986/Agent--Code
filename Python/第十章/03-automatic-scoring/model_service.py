"""使用标准库调用 Chat Completions，答题和评分共用接口但不共用消息。"""

import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from sample import parse_json


class ModelService:
    def __init__(self, *, environment=None, open_request=None):
        # 只读取进程变量，不自动打开环境文件。测试注入假配置和本地请求函数。
        self.environment = os.environ if environment is None else environment
        self.open_request = urlopen if open_request is None else open_request

    def settings(self):
        agent = self.environment.get("DEEPSEEK_MODEL") or "deepseek-flash"
        return {"agent": agent, "judge": self.environment.get("JUDGE_MODEL") or agent}

    def assert_configured(self):
        key = self.environment.get("DEEPSEEK_API_KEY")
        if not key or not key.strip():
            raise ValueError("请先向当前进程加载 DEEPSEEK_API_KEY，运行方式见 README.md。")

    def complete(self, role, request):
        """超时、HTTP 错误和截断直接报告，不自动重试、不把缺失结果当作答案。"""
        self.assert_configured()
        if role not in ("agent", "judge"):
            raise ValueError("模型角色须为 agent 或 judge。")
        params = {**request, "model": self.settings()[role], "stream": False,
                  "max_tokens": 1600, "temperature": 0, "thinking": {"type": "disabled"}}
        base_url = self.environment.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com"
        http_request = Request(
            base_url.rstrip("/") + "/chat/completions",
            data=json.dumps(params, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.environment['DEEPSEEK_API_KEY']}",
                     "Content-Type": "application/json"},
            method="POST",
        )
        try:
            # 原例 timeout=45_000 毫秒；urllib 的 timeout 单位为秒。
            with self.open_request(http_request, timeout=45) as response:
                completion = parse_json(response.read().decode("utf-8"))
        except HTTPError as error:
            # 不回显请求头或密钥，保留 HTTP 状态供定位认证、限流等问题。
            error.close()
            raise RuntimeError(f"模型请求失败：HTTP {error.code}") from error
        except (URLError, TimeoutError) as error:
            raise RuntimeError("模型请求失败：网络连接异常或超时。") from error

        choices = completion.get("choices") if isinstance(completion, dict) else None
        choice = choices[0] if isinstance(choices, list) and choices else None
        finish_reason = choice.get("finish_reason") if isinstance(choice, dict) else None
        if finish_reason not in ("stop", "tool_calls"):
            raise ValueError(f"模型没有完整返回：{finish_reason or 'empty_choices'}")
        if not isinstance(choice.get("message"), dict):
            raise ValueError("模型没有返回有效消息。")
        return choice["message"]
