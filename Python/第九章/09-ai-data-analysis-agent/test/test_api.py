"""HTTP 入口与真实本机 NDJSON/WS 测试，只运行测试自己创建的临时服务。"""

import asyncio
import json
import socket
import tempfile
import unittest

import httpx2
import uvicorn
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from server.common import ROOT
from server.main import create_app
from server.multimodal_service import MultimodalService
from server.storage import Storage
from server.voice.voice_gateway import VoiceGateway
from test_project import MAPPING, local_query


class FakeSpeech(MultimodalService):
    def __init__(self, storage):
        super().__init__(storage, {}, execute=local_query)
        self.sentences = []
    async def speak(self, text, signal):
        signal.throw_if_aborted()
        self.sentences.append(text)
        return "https://example.test/test-only.wav"


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.storage = Storage(self.root.name)
        self.multimodal = FakeSpeech(self.storage)
        self.app = create_app(storage=self.storage, env={}, execute=local_query, multimodal=self.multimodal)
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.root.cleanup()

    def imported(self, filename="sales-demo.xlsx"):
        response = self.client.post("/api/uploads", files={"file": (filename, (ROOT / "samples" / filename).read_bytes())})
        self.assertEqual(response.status_code, 201, response.text)
        upload = response.json()
        response = self.client.post("/api/datasets", json={"uploadId": upload["id"], "sheet": "销售明细", "mapping": MAPPING})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["dataset"]

    def session(self):
        dataset = self.imported()
        response = self.client.post("/api/sessions", json={"datasetId": dataset["id"]})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def analyze(self, session, question):
        response = self.client.post(f"/api/sessions/{session['id']}/analyze", json={"question": question, "mode": "replay"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("application/x-ndjson", response.headers["content-type"])
        packets = [json.loads(line) for line in response.text.splitlines()]
        self.assertEqual([p["data"]["stage"] for p in packets if p["event"] == "progress"], ["decide", "query", "report"])
        self.assertEqual(packets[-1]["event"], "report", packets)
        return packets[-1]["data"]

    def test_upload_import_followup_rows_history_html_and_csv(self):
        self.assertEqual(self.client.get("/api/health").json(), {"ok": True})
        self.assertEqual(self.client.get("/api/meta").json(), {"ai": False, "speech": False, "vision": False, "model": "deepseek-v4-flash"})
        session = self.session()
        first = self.analyze(session, "哪些区域连续两个月净销售额下降？")
        second = self.analyze(session, "只看华东，哪些商品贡献了主要降幅？")
        self.assertIn("华东连续两个月下降", first["answer"])
        self.assertEqual([r["净降幅贡献(%)"] for r in second["table"]], [80, 20])
        self.assertEqual(len(self.client.get(f"/api/datasets/{session['datasetId']}/rows").json()), 18)
        restored = self.client.get(f"/api/sessions/{session['id']}").json()
        self.assertEqual(len(restored["reports"]), 2); self.assertEqual(restored["lastSpec"]["region"], "华东")
        export = self.client.get(f"/api/sessions/{session['id']}/reports/{second['id']}/export")
        self.assertEqual(export.status_code, 200); self.assertIn("<svg", export.text)
        self.assertIn("attachment", export.headers["content-disposition"])
        csv = self.client.get(f"/api/sessions/{session['id']}/reports/{second['id']}/csv")
        self.assertTrue(csv.content.startswith(b"\xef\xbb\xbf")); self.assertIn("净降幅贡献", csv.text)

    def test_picture_replay_compare_and_samples(self):
        dataset = self.imported()
        image = self.client.get("/api/samples/dashboard.png")
        self.assertEqual(image.content, (ROOT / "samples/dashboard.png").read_bytes())
        facts = self.client.post("/api/images", files={"file": ("dashboard.png", image.content, "image/png")}, data={"mode": "replay"})
        self.assertEqual(facts.status_code, 201, facts.text)
        comparison = self.client.post("/api/images/compare", json={"datasetId": dataset["id"], "facts": facts.json()})
        self.assertEqual(comparison.json()["difference"], 0, comparison.text)
        self.assertEqual(comparison.json()["total"], 60000)

    def test_bad_data_and_inputs_return_message_and_do_not_query(self):
        dataset = self.imported("sales-issues.xlsx")
        response = self.client.post("/api/sessions", json={"datasetId": dataset["id"]})
        self.assertEqual(response.status_code, 400); self.assertIn("质量问题", response.json()["message"])
        for path in ("/api/samples/not-allowed", "/api/datasets/invalid/rows", "/api/sessions/invalid"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 400); self.assertIn("message", response.json())
        session = self.session()
        for question, mode in ((" ", "replay"), ("😀" * 1001, "replay"), ("问题", "other")):
            response = self.client.post(f"/api/sessions/{session['id']}/analyze", json={"question": question, "mode": mode})
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self.storage.session(session["id"])["reports"], [])

    def test_speech_sentence_limit_and_report_ownership(self):
        session = self.session()
        report = self.analyze(session, "9月全部区域净销售额是多少？")
        stored = self.storage.session(session["id"])
        stored["reports"][0]["answer"] = "😀" * 510 + "。" + "后续句。" * 10
        self.storage.save_session(stored)
        response = self.client.post(f"/api/sessions/{session['id']}/reports/{report['id']}/speech", json={})
        packets = [json.loads(line) for line in response.text.splitlines()]
        self.assertEqual([p["data"]["seq"] for p in packets if p["event"] == "audio"], list(range(8)))
        self.assertEqual(len(self.multimodal.sentences[0]), 500)
        self.assertEqual(packets[-1]["event"], "done")
        other = self.client.post("/api/sessions", json={"datasetId": session["datasetId"]}).json()
        response = self.client.get(f"/api/sessions/{other['id']}/reports/{report['id']}/export")
        self.assertEqual(response.status_code, 400); self.assertIn("报告不存在", response.json()["message"])

    def test_origin_rejects_other_browser_pages(self):
        for origin in (None, "http://evil.test", "http://localhost:5186"):
            headers = {"origin": origin} if origin else {}
            with self.assertRaises(WebSocketDisconnect):
                with self.client.websocket_connect("/voice", headers=headers):
                    self.fail("不应接受来源")


class LiveCancelTests(unittest.IsolatedAsyncioTestCase):
    async def test_client_disconnect_cancels_graph_and_keeps_session_unchanged(self):
        entered, closed = asyncio.Event(), asyncio.Event()
        async def paused_query(path, sql, signal):
            try:
                entered.set()
                await signal.run(asyncio.Event().wait())
            finally:
                closed.set()
        with tempfile.TemporaryDirectory() as root:
            storage = Storage(root)
            app = create_app(storage=storage, env={}, execute=paused_query)
            uploaded = app.state.datasets.upload((ROOT / "samples/sales-demo.xlsx").read_bytes(), "sales-demo.xlsx")
            dataset = app.state.datasets.import_data(uploaded["id"], "销售明细", MAPPING)["dataset"]
            session = app.state.analyses.create_session(dataset["id"])
            listener = socket.socket(); listener.bind(("127.0.0.1", 0)); listener.listen()
            port = listener.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(app, log_level="critical", lifespan="on", ws="websockets-sansio", timeout_graceful_shutdown=2))
            running = asyncio.create_task(server.serve(sockets=[listener]))
            try:
                async with asyncio.timeout(2):
                    while not server.started:
                        await asyncio.sleep(0.005)
                async with httpx2.AsyncClient(trust_env=False) as client:
                    async with client.stream("POST", f"http://127.0.0.1:{port}/api/sessions/{session['id']}/analyze",
                                             json={"question": "哪些区域连续下降？", "mode": "replay"}) as response:
                        async for line in response.aiter_lines():
                            if json.loads(line)["data"].get("stage") == "query":
                                break
                    await asyncio.wait_for(entered.wait(), 1)
                    await asyncio.wait_for(closed.wait(), 2)
                self.assertEqual(storage.session(session["id"])["reports"], [])
                async with asyncio.timeout(1):
                    while app.state.analyses.busy:
                        await asyncio.sleep(0)
                self.assertEqual(app.state.analyses.busy, {})
            finally:
                server.should_exit = True
                await asyncio.wait_for(running, 3)
                listener.close()


class RecognitionGatewayTests(unittest.TestCase):
    def test_recording_events_turn_isolation_final_and_disconnect(self):
        recognitions = []
        class FakeRecognition:
            def __init__(self, signal, emit):
                self.signal, self.emit, self.closed = signal, emit, False
                self.audio, self.finished = [], asyncio.Event()
                emit("asr.ready", {})
                self.done = asyncio.create_task(self.run())
                recognitions.append(self)
            async def run(self):
                try:
                    await self.signal.run(self.finished.wait())
                finally:
                    self.closed = True
            def append(self, audio):
                self.audio.append(audio)
                self.emit("asr.partial", {"text": "华东销售额"})
            def finish(self):
                self.emit("asr.final", {"text": "华东销售额"})
                self.finished.set()
        gateway = VoiceGateway(FakeRecognition)
        with tempfile.TemporaryDirectory() as root, TestClient(create_app(storage=Storage(root), env={}, gateway=gateway)) as client:
            with client.websocket_connect("/voice", headers={"origin": "http://localhost:5187"}) as ws:
                ws.send_json({"event": "listen", "data": {"turnId": "first"}})
                self.assertEqual(ws.receive_json()["event"], "asr.ready")
                ws.send_json({"event": "audio", "data": {"turnId": "wrong", "audio": "ignore"}})
                ws.send_json({"event": "audio", "data": {"turnId": "first", "audio": "AAAA"}})
                self.assertEqual(ws.receive_json()["data"], {"text": "华东销售额", "turnId": "first"})
                ws.send_json({"event": "finish", "data": {"turnId": "first"}})
                self.assertEqual(ws.receive_json()["event"], "asr.final")
                ws.send_json({"event": "listen", "data": {"turnId": "second"}})
                self.assertEqual(ws.receive_json()["data"]["turnId"], "second")
            self.assertEqual(recognitions[0].audio, ["AAAA"])
        self.assertTrue(all(item.closed for item in recognitions)); self.assertEqual(gateway.active, {})


if __name__ == "__main__":
    unittest.main()
