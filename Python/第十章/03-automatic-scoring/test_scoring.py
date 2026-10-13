"""完全离线验证：假模型、合成资料、本地 HTTP，不加载环境文件或真实密钥。"""

from contextlib import redirect_stdout
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from urllib.error import URLError

from agent_service import AgentService, execute_order_tool
from evaluation_service import (
    EvaluationService, JUDGE_PROMPT, RUBRIC_VERSION, build_judge_input, check_rules, parse_judge,
)
from examples import teaching_examples
from main import run_demo
from model_service import ModelService
from sample import DATA_DIR, load_case, parse_json, validate_order, validate_sample


CASE = load_case()
SAMPLE, FIXTURE = CASE["sample"], CASE["fixture"]
EXAMPLES = teaching_examples(SAMPLE, FIXTURE["orders"])
GOOD = EXAMPLES[3]["run"]


def message(content, tool_calls=None):
    result = {"role": "assistant", "content": content, "refusal": None}
    if tool_calls is not None:
        result["tool_calls"] = tool_calls
    return result


def order_call(arguments='{"orderId":"A1024"}', *, identity="call-order", name="get_order"):
    return {"id": identity, "type": "function", "function": {"name": name, "arguments": arguments}}


def fail(reason):
    raise RuntimeError(reason)


def judge_message(score=2, *, answer=GOOD["answer"], reason="测试评分", quotes=None):
    return message(json.dumps({"score": score, "reason": reason,
                               "quotes": [answer] if quotes is None else quotes}, ensure_ascii=False))


class FakeModel(ModelService):
    def __init__(self, reply):
        super().__init__(environment={"DEEPSEEK_API_KEY": "local-test-key",
                                      "DEEPSEEK_MODEL": "local-agent", "JUDGE_MODEL": "local-judge"})
        self.reply = reply
        self.calls = []

    def complete(self, role, request):
        # 保存每轮当时的历史，不能让后续追加消息改变第一轮记录。
        self.calls.append(deepcopy({"role": role, "request": request}))
        return self.reply(role, request)


def demo_reply(role, request):
    if role == "judge":
        answer = json.loads(request["messages"][1]["content"])["answer"]
        # 人工设定分数，仅用于验证应用怎么处理返回值，不实现“假语义评分器”。
        scores = {item["run"]["answer"]: item["expectedScore"] for item in EXAMPLES[1:]}
        return judge_message(scores.get(answer, 2), answer=answer)
    if any(item["role"] == "tool" for item in request["messages"]):
        return message("订单 A1024 的实付金额为 3500 元。")
    return message(None, [order_call()])


def completion(reply, finish_reason=None):
    return {"id": "mock-completion", "object": "chat.completion", "created": 1, "model": "local-test",
            "choices": [{"index": 0, "message": reply,
                         "finish_reason": finish_reason or ("tool_calls" if reply.get("tool_calls") else "stop")}]}


class LocalAPI:
    """只监听自动分配的 loopback 端口；测试结束主动关闭，不使用用户已有服务。"""
    def __init__(self, reply):
        self.reply = reply
        self.requests = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                owner.requests.append({"path": self.path, "authorization": self.headers.get("Authorization"),
                                       "contentType": self.headers.get("Content-Type"), "body": body})
                result = owner.reply(body)
                status, payload = result if isinstance(result, tuple) else (200, result)
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def model(self, suffix=""):
        return ModelService(environment={"DEEPSEEK_API_KEY": "local-test-key",
                                         "DEEPSEEK_BASE_URL": f"http://127.0.0.1:{self.server.server_port}{suffix}",
                                         "DEEPSEEK_MODEL": "local-agent", "JUDGE_MODEL": "local-judge"})


class SampleTests(unittest.TestCase):
    def test_reuses_only_python_02_data(self):
        self.assertEqual(SAMPLE["id"], "order-amount")
        self.assertEqual(SAMPLE["referenceOutputs"]["turns"][0]["facts"]["paidAmountYuan"], 3500)
        self.assertEqual(FIXTURE["orders"][1]["paidAmountYuan"], 800)
        self.assertEqual(set(FIXTURE), {"id", "orders"})
        self.assertEqual(DATA_DIR, Path(__file__).resolve().parent.parent / "02-evaluation-dataset" / "data")

    def test_exact_sample_and_fixture_match_required(self):
        dataset = parse_json((DATA_DIR / "dataset.json").read_text(encoding="utf-8"))
        fixtures = parse_json((DATA_DIR / "fixtures.json").read_text(encoding="utf-8"))
        changes = [
            ({**dataset, "cases": []}, fixtures),
            ({**dataset, "cases": [SAMPLE, SAMPLE]}, fixtures),
            (dataset, []),
            (dataset, [fixtures[0], fixtures[0]]),
        ]
        bad_orders = deepcopy(fixtures)
        bad_orders[0]["orders"].append(deepcopy(bad_orders[0]["orders"][0]))
        changes.append((dataset, bad_orders))
        for field, value in (("paidAmountYuan", 1), ("userId", "other"), ("tenantId", "other")):
            changed = deepcopy(fixtures)
            changed[0]["orders"][0][field] = value
            changes.append((dataset, changed))
        for data, fixture in changes:
            with self.subTest(data=data, fixture=fixture), self.assertRaises(ValueError):
                load_case(dataset=data, fixtures=fixture)

    def test_sample_schema_rejects_extra_fields_and_multiple_turns(self):
        changes = []
        changed = deepcopy(SAMPLE)
        changed["inputs"]["turns"].append("继续追问")
        changes.append(changed)
        for location in ((), ("inputs",), ("inputs", "context"), ("referenceOutputs",)):
            changed = deepcopy(SAMPLE)
            target = changed
            for key in location:
                target = target[key]
            target["unexpected"] = True
            changes.append(changed)
        changed = deepcopy(SAMPLE)
        changed["referenceOutputs"]["turns"][0]["criteria"] = []
        changes.append(changed)
        for changed in changes:
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                validate_sample(changed)

    def test_order_schema_and_trim_match(self):
        order = deepcopy(FIXTURE["orders"][0])
        order["orderId"] = "\ufeff A1024 \u3000"
        self.assertEqual(validate_order(order)["orderId"], "A1024")
        for value in (True, False, "3500", -1, float("inf"), float("nan")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_order({**order, "paidAmountYuan": value})
        with self.assertRaises(ValueError):
            validate_order({**order, "extra": "not-allowed"})

    def test_json_constants_rejected(self):
        for value in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_json(value)


class ToolAndAgentTests(unittest.TestCase):
    def test_application_identity_and_strict_args(self):
        for name, args in (("delete_order", {"orderId": "A1024"}),
                           ("get_order", {"orderId": "A1024", "userId": "other"}),
                           ("get_order", {}), ("get_order", {"orderId": 1024}),
                           ("get_order", {"orderId": "  "}), ("get_order", [])):
            with self.subTest(name=name, args=args):
                result = execute_order_tool(name, args, SAMPLE["inputs"]["context"], FIXTURE["orders"])
                self.assertEqual(result["result"], {"found": False, "error": "工具名称或参数不合法。"})
        for field in ("tenantId", "userId"):
            context = {**SAMPLE["inputs"]["context"], field: "other"}
            self.assertEqual(execute_order_tool("get_order", {"orderId": "A1024"}, context,
                                                FIXTURE["orders"])["result"], {"found": False})

    def test_raw_arguments_preserved_and_result_is_copy(self):
        args = {"orderId": " A1024 "}
        record = execute_order_tool("get_order", args, SAMPLE["inputs"]["context"], FIXTURE["orders"])
        self.assertEqual(record["args"], args)
        record["result"]["order"]["paidAmountYuan"] = 1
        self.assertEqual(FIXTURE["orders"][0]["paidAmountYuan"], 3500)
        self.assertEqual(execute_order_tool("get_order", {"orderId": "A9999"}, SAMPLE["inputs"]["context"],
                                            FIXTURE["orders"])["result"], {"found": False})

    def test_tool_history_and_no_reference_leak(self):
        model = FakeModel(demo_reply)
        run = AgentService(model).run(deepcopy(SAMPLE["inputs"]), deepcopy(FIXTURE["orders"]))
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["toolCalls"][0]["result"]["order"]["paidAmountYuan"], 3500)
        self.assertEqual([call["role"] for call in model.calls], ["agent", "agent"])
        first, second = [call["request"] for call in model.calls]
        self.assertEqual([item["role"] for item in first["messages"]], ["system", "user"])
        self.assertEqual([item["role"] for item in second["messages"]], ["system", "user", "assistant", "tool"])
        self.assertNotIn("3500", json.dumps(first))
        self.assertNotIn("referenceOutputs", json.dumps(first))
        self.assertNotIn("user-001", json.dumps(first))
        tool = second["messages"][-1]
        self.assertEqual(tool["tool_call_id"], "call-order")
        self.assertEqual(json.loads(tool["content"]), run["toolCalls"][0]["result"])

    def test_illegal_arguments_return_to_model_and_four_round_limit(self):
        model = FakeModel(lambda *_: message(None, [order_call("not-json")]))
        run = AgentService(model).run(SAMPLE["inputs"], FIXTURE["orders"])
        self.assertEqual(run["status"], "error")
        self.assertEqual(run["error"], "模型调用轮次已达到本例上限。")
        self.assertEqual(len(model.calls), 4)
        self.assertEqual(len(run["toolCalls"]), 4)
        self.assertEqual(run["toolCalls"][0]["args"], "not-json")
        self.assertIn("参数", json.loads(model.calls[1]["request"]["messages"][-1]["content"])["error"])

    def test_six_tool_limit_and_unsupported_tool_type(self):
        model = FakeModel(lambda *_: message(None, [order_call(identity=str(i)) for i in range(7)]))
        run = AgentService(model).run(SAMPLE["inputs"], FIXTURE["orders"])
        self.assertEqual(run["error"], "工具调用次数已达到本例上限。")
        self.assertEqual(len(run["toolCalls"]), 6)
        model = FakeModel(lambda *_: message(None, [{**order_call(), "type": "custom"}]))
        run = AgentService(model).run(SAMPLE["inputs"], FIXTURE["orders"])
        self.assertEqual(run["error"], "本例只支持 Function Tool。")

    def test_agent_error_preserves_previous_tool_records(self):
        model = FakeModel(lambda _role, request: fail("服务不可用")
                          if len(request["messages"]) > 2 else message(None, [order_call()]))
        run = AgentService(model).run(SAMPLE["inputs"], FIXTURE["orders"])
        self.assertEqual(run["status"], "error")
        self.assertEqual(run["answer"], "")
        self.assertEqual(len(run["toolCalls"]), 1)
        result = EvaluationService(model).evaluate(SAMPLE, run)
        self.assertEqual(result, {"status": "agent_error", "rules": [],
                                  "judge": {"status": "skipped", "reason": "服务不可用"}})
        self.assertEqual(len(model.calls), 2)

    def test_direct_answer_without_tool_fails_rules(self):
        model = FakeModel(lambda *_: message("订单 A1024 实付 3500 元。"))
        run = AgentService(model).run(SAMPLE["inputs"], FIXTURE["orders"])
        self.assertEqual(run["status"], "completed")
        result = EvaluationService(model).evaluate(SAMPLE, run)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["judge"]["status"], "skipped")
        self.assertEqual(len(model.calls), 1)


class EvaluationTests(unittest.TestCase):
    def test_valid_record_and_paraphrase_pass_rules(self):
        self.assertTrue(all(rule["passed"] for rule in check_rules(SAMPLE, GOOD)))

    def test_wrong_target_missing_calls_empty_answer_and_bad_data(self):
        self.assertFalse(check_rules(SAMPLE, EXAMPLES[0]["run"])[0]["passed"])
        self.assertFalse(check_rules(SAMPLE, {**GOOD, "toolCalls": []})[0]["passed"])
        self.assertFalse(check_rules(SAMPLE, {**GOOD, "answer": "\ufeff \u3000"})[2]["passed"])
        for change in ({"paidAmountYuan": 1}, {"paidAmountYuan": True}, {"userId": "other"},
                       {"tenantId": "other"}, {"orderId": "A1025"}, {"unexpected": True}):
            with self.subTest(change=change):
                run = deepcopy(GOOD)
                run["toolCalls"][0]["result"]["order"].update(change)
                self.assertFalse(check_rules(SAMPLE, run)[1]["passed"])
        for change in ({"found": False}, {"found": 1}, {"error": "查询失败"}, {"order": None}):
            run = deepcopy(GOOD)
            run["toolCalls"][0]["result"].update(change)
            self.assertFalse(check_rules(SAMPLE, run)[1]["passed"])

    def test_wrong_then_correct_fails_but_repeated_target_allowed(self):
        wrong = EXAMPLES[0]["run"]["toolCalls"][0]
        self.assertFalse(check_rules(SAMPLE, {**GOOD, "toolCalls": [wrong, *GOOD["toolCalls"]]})[0]["passed"])
        repeated = {**GOOD, "toolCalls": GOOD["toolCalls"] * 2}
        self.assertTrue(all(rule["passed"] for rule in check_rules(SAMPLE, repeated)))

    def test_unsupported_promise_requires_content_judge(self):
        self.assertTrue(all(rule["passed"] for rule in check_rules(SAMPLE, EXAMPLES[1]["run"])))
        self.assertTrue(all(rule["passed"] for rule in check_rules(SAMPLE, EXAMPLES[2]["run"])))

    def test_failed_rules_skip_judge(self):
        model = FakeModel(lambda *_: fail("不应调用模型"))
        result = EvaluationService(model).evaluate(SAMPLE, EXAMPLES[0]["run"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["judge"]["status"], "skipped")
        self.assertEqual(model.calls, [])

    def test_score_statuses_and_independent_judge_request(self):
        for score, status in ((2, "passed"), (1, "failed"), (0, "failed"), (None, "needs_review")):
            with self.subTest(score=score):
                model = FakeModel(lambda *_: judge_message(score))
                result = EvaluationService(model).evaluate(SAMPLE, GOOD)
                self.assertEqual(result["status"], status)
                call = model.calls[0]
                self.assertEqual(call["role"], "judge")
                self.assertEqual(call["request"]["response_format"], {"type": "json_object"})
                self.assertNotIn("tools", call["request"])
                self.assertEqual([item["role"] for item in call["request"]["messages"]], ["system", "user"])
                self.assertEqual(call["request"]["messages"][0]["content"], JUDGE_PROMPT)

    def test_invalid_json_schema_and_quotes_are_evaluation_errors(self):
        invalid = ["", "not json", "```json\n{}\n```", '{"score":100}',
                   json.dumps({"score": 2, "reason": "引用错误", "quotes": ["不存在的原文"]})]
        for value in invalid:
            with self.subTest(value=value):
                result = EvaluationService(FakeModel(lambda *_: message(value))).evaluate(SAMPLE, GOOD)
                self.assertEqual(result["status"], "evaluation_error")
                self.assertEqual(result["judge"]["status"], "error")
                self.assertNotIn("result", result["judge"])
        result = EvaluationService(FakeModel(lambda *_: fail("网络超时"))).evaluate(SAMPLE, GOOD)
        self.assertEqual(result["status"], "evaluation_error")

    def test_strict_judge_fields_types_and_quote_limit(self):
        base = {"score": 2, "reason": "正确", "quotes": [GOOD["answer"]]}
        changes = [{"score": True}, {"score": False}, {"score": "2"}, {"score": 2.5},
                   {"reason": " \ufeff"}, {"reason": 2}, {"quotes": []}, {"quotes": [""]},
                   {"quotes": [1]}, {"quotes": [GOOD["answer"]] * 6}, {"extra": True}]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                parse_judge(json.dumps({**base, **change}), GOOD["answer"])
        self.assertEqual(parse_judge(json.dumps({"score": None, "reason": "需核对", "quotes": []}),
                                     GOOD["answer"])["score"], None)
        with self.assertRaisesRegex(ValueError, "不存在"):
            parse_judge(json.dumps({"score": None, "reason": "需核对", "quotes": ["不存在"]}), GOOD["answer"])

    def test_reason_trim_and_quotes_not_trimmed(self):
        result = parse_judge(json.dumps({"score": 2, "reason": " \ufeff 正确 \u3000", "quotes": ["三千五百元"]}),
                             GOOD["answer"])
        self.assertEqual(result["reason"], "正确")
        with self.assertRaises(ValueError):
            parse_judge(json.dumps({"score": 2, "reason": "正确", "quotes": [" 三千五百元 "]}), GOOD["answer"])

    def test_judge_input_has_no_human_expected_score(self):
        value = build_judge_input(SAMPLE, GOOD)
        self.assertEqual(list(value), ["question", "reference", "toolResults", "answer"])
        self.assertNotIn("expectedScore", json.dumps(value))
        self.assertNotIn("correct-paraphrase", json.dumps(value))

    def test_teaching_examples_have_independent_records(self):
        examples = teaching_examples(SAMPLE, FIXTURE["orders"])
        examples[1]["run"]["toolCalls"][0]["result"]["order"]["paidAmountYuan"] = 1
        self.assertEqual(examples[2]["run"]["toolCalls"][0]["result"]["order"]["paidAmountYuan"], 3500)
        self.assertTrue(all(item["run"]["origin"] == "teaching_example" for item in examples))


class ModelAndEntryTests(unittest.TestCase):
    def test_defaults_and_configuration_check_do_not_send_requests(self):
        model = ModelService(environment={}, open_request=lambda *_args, **_kwargs: fail("不能发请求"))
        self.assertEqual(model.settings(), {"agent": "deepseek-flash", "judge": "deepseek-flash"})
        for key in (None, "", "  "):
            model.environment = {} if key is None else {"DEEPSEEK_API_KEY": key}
            with self.assertRaisesRegex(ValueError, "DEEPSEEK_API_KEY"):
                model.complete("agent", {"messages": []})
        model.environment = {"DEEPSEEK_MODEL": "custom"}
        self.assertEqual(model.settings(), {"agent": "custom", "judge": "custom"})

    def test_timeout_in_seconds_and_network_failure_not_retried(self):
        calls = []

        def unavailable(request, *, timeout):
            calls.append((request, timeout))
            raise URLError("离线测试模拟连接失败")

        model = ModelService(environment={"DEEPSEEK_API_KEY": "local-test-key"}, open_request=unavailable)
        with self.assertRaisesRegex(RuntimeError, "网络连接异常或超时"):
            model.complete("agent", {"messages": [{"role": "user", "content": "测试"}]})
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1], 45)

    def test_local_http_agent_tool_and_judge_integration(self):
        def reply(body):
            role = "judge" if body.get("response_format") else "agent"
            return completion(demo_reply(role, body))

        with LocalAPI(reply) as api:
            model = api.model("/v1/")
            run = AgentService(model).run(SAMPLE["inputs"], FIXTURE["orders"])
            evaluation = EvaluationService(model).evaluate(SAMPLE, run)
            self.assertEqual(evaluation["status"], "passed")
            requests = api.requests
        self.assertEqual([item["body"]["model"] for item in requests], ["local-agent", "local-agent", "local-judge"])
        for item in requests:
            self.assertEqual(item["path"], "/v1/chat/completions")
            self.assertEqual(item["authorization"], "Bearer local-test-key")
            self.assertEqual(item["contentType"], "application/json")
            self.assertEqual(item["body"]["thinking"], {"type": "disabled"})
            self.assertEqual(item["body"]["max_tokens"], 1600)
            self.assertEqual(item["body"]["temperature"], 0)
            self.assertIs(item["body"]["stream"], False)
        self.assertNotIn("tools", requests[2]["body"])
        self.assertEqual(requests[2]["body"]["response_format"], {"type": "json_object"})

    def test_http_429_not_retried(self):
        with LocalAPI(lambda _: (429, {"error": {"message": "test rate limit"}})) as api:
            with self.assertRaisesRegex(RuntimeError, "HTTP 429"):
                api.model().complete("judge", {"messages": []})
            self.assertEqual(len(api.requests), 1)

    def test_truncated_or_missing_completion_rejected(self):
        values = [{"choices": []}, {"choices": [{"finish_reason": "stop"}]},
                  completion(message("未完成"), "length"), completion(message("被过滤"), "content_filter"),
                  completion(message("中断"), "aborted")]
        with LocalAPI(lambda _: values.pop(0)) as api:
            model = api.model()
            for _ in range(5):
                with self.assertRaises(ValueError):
                    model.complete("agent", {"messages": []})

    def test_full_entry_report_and_six_local_requests(self):
        def reply(body):
            return completion(demo_reply("judge" if body.get("response_format") else "agent", body))

        with tempfile.TemporaryDirectory() as temporary, LocalAPI(reply) as api:
            output = Path(temporary) / "outputs" / "latest.json"
            with redirect_stdout(io.StringIO()):
                exit_code = run_demo(api.model(), output_path=output)
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(exit_code, 0)
            self.assertEqual(len(api.requests), 6)
            self.assertEqual(report["rubricVersion"], RUBRIC_VERSION)
            self.assertEqual(report["datasetVersion"], CASE["datasetVersion"])
            self.assertEqual(report["fixtureId"], FIXTURE["id"])
            self.assertEqual(report["caseId"], SAMPLE["id"])
            self.assertEqual(report["actual"]["run"]["origin"], "live")
            self.assertEqual(report["actual"]["evaluation"]["status"], "passed")
            self.assertEqual(len(report["comparisons"]), 4)
            self.assertTrue(all(item["agreement"] for item in report["comparisons"]))
            self.assertNotIn("successRate", report)
            self.assertNotIn("local-test-key", json.dumps(report))

    def test_valid_failed_or_disagreeing_evaluations_exit_zero(self):
        model = FakeModel(lambda role, request: demo_reply(role, request) if role == "agent"
                          else judge_message(0, answer=json.loads(request["messages"][1]["content"])["answer"]))
        with tempfile.TemporaryDirectory() as temporary, redirect_stdout(io.StringIO()):
            output = Path(temporary) / "latest.json"
            self.assertEqual(run_demo(model, output_path=output), 0)
            report = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(report["actual"]["evaluation"]["status"], "failed")
        self.assertFalse(report["comparisons"][3]["agreement"])

    def test_technical_errors_save_report_and_exit_one(self):
        replies = [lambda role, request: fail("Agent 离线异常") if role == "agent" else demo_reply(role, request),
                   lambda role, request: message("bad-json") if role == "judge" else demo_reply(role, request)]
        for reply, status in zip(replies, ("agent_error", "evaluation_error")):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as temporary, redirect_stdout(io.StringIO()):
                output = Path(temporary) / "latest.json"
                self.assertEqual(run_demo(FakeModel(reply), output_path=output), 1)
                report = json.loads(output.read_text(encoding="utf-8"))
                self.assertEqual(report["actual"]["evaluation"]["status"], status)


if __name__ == "__main__":
    unittest.main()
