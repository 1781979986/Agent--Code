"""只用假 Key 和注入 HTTP 客户端，验证模型、视觉、TTS 协议及取消。"""

import asyncio
import copy
import json
import tempfile
import unittest

import httpx2

from server.common import ROOT
from server.dataset_service import DatasetService
from server.model import AnalysisProvider
from server.multimodal_service import MultimodalService
from server.signal import CancelSignal, TurnCancelled
from server.storage import Storage
from test_project import DATASET, MAPPING, SESSION, SPEC, build_sql, local_query

ENV = {"DASHSCOPE_API_KEY": "unit-test", "DASHSCOPE_BASE_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1"}
FACTS = {"title": "看板", "start": None, "end": None, "metric": "unknown", "unit": "元", "region": None,
         "amount": 60000, "uncertainties": ["日期不清楚"]}


def client_factory(handler):
    return lambda **kwargs: httpx2.AsyncClient(transport=httpx2.MockTransport(handler), **kwargs)


def decision_response(decision):
    return {"choices": [{"message": {"content": json.dumps(decision, ensure_ascii=False)}}]}


class ModelTests(unittest.IsolatedAsyncioTestCase):
    async def test_json_fields_minimal_context_and_four_turn_history(self):
        requests = []
        async def handler(request):
            requests.append(request)
            return httpx2.Response(200, json=decision_response({"route": "query", "spec": SPEC, "sql": build_sql(SPEC), "message": "查询"}))
        provider = AnalysisProvider({"DEEPSEEK_API_KEY": "unit-test", "DEEPSEEK_MODEL": "test-model"}, client_factory=client_factory(handler))
        session = {**SESSION, "lastSpec": SPEC, "reports": [{"question": str(index), "answer": "摘要", "secret": "不发送"} for index in range(7)]}
        result = await provider.decide("问题", DATASET, session, "ai", CancelSignal(), "旧错误")
        self.assertEqual(result["spec"], SPEC)
        request = requests[0]; body = json.loads(request.content)
        self.assertEqual(str(request.url), "https://api.deepseek.com/chat/completions")
        self.assertEqual(request.headers["Authorization"], "Bearer unit-test")
        self.assertEqual(body["model"], "test-model"); self.assertEqual(body["thinking"], {"type": "disabled"})
        self.assertEqual(body["response_format"], {"type": "json_object"}); self.assertFalse(body["stream"])
        context = json.loads(body["messages"][1]["content"])
        self.assertEqual(len(context["history"]), 4); self.assertEqual(context["history"][0]["question"], "3")
        self.assertEqual(context["previousSpec"], SPEC); self.assertEqual(context["previousError"], "旧错误")
        self.assertEqual(set(context["dataset"]), {"start", "end", "regions", "products"})
        self.assertNotIn("secret", body["messages"][1]["content"])

    async def test_at_most_one_transport_retry_but_not_bad_model_json(self):
        calls = []
        async def handler(request):
            calls.append(request)
            return httpx2.Response(429, json={"message": "限流"}, headers={"retry-after-ms": "1"})
        provider = AnalysisProvider({"DEEPSEEK_API_KEY": "unit-test"}, client_factory=client_factory(handler))
        with self.assertRaisesRegex(RuntimeError, "限流"):
            await provider.decide("问题", DATASET, SESSION, "ai", CancelSignal())
        self.assertEqual(len(calls), 2)
        calls.clear()
        async def bad(request):
            calls.append(request)
            return httpx2.Response(200, json=decision_response({"route": "query", "spec": SPEC, "sql": None, "message": "查询"}))
        with self.assertRaisesRegex(ValueError, "缺少"):
            await AnalysisProvider({"DEEPSEEK_API_KEY": "unit-test"}, client_factory=client_factory(bad)).decide("问题", DATASET, SESSION, "ai", CancelSignal())
        self.assertEqual(len(calls), 1)

    async def test_invalid_dates_region_contribution_and_missing_key(self):
        for override, message in (({"start": "2026-10-01"}, "晚于"), ({"region": "不存在"}, "不属于"),
                                  ({"kind": "contribution"}, "明确区域"), ({"start": "2026-09-31"}, "日期")):
            async def handler(request):
                return httpx2.Response(200, json=decision_response({"route": "query", "spec": {**SPEC, **override}, "sql": "SELECT 1", "message": "查询"}))
            with self.subTest(override=override), self.assertRaisesRegex(ValueError, message):
                await AnalysisProvider({"DEEPSEEK_API_KEY": "unit-test"}, client_factory=client_factory(handler)).decide("问题", DATASET, SESSION, "ai", CancelSignal())
        with self.assertRaisesRegex(ValueError, "DEEPSEEK_API_KEY"):
            await AnalysisProvider({}).decide("问题", DATASET, SESSION, "ai", CancelSignal())

    async def test_canceled_request_cleans_up_transport_and_replay_is_offline(self):
        entered, closed = asyncio.Event(), asyncio.Event()
        async def handler(request):
            try:
                entered.set()
                await asyncio.Event().wait()
            finally:
                closed.set()
        signal = CancelSignal()
        provider = AnalysisProvider({"DEEPSEEK_API_KEY": "unit-test"}, client_factory=client_factory(handler))
        task = asyncio.create_task(provider.decide("问题", DATASET, SESSION, "ai", signal))
        await entered.wait(); signal.abort("用户停止")
        with self.assertRaisesRegex(TurnCancelled, "用户停止"):
            await task
        self.assertTrue(closed.is_set())
        self.assertEqual((await AnalysisProvider({}).decide("按月趋势", DATASET, SESSION, "replay", CancelSignal()))["route"], "query")


class MultimodalTests(unittest.IsolatedAsyncioTestCase):
    async def test_vision_real_bytes_unknown_values_and_no_retry(self):
        requests = []
        async def handler(request):
            requests.append(request)
            return httpx2.Response(200, json={"choices": [{"message": {"content": "```json\n" + json.dumps(FACTS, ensure_ascii=False) + "\n```"}}]})
        service = MultimodalService(Storage("/unused"), ENV, client_factory=client_factory(handler))
        result = await service.inspect((ROOT / "samples/dashboard.png").read_bytes(), "image/png", "ai", CancelSignal())
        self.assertEqual(result, FACTS)
        body = json.loads(requests[0].content)
        self.assertEqual(body["model"], "qwen3-vl-flash")
        self.assertTrue(body["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,"))
        self.assertIn("JSON", body["messages"][0]["content"])
        with self.assertRaisesRegex(ValueError, "口径不完整"):
            await service.compare("unused", result, CancelSignal())
        self.assertEqual(len(requests), 1)

    async def test_fixed_picture_hash_comparison_and_unit_conversion(self):
        with tempfile.TemporaryDirectory() as root:
            storage = Storage(root); datasets = DatasetService(storage)
            uploaded = datasets.upload((ROOT / "samples/sales-demo.xlsx").read_bytes(), "sales-demo.xlsx")
            dataset = datasets.import_data(uploaded["id"], "销售明细", MAPPING)["dataset"]
            service = MultimodalService(storage, {}, execute=local_query)
            data = (ROOT / "samples/dashboard.png").read_bytes()
            facts = await service.inspect(data, "image/png", "replay", CancelSignal())
            result = await service.compare(dataset["id"], facts, CancelSignal())
            self.assertEqual(result["total"], 60000); self.assertEqual(result["difference"], 0)
            result = await service.compare(dataset["id"], {**facts, "unit": "万元", "amount": 6}, CancelSignal())
            self.assertEqual(result["difference"], 0)
            result = await service.compare(dataset["id"], {**facts, "metric": "net"}, CancelSignal())
            self.assertEqual(result["difference"], 1000)
            with self.assertRaisesRegex(ValueError, "只支持"):
                await service.inspect(data + b"modified", "image/png", "replay", CancelSignal())
            for override in ({"uncertainties": ["缺口"]}, {"metric": "unknown"}, {"amount": None}, {"start": "2026-10-01"}):
                with self.assertRaisesRegex(ValueError, "口径不完整"):
                    await service.compare(dataset["id"], {**facts, **override}, CancelSignal())

    async def test_tts_region_payload_signed_url_errors_and_cancel(self):
        requests = []
        async def handler(request):
            requests.append(request)
            return httpx2.Response(200, json={"output": {"audio": {"url": "http://sample.oss-cn-beijing.aliyuncs.com/result.wav?signature=fake"}}})
        service = MultimodalService(Storage("/unused"), ENV, client_factory=client_factory(handler))
        audio = await service.speak("华东净销售额下降。", CancelSignal())
        self.assertEqual(audio, "https://sample.oss-cn-beijing.aliyuncs.com/result.wav?signature=fake")
        self.assertEqual(str(requests[0].url), "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation")
        self.assertEqual(json.loads(requests[0].content), {"model": "qwen3-tts-flash", "input": {"text": "华东净销售额下降。", "voice": "Cherry", "language_type": "Chinese"}})
        for url in ("https://evil.test/audio", "https://a.oss-cn-beijing.aliyuncs.com:444/a", "https://user:pass@a.oss-cn-beijing.aliyuncs.com/a"):
            async def bad(request):
                return httpx2.Response(200, json={"output": {"audio": {"url": url}}})
            with self.assertRaisesRegex(ValueError, "无效播放"):
                await MultimodalService(Storage("/unused"), ENV, client_factory=client_factory(bad)).speak("文字", CancelSignal())
        signal = CancelSignal(); signal.abort()
        with self.assertRaises(TurnCancelled):
            await service.speak("文字", signal)
        self.assertEqual(len(requests), 1)


if __name__ == "__main__":
    unittest.main()
