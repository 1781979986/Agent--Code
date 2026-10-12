"""模型得到必要元信息和最近四轮摘要，不收到原始整表；Replay 与 AI 明确分开。"""

import json
import os

import httpx2

from .analysis import build_sql, replay_decision
from .common import json_text
from .http_client import post_json
from .prompts import ANALYSIS_INSTRUCTIONS
from .schemas import Decision


class AnalysisProvider:
    def __init__(self, env=None, *, client_factory=httpx2.AsyncClient):
        self.env = os.environ if env is None else env
        self.client_factory = client_factory

    async def decide(self, question, dataset, session, mode, signal, previous_error=""):
        signal.throw_if_aborted()
        if mode == "replay":
            return replay_decision(question, dataset, session["lastSpec"])
        if not self.env.get("DEEPSEEK_API_KEY"):
            raise ValueError("AI 分析需要配置 DEEPSEEK_API_KEY；也可以先选 Replay 演示")
        body = {"model": self.env.get("DEEPSEEK_MODEL") or "deepseek-v4-flash", "stream": False,
                "response_format": {"type": "json_object"}, "thinking": {"type": "disabled"},
                "messages": [{"role": "system", "content": ANALYSIS_INSTRUCTIONS}, {"role": "user", "content": json_text({
                    "question": question, "dataset": {key: dataset[key] for key in ("start", "end", "regions", "products")},
                    "previousSpec": session["lastSpec"], "history": [{"question": item["question"], "answer": item["answer"]} for item in session["reports"][-4:]],
                    "previousError": previous_error})}]}
        status, result = await post_json((self.env.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com").rstrip("/") + "/chat/completions",
                                        self.env["DEEPSEEK_API_KEY"], body, signal, timeout=90, retries=1, client_factory=self.client_factory)
        if status >= 400:
            raise RuntimeError((result.get("error") or {}).get("message") or result.get("message") or f"模型请求失败 HTTP {status}")
        choices = result.get("choices") or []
        content = (choices[0].get("message") or {}).get("content") if choices else "{}"
        decision = Decision.model_validate(json.loads(content or "{}")).model_dump()
        if decision["route"] == "query":
            spec = decision["spec"]
            if not spec or not decision["sql"]:
                raise ValueError("查询决策缺少 spec 或 sql")
            if spec["start"] > spec["end"]:
                raise ValueError("开始日期晚于结束日期")
            if spec["region"] and spec["region"] not in dataset["regions"]:
                raise ValueError("所选区域不属于当前数据集")
            if spec["kind"] == "contribution" and not spec["region"]:
                raise ValueError("商品降幅需要明确区域")
        return decision


def verification_sql(decision):
    if not decision["spec"]:
        raise ValueError("缺少已确认的分析口径")
    return build_sql(decision["spec"])
