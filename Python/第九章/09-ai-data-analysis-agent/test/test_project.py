"""真实样本、数据库、会话和 LangGraph 的离线验证；不是 Docker 隔离验证。"""

import asyncio
import copy
import hashlib
import io
import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

from server.analysis import amount, build_report, build_sql, month_sequence, replay_decision
from server.analysis_service import AnalysisService
from server.common import ROOT, new_id
from server.dataset_service import DatasetService
from server.model import AnalysisProvider
from server.report import chart_option, report_html, result_csv
from server.signal import CancelSignal, TurnCancelled
from server.storage import Storage, checked_id
from server.table_parser import normalize, parse_file, preview_sheet
from server.workflow import INITIAL_STATE, create_analysis_graph
from runtime.query_worker import run_read_only_query

MAPPING = {"date": "销售日期", "region": "区域", "product": "商品名称", "paid": "实付金额（元）", "refund": "退款金额（元）"}
SPEC = {"kind": "decline", "metric": "net", "start": "2026-07-01", "end": "2026-09-30", "region": None}
DATASET = {"id": new_id(), "name": "sales.csv", "sheet": "CSV", "version": 1, "checksum": "abc", "createdAt": "",
           "rowCount": 18, "status": "ready", "issues": [], "start": "2026-07-15", "end": "2026-09-15",
           "regions": ["华东", "华南"], "products": ["咖啡机", "键盘"], "mapping": MAPPING}
SESSION = {"id": new_id(), "datasetId": DATASET["id"], "title": "", "createdAt": "", "reports": [], "lastSpec": None}


def evidence(rows, **override):
    spec = {**SPEC, **override}
    return {"id": new_id(), "dataset": DATASET, "rows": rows, "spec": spec,
            "sql": build_sql(spec), "createdAt": "", "durationMs": 10}


async def local_query(path, sql, signal):
    signal.throw_if_aborted()
    return run_read_only_query(path, sql)["rows"]


class TableTests(unittest.TestCase):
    def test_samples_all_sheets_and_csv(self):
        for filename in ("sales-demo.xlsx", "sales-demo.csv"):
            sheets = parse_file((ROOT / "samples" / filename).read_bytes(), filename)
            sheet = sheets[0]
            preview = preview_sheet(sheet)
            self.assertEqual(preview["rowCount"], 18)
            self.assertEqual(len(preview["rows"]), 4)
            result = normalize(sheet, preview["suggested"])
            self.assertEqual(len(result["rows"]), 18)
            self.assertEqual(result["issues"], [])
            if filename.endswith("xlsx"):
                self.assertEqual(preview_sheet(sheets[1])["rowCount"], 2)

    def test_blank_rows_zero_and_source_row_numbers(self):
        sheet = {"name": "销售", "cells": [list(MAPPING.values()), [None, "", None],
                 ["2026-09-15", "华东", "咖啡机", 0, 0], [], ["2026-09-16", "华南", "键盘", 100, 0]]}
        self.assertEqual(preview_sheet(sheet)["rowCount"], 2)
        self.assertEqual([row["source_row"] for row in normalize(sheet, MAPPING)["rows"]], [3, 5])

    def test_quality_errors_keep_every_row(self):
        good = ["2026-09-15", "华东", "咖啡机", 100, 0]
        cells = [list(MAPPING.values()), good, good, ["2026-09-31", "华东", "咖啡机", 100, 200],
                 ["2026-09-15", "华南", "咖啡机", {"formula": "1+1", "result": 2}, ""]]
        result = normalize({"name": "销售", "cells": cells}, MAPPING)
        for message in ("完全相同", "日期", "超过实付", "公式", "必填"):
            self.assertTrue(any(message in issue["message"] for issue in result["issues"]))
        self.assertEqual(len(result["rows"]), 4)

    def test_input_and_mapping_limits(self):
        for data, name in ((b"", "a.csv"), (b"x" * (5 * 1024 * 1024 + 1), "a.csv"), (b"\xff", "a.csv"), (b"x", "a.xls"), (b"a,b\n1\n", "a.csv")):
            with self.assertRaises((ValueError, UnicodeError)):
                parse_file(data, name)
        with self.assertRaises(ValueError):
            preview_sheet({"name": "销售", "cells": [["重复", "重复"]]})
        with self.assertRaisesRegex(ValueError, "五个不同"):
            normalize({"name": "销售", "cells": [list(MAPPING.values())]}, {**MAPPING, "refund": MAPPING["paid"]})

    def test_xlsx_formula_and_merge_rejected(self):
        book = Workbook(); sheet = book.active
        sheet.append(list(MAPPING.values())); sheet.append(["2026-09-15", "华东", "咖啡机", "=1+1", 0])
        data = io.BytesIO(); book.save(data)
        parsed = parse_file(data.getvalue(), "formula.xlsx")[0]
        self.assertTrue(any("公式" in issue["message"] for issue in normalize(parsed, MAPPING)["issues"]))
        sheet.merge_cells("A1:B1"); data = io.BytesIO(); book.save(data)
        with self.assertRaisesRegex(ValueError, "合并"):
            parse_file(data.getvalue(), "merge.xlsx")
        book.close()


class CalculationTests(unittest.TestCase):
    def test_decline_gaps_and_three_months(self):
        rows = [{"month": month, "region": "华东", "amount": value} for month, value in
                (("2026-07", 30000), ("2026-08", 25000), ("2026-09", 20000))]
        report = build_report("下降", "replay", evidence(rows))
        self.assertIn("华东连续两个月下降", report["answer"])
        gap = build_report("下降", "replay", evidence([rows[0], rows[2]]))
        self.assertEqual(gap["chart"]["series"][0]["values"], [30000, None, 20000])
        self.assertIn("没有数据完整", gap["answer"])

    def test_contribution_and_missing_zero_drop_negative_contribution(self):
        rows = [{"month": month, "product": product, "amount": value} for month, product, value in
                (("2026-08", "咖啡机", 14000), ("2026-09", "咖啡机", 10000), ("2026-08", "键盘", 11000), ("2026-09", "键盘", 10000))]
        report = build_report("商品", "replay", evidence(rows, kind="contribution", region="华东"))
        self.assertEqual([row["净降幅贡献(%)"] for row in report["table"]], [80, 20])
        self.assertIn("不能", build_report("商品", "replay", evidence(rows[:-1], kind="contribution", region="华东"))["notes"][1])
        self.assertIn("缺少", build_report("商品", "replay", evidence(rows[:-1], kind="contribution", region="华东"))["answer"])
        rows[1]["amount"] = 15000
        report = build_report("商品", "replay", evidence(rows, kind="contribution", region="华东"))
        self.assertTrue(all(row["净降幅贡献(%)"] is None for row in report["table"]))

    def test_replay_inheritance_metric_time_and_unknown(self):
        decision = replay_decision("只看华东，哪些商品贡献了主要降幅？", DATASET, SPEC)
        self.assertEqual(decision["spec"], {**SPEC, "kind": "contribution", "region": "华东"})
        self.assertEqual(replay_decision("9月全部区域实付金额是多少？", DATASET, SPEC)["spec"]["metric"], "paid")
        self.assertEqual(replay_decision("为什么用户不买咖啡机？", DATASET, SPEC)["route"], "insufficient")
        self.assertEqual(replay_decision("你好", DATASET, SPEC)["route"], "clarify")
        with self.assertRaisesRegex(ValueError, "月份无效"):
            replay_decision("13月销售", DATASET, SPEC)

    def test_money_month_boundaries_empty_and_report_security(self):
        self.assertEqual(month_sequence("2025-12-01", "2026-02-28"), ["2025-12", "2026-01", "2026-02"])
        with self.assertRaisesRegex(ValueError, "120"):
            month_sequence("2000-01-01", "2010-01-31")
        for value in (None, float("inf"), "bad"):
            with self.assertRaisesRegex(ValueError, "无效"):
                amount(value)
        report = build_report("<script>alert(1)</script>", "replay", evidence([{"region": "华东", "amount": 20000}], kind="summary"))
        exported = report_html(report)
        self.assertIn("<svg", exported); self.assertIn("&lt;script&gt;", exported)
        self.assertNotIn("<script>alert", exported); self.assertIn("abc", exported)
        self.assertFalse(chart_option(report["chart"])["series"][0]["connectNulls"])
        self.assertIn("'=1+1", result_csv([{"name": "=1+1"}]))
        self.assertIn("不代表", build_report("空", "replay", evidence([]))["answer"])


class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_retry_only_once_and_preserves_error(self):
        calls = []
        async def fail(*args):
            calls.append(args)
            raise RuntimeError("数据库不可用")
        graph = create_analysis_graph(question="哪些区域连续下降？", dataset=DATASET, session=SESSION, mode="replay",
                                      directory="/unused", signal=CancelSignal(), emit=lambda _: None, execute=fail)
        state = await graph.ainvoke(INITIAL_STATE.copy())
        self.assertEqual(len(calls), 2)
        self.assertEqual(state["report"]["status"], "insufficient")
        self.assertIn("数据库不可用", state["report"]["answer"])

    async def test_ai_metric_verification_and_repair(self):
        correct = build_sql({**SPEC, "kind": "summary"}); wrong = correct.replace("paid_amount - refund_amount", "paid_amount")
        calls, errors = [], []
        class Provider:
            async def decide(self, *args):
                errors.append(args[-1])
                return {"route": "query", "spec": {**SPEC, "kind": "summary"}, "sql": wrong if len(errors) == 1 else correct, "message": "查询"}
        async def execute(path, sql, signal):
            calls.append(sql)
            return [{"region": "华东", "amount": 21000 if sql == wrong else 20000}]
        graph = create_analysis_graph(question="净销售额", dataset=DATASET, session=SESSION, mode="ai", directory="/unused",
                                      signal=CancelSignal(), emit=lambda _: None, provider=Provider(), execute=execute)
        state = await graph.ainvoke(INITIAL_STATE.copy())
        self.assertIn("口径不一致", errors[1]); self.assertEqual(calls, [wrong, correct, correct])
        self.assertEqual(state["report"]["evidence"]["sql"], correct)

    async def test_cancel_never_creates_success_report(self):
        signal = CancelSignal()
        async def execute(*args):
            signal.abort("取消")
            return []
        graph = create_analysis_graph(question="按月趋势", dataset=DATASET, session=SESSION, mode="replay", directory="/unused",
                                      signal=signal, emit=lambda _: None, execute=execute)
        with self.assertRaisesRegex(TurnCancelled, "取消"):
            await graph.ainvoke(INITIAL_STATE.copy())


class PersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_version_changes_and_session_stays_on_original_dataset(self):
        with tempfile.TemporaryDirectory() as root:
            storage = Storage(root); datasets = DatasetService(storage)
            data = (ROOT / "samples/sales-demo.csv").read_bytes()
            first_upload = datasets.upload(data, "sales-demo.csv")
            first = datasets.import_data(first_upload["id"], "CSV", MAPPING)["dataset"]
            analyses = AnalysisService(storage, provider=AnalysisProvider({}), execute=local_query)
            session = analyses.create_session(first["id"])
            second_upload = datasets.upload(data.replace(b"11000,1000", b"12000,1000"), "sales-demo.csv")
            second = datasets.import_data(second_upload["id"], "CSV", MAPPING)["dataset"]
            self.assertEqual(second["version"], 2); self.assertNotEqual(first["checksum"], second["checksum"])
            self.assertEqual(storage.session(session["id"])["datasetId"], first["id"])
            report = await analyses.analyze(session["id"], "9月全部区域净销售额是多少？", "replay", CancelSignal(), lambda _: None)
            self.assertIn("59,000.00", report["answer"])

    async def test_session_busy_until_cleanup_cancel_does_not_persist(self):
        with tempfile.TemporaryDirectory() as root:
            storage = Storage(root); datasets = DatasetService(storage)
            uploaded = datasets.upload((ROOT / "samples/sales-demo.xlsx").read_bytes(), "sales-demo.xlsx")
            dataset = datasets.import_data(uploaded["id"], "销售明细", MAPPING)["dataset"]
            entered, released = asyncio.Event(), asyncio.Event()
            async def delayed(path, sql, signal):
                entered.set()
                await released.wait()
                signal.throw_if_aborted()
                return []
            analyses = AnalysisService(storage, provider=AnalysisProvider({}), execute=delayed)
            session = analyses.create_session(dataset["id"]); signal = CancelSignal()
            task = asyncio.create_task(analyses.analyze(session["id"], "按月趋势", "replay", signal, lambda _: None))
            await entered.wait()
            try:
                with self.assertRaisesRegex(ValueError, "还有任务"):
                    await analyses.analyze(session["id"], "按月趋势", "replay", CancelSignal(), lambda _: None)
                signal.abort("用户停止")
            finally:
                released.set()
                with self.assertRaises(TurnCancelled):
                    await task
            self.assertEqual(analyses.busy, {}); self.assertEqual(storage.session(session["id"])["reports"], [])

    async def test_end_to_end_import_followup_restore_export_and_versions(self):
        with tempfile.TemporaryDirectory() as root:
            storage = Storage(root); datasets = DatasetService(storage)
            uploaded = datasets.upload((ROOT / "samples/sales-demo.xlsx").read_bytes(), "sales-demo.xlsx")
            result = datasets.import_data(uploaded["id"], "销售明细", MAPPING)
            dataset = result["dataset"]
            self.assertEqual(dataset["rowCount"], 18); self.assertEqual(dataset["status"], "ready")
            self.assertTrue(datasets.import_data(uploaded["id"], "销售明细", MAPPING)["repeated"])
            analyses = AnalysisService(storage, provider=AnalysisProvider({}), execute=local_query)
            session = analyses.create_session(dataset["id"])
            first = await analyses.analyze(session["id"], "哪些区域连续两个月净销售额下降？", "replay", CancelSignal(), lambda _: None)
            self.assertIn("华东连续两个月下降", first["answer"])
            second = await analyses.analyze(session["id"], "只看华东，哪些商品贡献了主要降幅？", "replay", CancelSignal(), lambda _: None)
            self.assertEqual([row["净降幅贡献(%)"] for row in second["table"]], [80, 20])
            third = await analyses.analyze(session["id"], "9月全部区域净销售额是多少？", "replay", CancelSignal(), lambda _: None)
            self.assertIn("59,000.00", third["answer"])
            recovered = Storage(root).session(session["id"])
            self.assertEqual(len(recovered["reports"]), 3)
            self.assertEqual(recovered["lastSpec"]["metric"], "net")
            self.assertIn("<svg", analyses.export(session["id"], second["id"]))
            before = hashlib.sha256((storage.dataset_dir(dataset["id"]) / "data.duckdb").read_bytes()).digest()
            with self.assertRaisesRegex(ValueError, "POLICY"):
                run_read_only_query(storage.dataset_dir(dataset["id"]) / "data.duckdb", "DELETE FROM sales")
            self.assertEqual(hashlib.sha256((storage.dataset_dir(dataset["id"]) / "data.duckdb").read_bytes()).digest(), before)

    async def test_bad_dataset_blocks_query_and_ids(self):
        with tempfile.TemporaryDirectory() as root:
            storage = Storage(root); datasets = DatasetService(storage)
            uploaded = datasets.upload((ROOT / "samples/sales-issues.xlsx").read_bytes(), "sales-issues.xlsx")
            dataset = datasets.import_data(uploaded["id"], "销售明细", MAPPING)["dataset"]
            self.assertEqual(dataset["status"], "needs_review")
            self.assertFalse((storage.dataset_dir(dataset["id"]) / "data.duckdb").exists())
            with self.assertRaisesRegex(ValueError, "质量问题"):
                AnalysisService(storage).create_session(dataset["id"])
            for value in ("../", "x" * 36, None):
                with self.assertRaises(ValueError):
                    checked_id(value)


if __name__ == "__main__":
    unittest.main()
