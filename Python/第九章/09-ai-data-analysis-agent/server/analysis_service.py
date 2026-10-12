"""固定会话的数据版本与追问条件；只持久化完成的报告，不恢复中途计算。"""

import asyncio

from .common import new_id, now, utf16_slice
from .report import report_html
from .workflow import INITIAL_STATE, create_analysis_graph


class AnalysisService:
    def __init__(self, storage, *, provider=None, execute=None):
        self.storage, self.provider, self.execute = storage, provider, execute
        self.busy = {}

    def list_sessions(self):
        directory = self.storage.directory / "sessions"
        sessions = [self.storage.session(path.stem) for path in directory.glob("*.json")] if directory.exists() else []
        return sorted(sessions, key=lambda session: session["createdAt"], reverse=True)

    def create_session(self, dataset_id):
        dataset = self.storage.dataset(dataset_id)
        if dataset["status"] != "ready":
            raise ValueError("数据存在质量问题，请修改原表后重新上传")
        session = {"id": new_id(), "datasetId": dataset_id, "title": dataset["name"], "createdAt": now(), "reports": [], "lastSpec": None}
        self.storage.save_session(session)
        return session

    async def analyze(self, session_id, question, mode, signal, emit):
        previous = self.busy.get(session_id)
        if previous is not None and not previous.aborted:
            raise ValueError("本会话还有任务在运行，请等停止完成后再提问")
        self.busy[session_id] = signal
        try:
            session = await asyncio.to_thread(self.storage.session, session_id)
            dataset = await asyncio.to_thread(self.storage.dataset, session["datasetId"])
            if dataset["status"] != "ready":
                raise ValueError("当前数据不允许查询")
            graph = create_analysis_graph(question=question, dataset=dataset, session=session, mode=mode,
                                          directory=self.storage.dataset_dir(dataset["id"]), signal=signal, emit=emit,
                                          provider=self.provider, execute=self.execute)
            state = await graph.ainvoke(INITIAL_STATE.copy(), {"recursion_limit": 12})
            signal.throw_if_aborted()
            report = state["report"]
            session["reports"].append(report)
            if report["evidence"]:
                session["lastSpec"] = report["evidence"]["spec"]
            session["title"] = utf16_slice(session["reports"][0]["question"], 32)
            # 本地小文件在本事件循环原子发布，取消不能与异步线程保存完成发生竞态。
            self.storage.save_session(session)
            return report
        finally:
            if self.busy.get(session_id) is signal:
                self.busy.pop(session_id, None)

    def find_report(self, session_id, report_id):
        report = next((report for report in self.storage.session(session_id)["reports"] if report["id"] == report_id), None)
        if report is None:
            raise ValueError("报告不存在")
        return report

    def export(self, session_id, report_id):
        report = self.find_report(session_id, report_id)
        html = report_html(report)
        directory = self.storage.directory / "reports"
        self.storage.save(directory / f"{report['id']}.json", report)
        (directory / f"{report['id']}.html").write_text(html, encoding="utf-8")
        return html
