"""离线验证样本准备；不执行 Agent，也不把人工参考当作模型输出。"""

from copy import deepcopy
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from uuid import UUID

from dataset_service import DatasetService, read_data, validate_dataset, validate_fixtures
from main import main


class DatasetTests(unittest.TestCase):
    def test_sample_coverage_and_original_order(self):
        service = DatasetService()
        self.assertEqual([item["id"] for item in service.list()], [
            "order-amount", "missing-order-id", "order-not-found", "warranty-unknown",
            "refund-at-threshold", "refund-over-threshold", "follow-up-refund"])
        self.assertEqual(len(service.prepare("follow-up-refund")["inputs"]["turns"]), 2)
        self.assertEqual(service.get_reference("missing-order-id")["turns"][0]["behavior"], "clarify")
        self.assertEqual(service.get_reference("warranty-unknown")["turns"][0]["behavior"], "insufficient_evidence")
        self.assertIs(service.get_reference("refund-at-threshold")["turns"][0]["facts"]["requiresManualReview"], False)
        self.assertIs(service.get_reference("refund-over-threshold")["turns"][0]["facts"]["requiresManualReview"], True)

    def test_reference_separation_at_every_input_level(self):
        service = DatasetService()
        for sample in service.list():
            run = service.prepare(sample["id"])
            self.assertEqual(set(run), {"runId", "datasetVersion", "caseId", "inputs", "environment"})
            self.assertEqual(set(run["inputs"]), {"context", "turns"})
            self.assertNotIn("referenceOutputs", json.dumps(run))
        self.assertEqual(service.get_reference("order-amount")["turns"][0]["facts"]["paidAmountYuan"], 3500)

    def test_independent_uuid_orders_input_messages_memory_and_policy(self):
        service = DatasetService()
        first = service.prepare("order-amount")
        first["environment"]["orders"][0]["paidAmountYuan"] = 1
        first["environment"]["policy"]["manualReviewAboveYuan"] = 0
        first["environment"]["messages"].append({"role": "assistant", "content": "旧回答"})
        first["environment"]["memories"]["preference"] = "旧记忆"
        first["inputs"]["turns"][0] = "被修改的问题"
        first["inputs"]["context"]["userId"] = "another-user"
        second = service.prepare("order-amount")
        self.assertNotEqual(first["runId"], second["runId"])
        self.assertEqual(UUID(second["runId"]).version, 4)
        self.assertEqual(second["environment"]["orders"][0]["paidAmountYuan"], 3500)
        self.assertEqual(second["environment"]["policy"]["manualReviewAboveYuan"], 2000)
        self.assertEqual(second["environment"]["messages"], [])
        self.assertEqual(second["environment"]["memories"], {})
        self.assertEqual(second["inputs"]["turns"][0], "帮我查一下订单 A1024 的实付金额。")
        self.assertEqual(second["inputs"]["context"]["userId"], "user-001")
        # 不同任务即使引用同一 fixture，也不能共用一份可变业务环境。
        self.assertEqual(service.prepare("follow-up-refund")["environment"]["orders"][0]["paidAmountYuan"], 3500)

    def test_reference_and_catalog_copies_are_independent(self):
        service = DatasetService()
        reference = service.get_reference("order-amount")
        reference["turns"][0]["facts"]["paidAmountYuan"] = 1
        reference["turns"][0]["criteria"][0] = "被修改的要求"
        catalog = service.list(); catalog[0]["id"] = "changed"
        fresh = service.get_reference("order-amount")["turns"][0]
        self.assertEqual(fresh["facts"]["paidAmountYuan"], 3500)
        self.assertIn("当前用户", fresh["criteria"][0])
        self.assertEqual(service.list()[0]["id"], "order-amount")

    def test_injected_raw_objects_do_not_mutate_internal_data(self):
        dataset, fixtures = read_data("dataset.json"), read_data("fixtures.json")
        service = DatasetService(dataset=dataset, fixtures=fixtures)
        dataset["cases"][0]["inputs"]["context"]["userId"] = "changed"
        fixtures[0]["orders"][0]["paidAmountYuan"] = 1
        self.assertEqual(service.prepare("order-amount")["inputs"]["context"]["userId"], "user-001")
        self.assertEqual(service.prepare("order-amount")["environment"]["orders"][0]["paidAmountYuan"], 3500)

    def test_missing_reference_duplicate_ids_and_turn_mismatch(self):
        original = read_data("dataset.json")
        missing = deepcopy(original); del missing["cases"][0]["referenceOutputs"]
        duplicate = deepcopy(original); duplicate["cases"].append(deepcopy(duplicate["cases"][0]))
        mismatch = deepcopy(original); mismatch["cases"][0]["inputs"]["turns"].append("多一轮")
        for value in (missing, duplicate, mismatch):
            with self.subTest(value=value["version"]), self.assertRaises(ValueError):
                validate_dataset(value)

    def test_strict_schema_rejects_unknown_fields_at_every_object_level(self):
        paths = [(), ("cases", 0), ("cases", 0, "inputs"), ("cases", 0, "inputs", "context"),
                 ("cases", 0, "referenceOutputs"), ("cases", 0, "referenceOutputs", "turns", 0)]
        for path in paths:
            dataset = read_data("dataset.json"); target = dataset
            for key in path:
                target = target[key]
            target["unexpected"] = "未知字段"
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "不能缺失或增加"):
                validate_dataset(dataset)

    def test_text_trims_bom_but_keeps_fact_string_content(self):
        original = read_data("dataset.json")
        original["version"] = "\ufeff  after-sales-dataset-v1\n"
        original["cases"][0]["inputs"]["turns"][0] = "  问题\n"
        facts = original["cases"][0]["referenceOutputs"]["turns"][0]["facts"]
        facts["  note  "] = "  不应去掉事实中的空格  "
        cleaned = validate_dataset(original)
        self.assertEqual(cleaned["version"], "after-sales-dataset-v1")
        self.assertEqual(cleaned["cases"][0]["inputs"]["turns"][0], "问题")
        self.assertEqual(cleaned["cases"][0]["referenceOutputs"]["turns"][0]["facts"]["note"], "  不应去掉事实中的空格  ")
        self.assertEqual(original["version"], "\ufeff  after-sales-dataset-v1\n")

    def test_empty_arrays_invalid_text_behavior_and_fact_types(self):
        for field, value in (("behavior", "answer "), ("criteria", []), ("facts", {"bad": None}),
                             ("facts", {"bad": []}), ("facts", {"bad": {}}), ("facts", {"bad": float("nan")}),
                             ("facts", {"bad": float("inf")}), ("facts", {" ": 1})):
            dataset = read_data("dataset.json")
            dataset["cases"][0]["referenceOutputs"]["turns"][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                validate_dataset(dataset)
        for value in (None, {}, {"version": "x", "cases": []}, {"version": " ", "cases": read_data("dataset.json")["cases"]}):
            with self.assertRaises(ValueError):
                validate_dataset(value)

    def test_duplicate_fixture_missing_fixture_and_invalid_amount(self):
        fixtures = read_data("fixtures.json")
        with self.assertRaisesRegex(ValueError, "测试资料 ID 重复"):
            DatasetService(fixtures=[*fixtures, deepcopy(fixtures[0])])
        dataset = read_data("dataset.json"); dataset["cases"][0]["fixtureId"] = "not-found"
        with self.assertRaisesRegex(ValueError, "找不到测试资料：not-found"):
            DatasetService(dataset=dataset)
        for amount in (-1, True, "3500", float("inf")):
            fixtures = read_data("fixtures.json"); fixtures[0]["orders"][0]["paidAmountYuan"] = amount
            with self.subTest(amount=amount), self.assertRaises(ValueError):
                validate_fixtures(fixtures)
        # 与原例一致，fixture 可以没有订单；不能把空列表误判为缺失字段。
        fixtures = read_data("fixtures.json"); fixtures[0]["orders"] = []
        self.assertEqual(validate_fixtures(fixtures)[0]["orders"], [])

    def test_business_reference_agrees_with_orders_and_strict_threshold(self):
        service = DatasetService(); run = service.prepare("order-amount")
        context, environment = run["inputs"]["context"], run["environment"]
        order = next(order for order in environment["orders"] if order["orderId"] == "A1024"
                     and order["tenantId"] == context["tenantId"] and order["userId"] == context["userId"])
        self.assertEqual(service.get_reference("order-amount")["turns"][0]["facts"]["paidAmountYuan"], order["paidAmountYuan"])
        for identity in ("refund-at-threshold", "refund-over-threshold"):
            facts = service.get_reference(identity)["turns"][0]["facts"]
            self.assertEqual(facts["requiresManualReview"], facts["refundAmountYuan"] > environment["policy"]["manualReviewAboveYuan"])
            self.assertEqual(facts["policyId"], environment["policy"]["id"])
        follow_up = service.get_reference("follow-up-refund")["turns"][1]["facts"]
        self.assertEqual(follow_up["refundAmountYuan"], order["paidAmountYuan"])
        self.assertEqual(follow_up["requiresManualReview"], order["paidAmountYuan"] > environment["policy"]["manualReviewAboveYuan"])

    def test_unknown_case_and_out_of_scope_filename_rejected(self):
        service = DatasetService()
        for method in (service.prepare, service.get_reference):
            with self.assertRaisesRegex(ValueError, "找不到样本：not-a-case"):
                method("not-a-case")
        with self.assertRaisesRegex(ValueError, "只读取本节"):
            read_data("not-a-data-file.json")

    def test_demo_explains_isolation_without_agent_score(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            main()
        text = output.getvalue()
        for expected in ("结构检查通过：7 条样本", "运行参数是否包含 referenceOutputs： false",
                         "修改第一份副本以后： 1", "重新准备后，A1024 实付金额： 3500",
                         "重新准备后的消息数量： 0", "重新准备后的记忆数量： 0", "没有计算 Agent 得分"):
            self.assertIn(expected, text)

    def test_cli_uses_source_location_not_current_directory(self):
        with tempfile.TemporaryDirectory() as root:
            result = subprocess.run([sys.executable, str(Path(__file__).resolve().parent / "main.py")],
                                    cwd=root, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                                    capture_output=True, text=True, check=True)
        self.assertIn("结构检查通过：7 条样本", result.stdout)
        self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
