"""完整真实 Docker 验证；使用临时数据、固定 Replay 计划，不调用收费供应商。"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path

# 只影响本验证进程，不读取环境文件，也不向追踪服务上传临时样本。
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from server.analysis_service import AnalysisService  # noqa: E402
from server.common import ROOT  # noqa: E402
from server.dataset_service import DatasetService  # noqa: E402
from server.model import AnalysisProvider  # noqa: E402
from server.multimodal_service import MultimodalService  # noqa: E402
from server.sandbox import IMAGE, docker, execute_query  # noqa: E402
from server.signal import CancelSignal  # noqa: E402
from server.storage import Storage  # noqa: E402


async def main():
    # 缺镜像时明确失败，不让 Docker 自动尝试拉取同名远端镜像。
    await docker(["image", "inspect", IMAGE])
    with tempfile.TemporaryDirectory(prefix="analysis-verify-") as root:
        storage = Storage(root); datasets = DatasetService(storage)
        uploaded = datasets.upload((ROOT / "samples/sales-demo.xlsx").read_bytes(), "sales-demo.xlsx")
        mapping = next(sheet["suggested"] for sheet in uploaded["sheets"] if sheet["name"] == "销售明细")
        imported = datasets.import_data(uploaded["id"], "销售明细", mapping)
        dataset = imported["dataset"]
        assert dataset["rowCount"] == 18 and dataset["status"] == "ready"
        assert datasets.import_data(uploaded["id"], "销售明细", mapping)["repeated"]
        print("✓ Excel 导入 18 行，重复内容复用原版本")
        analyses = AnalysisService(storage, provider=AnalysisProvider({}))
        session = analyses.create_session(dataset["id"])
        async def analyze(question):
            return await analyses.analyze(session["id"], question, "replay", CancelSignal(), lambda _: None)
        first = await analyze("哪些区域连续两个月净销售额下降？")
        assert "华东连续两个月下降" in first["answer"] and "华南连续" not in first["answer"], first["answer"]
        print("✓ 实际容器查询：华东 30000 → 25000 → 20000")
        second = await analyze("只看华东，哪些商品贡献了主要降幅？")
        assert [row["净降幅贡献(%)"] for row in second["table"]] == [80, 20]
        assert len(Storage(root).session(session["id"])["reports"]) == 2
        assert "<svg" in analyses.export(session["id"], second["id"])
        print("✓ 追问继承条件，贡献 4000 元 / 80%、1000 元 / 20%，历史与离线报告可恢复")
        multimodal = MultimodalService(storage, {})
        facts = await multimodal.inspect((ROOT / "samples/dashboard.png").read_bytes(), "image/png", "replay", CancelSignal())
        comparison = await multimodal.compare(dataset["id"], facts, CancelSignal())
        assert comparison["total"] == 60000 and comparison["difference"] == 0
        print("✓ 截图实付 60000 元与原表同口径一致")
        bad = datasets.upload((ROOT / "samples/sales-issues.xlsx").read_bytes(), "sales-issues.xlsx")
        bad_dataset = datasets.import_data(bad["id"], "销售明细", bad["sheets"][0]["suggested"])["dataset"]
        assert bad_dataset["status"] == "needs_review"
        try:
            analyses.create_session(bad_dataset["id"])
        except ValueError:
            pass
        else:
            raise AssertionError("质量问题未阻止会话创建")
        for sql in ("DELETE FROM sales", "SELECT * FROM read_csv('/etc/passwd')"):
            try:
                await execute_query(storage.dataset_dir(dataset["id"]) / "data.duckdb", sql, CancelSignal())
            except Exception as error:
                assert "POLICY" in str(error), error
            else:
                raise AssertionError("越权 SQL 未被拒绝")
        print("✓ 质量问题与越界 SQL 被拒绝；全部真实 Docker 项目验证通过。")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as error:
        print(f"项目验证失败：{error}", file=sys.stderr)
        sys.exit(1)
